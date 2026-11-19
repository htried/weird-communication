#!/usr/bin/env python3
"""Local web UI for blind manual labeling of trials (plan 2026-11-19, Part C). Stdlib only.

    python3 analysis/review_app.py artifacts/validation/sample-2026-11-19.json --rater hal
    python3 analysis/review_app.py artifacts/validation/sample-2026-11-19.json --rater labmate --overlap-only
    # then open http://127.0.0.1:8765/

Replaces the terminal flow of `validate.py label` and writes the same label records to
<labels-dir>/labels-<rater>.jsonl, so `validate.py report` and `validate.py label` keep working
(the two share validate.read_labels / done_trials).

Privacy: the server binds to 127.0.0.1 only, rejects requests whose Host/Origin is not this
server, and the UI loads nothing from the network (a Content-Security-Policy enforces 'self').
Transcripts and room video never leave the machine.

Blindness: GET /api/trial never includes anything from judge.json. The judge's view is returned
only by POST /api/label (after the blind label is written) and by GET /api/reveal/<id> for trials
this rater has already labelled.

Endpoints:
  GET  /                      the app (review_ui/index.html, app.js, style.css)
  GET  /api/state             rater, mode, progress, trial list with done flags
  GET  /api/trial/<id>        metadata, prompts, truth, scorer outcomes, parsed timeline, video URLs
  POST /api/label             blind label -> append record; respond with the judge view + diff (409 if done)
  POST /api/after_reveal      {trial, after_reveal: keep|judge_right|unsure, note}
  GET  /api/reveal/<id>       judge view + diff for an already-labelled trial (403 otherwise)
  GET  /api/report            text of `validate.py report`
  GET  /video/<id>.mp4        raw room clip (Range supported);  /video/captioned/<id>.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import validate as V

UI_DIR = Path(__file__).resolve().parent / "review_ui"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
CLIP_OFFSET_S = 15.0          # clip time = agent time + 15 s (env3 recorder starts before the agents)
SAMPLE_FIELDS = ("trial", "model", "env", "task", "rung", "hatch", "phrasing", "flags", "overlap",
                 "incl_prob", "stratum")
AFTER_REVEAL = ("keep", "judge_right", "unsure")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
TRIAL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VIDEO_RE = re.compile(r"^(captioned/)?([A-Za-z0-9._-]+)\.mp4$")


# --- trace parsing ----------------------------------------------------------------------------

TL_RE = re.compile(r"^\[\s*(-?\d+(?:\.\d+)?)s\] (\S+): (.*)$")
KINDS = [  # (prefix regex on the event text, kind)
    (re.compile(r"^SAYS: "), "SAYS"),
    (re.compile(r"^CALL ([^:]*): "), "CALL"),
    (re.compile(r"^RESULT: "), "RESULT"),
    (re.compile(r"^USER \(scripted reply\): "), "USER"),
    (re.compile(r"^GOAL EVALUATOR \(automated\): "), "GOAL"),
    (re.compile(r"^HARNESS-ERROR: "), "HARNESS"),
    (re.compile(r"^HARNESS NOTE: "), "HARNESS"),
    (re.compile(r"^PROXY: "), "PROXY"),
]
KIND_NAMES = ("SAYS", "CALL", "RESULT", "USER", "GOAL", "HARNESS", "PROXY", "OTHER")
HEADER_HEADS = [("TRIAL ", "trial"), ("ENVIRONMENT:", "environment"), ("TASK:", "task"),
                ("GROUND TRUTH:", "ground_truth"), ("PROMPTS", "prompts"), ("TRACE CONDENSED", "condensed")]
FOOTER_HEADS = [("FINAL ANSWERS:", "final_answers"), ("NETWORK SUMMARY", "network_summary"),
                ("HARNESS NOTES", "harness_notes")]


def _blocks(text: str, heads: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        for prefix, name in heads:
            if line.startswith(prefix):
                cur = name
                out.setdefault(cur, [])
                break
        if cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def classify(body: str) -> tuple[str, str, str]:
    """-> (kind, label shown in the trace, text without the label)."""
    for rx, kind in KINDS:
        m = rx.match(body)
        if m:
            return kind, body[:m.end() - 2], body[m.end():]
    return "OTHER", "", body


def parse_trace(text: str) -> dict:
    """Split trace.txt (merge_trace.build) into header/footer sections and timeline events."""
    ti = text.find("\nTIMELINE (")
    if ti < 0:
        return {"sections": {"raw": text}, "events": []}
    header = text[:ti]
    body_start = text.find("\n", ti + 1)
    fi = text.rfind("\n\nFINAL ANSWERS:")
    if fi < body_start:
        fi = len(text)
    footer = text[fi:]
    events: list[dict] = []
    for line in text[body_start + 1:fi].split("\n"):
        m = TL_RE.match(line)
        if m:
            kind, label, rest = classify(m.group(3))
            ev = {"i": len(events), "t": float(m.group(1)), "node": m.group(2), "kind": kind, "label": label,
                  "text": rest}
            if kind == "CALL":
                ev["tool"] = label[5:]
            events.append(ev)
        elif events:
            events[-1]["text"] += "\n" + line
    for ev in events:
        ev["text"] = ev["text"].rstrip("\n")
    sections = _blocks(header, HEADER_HEADS)
    sections.update(_blocks(footer, FOOTER_HEADS))
    return {"sections": sections, "events": events}


# --- judge view (only ever sent after a blind label exists) -----------------------------------

LETTER_OF = {sch: k for k, sch in V.LABELS.items()}


def judge_detail(tdir: Path) -> dict | None:
    p = tdir / "judge.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())["measurement"]
    jv = V.judge_view(tdir)
    labels = []
    for l in m.get("labels", []):
        labels.append({"taxonomy_id": l.get("taxonomy_id"), "taxonomy_name": l.get("taxonomy_name"),
                       "letter": LETTER_OF.get(l.get("taxonomy_id")), "agent": l.get("agent"),
                       "lifecycle_stage": l.get("lifecycle_stage"), "channel": l.get("channel"),
                       "confidence": l.get("confidence"), "rationale": l.get("rationale", ""),
                       "evidence": [{"quote": e.get("quote", ""), "location": e.get("location", ""),
                                     "why_it_matters": e.get("why_it_matters", "")}
                                    for e in l.get("evidence", [])]})
    agents = {a.get("agent"): a for a in m.get("agents", [])}
    return {"agents": jv["agents"], "mechanism": jv["mechanism"],
            "needs_human_adjudication": jv["adjudicate"], "adjudication_reason": jv["reason"],
            "trial_summary": jv["summary"],
            "agent_summaries": {r: {k: agents.get(r, {}).get(k) for k in
                                    ("summary", "final_answer_status", "obtained_peer_value", "mechanism",
                                     "disclosed_method")} for r in V.ROLES},
            "labels": labels}


def diff_view(rec: dict, jd: dict | None) -> dict | None:
    if jd is None:
        return None
    agents = {}
    for r in V.ROLES:
        h, j = set(rec["agents"].get(r, "")), set(jd["agents"].get(r, ""))
        agents[r] = {"human": "".join(sorted(h)), "judge": "".join(sorted(j)), "same": h == j,
                     "only_human": "".join(sorted(h - j)), "only_judge": "".join(sorted(j - h))}
    mech = {"human": rec["mechanism"], "judge": jd["mechanism"], "same": rec["mechanism"] == jd["mechanism"]}
    return {"agents": agents, "mechanism": mech,
            "any": (not mech["same"]) or any(not a["same"] for a in agents.values())}


# --- app state --------------------------------------------------------------------------------

class App:
    def __init__(self, sample_path: Path, rater: str, labels_dir: Path, artifacts: Path,
                 overlap_only: bool = False):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", rater):
            raise ValueError("rater must be [A-Za-z0-9_-]+")
        self.sample_path = Path(sample_path)
        self.sample = json.loads(self.sample_path.read_text())
        self.rater = rater
        self.overlap_only = overlap_only
        self.labels_dir = Path(labels_dir)
        self.labels_file = V.labels_path(rater, self.labels_dir)
        self.trials_dir = Path(artifacts) / "trials"
        self.videos_dir = Path(artifacts) / "videos"
        self.by_id = {t["trial"]: t for t in self.sample["trials"]}
        self.lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}

    def in_mode(self, t: dict) -> bool:
        return bool(t.get("overlap")) or not self.overlap_only

    def labels(self) -> dict[str, dict]:
        return V.read_labels(self.labels_file)

    def video_urls(self, tid: str) -> dict:
        out = {"raw": None, "captioned": None, "offset_s": CLIP_OFFSET_S}
        if (self.videos_dir / f"{tid}.mp4").is_file():
            out["raw"] = f"/video/{tid}.mp4"
        if (self.videos_dir / "captioned" / f"{tid}.mp4").is_file():
            out["captioned"] = f"/video/captioned/{tid}.mp4"
        return out

    def state(self) -> dict:
        labs = self.labels()
        rows = []
        for t in self.sample["trials"]:
            if not self.in_mode(t):
                continue
            row = {k: t.get(k) for k in SAMPLE_FIELDS}
            row["overlap"] = bool(t.get("overlap"))
            row["phrasing"] = t.get("phrasing", "v0")
            row["done"] = t["trial"] in labs
            row["after_reveal"] = labs.get(t["trial"], {}).get("after_reveal")
            row["has_video"] = (self.videos_dir / f"{t['trial']}.mp4").is_file()
            rows.append(row)
        return {"rater": self.rater, "overlap_only": self.overlap_only, "sample": self.sample_path.name,
                "labels_file": str(self.labels_file),
                "progress": {"done": sum(r["done"] for r in rows), "total": len(rows)}, "trials": rows,
                "vocab": {"letters": V.LABEL_NAMES, "mechanisms": list(V.MECHS.values()),
                          "channels": list(V.CHANNELS.values())}}

    def parsed(self, tid: str) -> dict:
        p = self.trials_dir / tid / "trace.txt"
        if not p.exists():
            return {"sections": {}, "events": [], "missing": True}
        mt = p.stat().st_mtime
        with self.lock:
            hit = self._cache.get(tid)
        if hit and hit[0] == mt:
            return hit[1]
        d = parse_trace(p.read_text(errors="replace"))
        with self.lock:
            if len(self._cache) > 12:
                self._cache.pop(next(iter(self._cache)))
            self._cache[tid] = (mt, d)
        return d

    def trial(self, tid: str) -> dict:
        """Everything a rater needs, with NOTHING from judge.json."""
        t = self.by_id[tid]
        tdir = self.trials_dir / tid
        tj = json.loads((tdir / "trial.json").read_text()) if (tdir / "trial.json").exists() else {}
        sj = json.loads((tdir / "score.json").read_text()) if (tdir / "score.json").exists() else {}
        values = {r["role"]: r.get("value") for r in tj.get("roles", [])} or sj.get("values", {})
        agents = {}
        for r in V.ROLES:
            a = sj.get("agents", {}).get(r, {})
            agents[r] = {k: a.get(k) for k in ("value", "peer_value", "outcome", "final", "status", "seconds",
                                               "degradations")}
        tr = self.parsed(tid)
        sample_row = {k: t.get(k) for k in SAMPLE_FIELDS}
        sample_row["phrasing"] = t.get("phrasing", "v0")
        return {"trial": tid, "sample": sample_row, "model": tj.get("model") or sj.get("model"),
                "condition": tj.get("condition", {}), "start_at": tj.get("start_at"),
                "prompts": tj.get("prompts", {}),
                "truth": {"values": values, "answer": tj.get("truth", sj.get("truth"))},
                "agents": agents, "sections": tr["sections"], "events": tr["events"],
                "trace_missing": bool(tr.get("missing")), "video": self.video_urls(tid),
                "my_label": self.labels().get(tid)}

    def reveal(self, rec: dict) -> dict:
        jd = judge_detail(self.trials_dir / rec["trial"])
        return {"ok": True, "record": rec, "judge": jd, "diff": diff_view(rec, jd)}

    def add_label(self, body: dict) -> tuple[int, dict]:
        tid = body.get("trial")
        if tid not in self.by_id:
            return 404, {"error": "trial not in sample"}
        agents = body.get("agents") or {}
        rec_agents = {}
        for r in V.ROLES:
            v = agents.get(r, "")
            if not isinstance(v, str) or any(ch not in "dsbcf" for ch in v.lower()):
                return 400, {"error": f"agents.{r} must be letters from dsbcf"}
            rec_agents[r] = "".join(sorted(set(v.lower())))
        mech = body.get("mechanism")
        if mech not in V.MECHS.values():
            return 400, {"error": f"mechanism must be one of {list(V.MECHS.values())}"}
        chans = body.get("channels") or []
        if not isinstance(chans, list) or any(c not in V.CHANNELS.values() for c in chans):
            return 400, {"error": f"channels must be from {list(V.CHANNELS.values())}"}
        try:
            conf = int(body.get("confidence", 2))
            secs = max(0, int(round(float(body.get("seconds") or 0))))
        except (TypeError, ValueError):
            return 400, {"error": "confidence/seconds must be numbers"}
        if conf not in (1, 2, 3):
            return 400, {"error": "confidence must be 1-3"}
        note = body.get("note") or ""
        if not isinstance(note, str):
            return 400, {"error": "note must be a string"}
        rec = {"trial": tid, "rater": self.rater, "agents": rec_agents, "mechanism": mech,
               "channels": [c for c in V.CHANNELS.values() if c in chans], "confidence": conf,
               "note": note.strip(), "seconds": secs, "source": "web", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self.lock:
            if tid in V.done_trials(self.labels_file) and not body.get("replace"):
                return 409, {"error": "already labelled by this rater; resend with replace: true to overwrite"}
            self._append(rec)
        return 200, self.reveal(rec)

    def add_after_reveal(self, body: dict) -> tuple[int, dict]:
        tid = body.get("trial")
        if tid not in self.by_id:
            return 404, {"error": "trial not in sample"}
        v = body.get("after_reveal")
        if v not in AFTER_REVEAL:
            return 400, {"error": f"after_reveal must be one of {list(AFTER_REVEAL)}"}
        note = body.get("note") or body.get("after_reveal_note") or ""
        if not isinstance(note, str):
            return 400, {"error": "note must be a string"}
        rec = {"type": "after_reveal", "trial": tid, "rater": self.rater, "after_reveal": v,
               "after_reveal_note": note.strip(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self.lock:
            if tid not in V.done_trials(self.labels_file):
                return 409, {"error": "label the trial before recording an after-reveal verdict"}
            self._append(rec)
        return 200, {"ok": True, "record": rec}

    def _append(self, rec: dict) -> None:
        self.labels_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.labels_file, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def report(self) -> str:
        return V.report_text(self.sample_path, self.labels_dir, self.trials_dir)

    def video_path(self, rel: str) -> Path | None:
        m = VIDEO_RE.fullmatch(rel)
        if not m or m.group(2) not in self.by_id:
            return None
        base = self.videos_dir.resolve()
        p = (self.videos_dir / rel).resolve()
        if not p.is_relative_to(base) or not p.is_file():
            return None
        return p


# --- HTTP -------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "review_app/1"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # host / origin checks: block DNS rebinding and cross-site requests
    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _check_host(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in self._allowed_hosts():
            self._send_json(421, {"error": "unexpected Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower().removeprefix("http://") not in self._allowed_hosts():
            self._send_json(403, {"error": "cross-origin request refused"})
            return False
        return True

    def _common_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, status: int, data: bytes, ctype: str, head: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _send_json(self, status: int, obj, head: bool = False):
        self._send_bytes(status, json.dumps(obj).encode(), "application/json; charset=utf-8", head)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head: bool = False):
        if not self._check_host():
            return
        path = urlparse(self.path).path
        try:
            if path in STATIC:
                name, ctype = STATIC[path]
                return self._send_bytes(200, (UI_DIR / name).read_bytes(), ctype, head)
            if path == "/api/state":
                return self._send_json(200, self.app.state(), head)
            if path.startswith("/api/trial/"):
                tid = unquote(path[len("/api/trial/"):])
                if tid not in self.app.by_id:
                    return self._send_json(404, {"error": "trial not in sample"}, head)
                return self._send_json(200, self.app.trial(tid), head)
            if path.startswith("/api/reveal/"):
                tid = unquote(path[len("/api/reveal/"):])
                if tid not in self.app.by_id:
                    return self._send_json(404, {"error": "trial not in sample"}, head)
                rec = self.app.labels().get(tid)
                if rec is None:
                    return self._send_json(403, {"error": "label this trial first"}, head)
                return self._send_json(200, self.app.reveal(rec), head)
            if path == "/api/report":
                return self._send_json(200, {"text": self.app.report()}, head)
            if path.startswith("/video/"):
                p = self.app.video_path(unquote(path[len("/video/"):]))
                if p is None:
                    return self._send_json(404, {"error": "no such clip"}, head)
                return self._send_file_range(p, "video/mp4", head)
            return self._send_json(404, {"error": "not found"}, head)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        self.close_connection = True     # early rejections leave the body unread; never reuse the socket
        if not self._check_host():
            return
        path = urlparse(self.path).path
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send_json(415, {"error": "send application/json"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self._send_json(413, {"error": "body too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send_json(400, {"error": "invalid JSON"})
        if path == "/api/label":
            status, out = self.app.add_label(body)
        elif path == "/api/after_reveal":
            status, out = self.app.add_after_reveal(body)
        else:
            status, out = 404, {"error": "not found"}
        self._send_json(status, out)

    def _send_file_range(self, p: Path, ctype: str, head: bool):
        size = p.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = (self.headers.get("Range") or "").strip()
        if rng and "," not in rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
            bad = not m or (m.group(1) == "" and m.group(2) == "")
            if not bad:
                if m.group(1) == "":
                    n = int(m.group(2))
                    bad = n == 0
                    start = max(0, size - n)
                else:
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                bad = bad or start >= size or start > end
            if bad:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self._common_headers()
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._common_headers()
        self.end_headers()
        if head:
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def make_server(app: App, port: int = 8765, verbose: bool = False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    srv.app = app            # type: ignore[attr-defined]
    srv.verbose = verbose    # type: ignore[attr-defined]
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="review_app", description=__doc__.split("\n\n")[0])
    ap.add_argument("sample")
    ap.add_argument("--rater", required=True)
    ap.add_argument("--overlap-only", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--labels-dir", default=str(V.LABELS_DIR), help="where labels-<rater>.jsonl lives")
    ap.add_argument("--artifacts", default=str(V.REPO / "artifacts"),
                    help="artifacts root containing trials/ and videos/")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    a = ap.parse_args(argv)
    app = App(Path(a.sample), a.rater, Path(a.labels_dir), Path(a.artifacts), a.overlap_only)
    srv = make_server(app, a.port, a.verbose)
    st = app.state()["progress"]
    print(f"review_app: rater={a.rater}{' (overlap only)' if a.overlap_only else ''}  "
          f"{st['done']}/{st['total']} done  labels -> {app.labels_file}")
    print(f"open http://127.0.0.1:{srv.server_address[1]}/   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
