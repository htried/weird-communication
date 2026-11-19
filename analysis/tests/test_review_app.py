"""review_app.py end-to-end over HTTP. Stdlib unittest.

    python3 -m unittest analysis.tests.test_review_app

Builds a temporary artifacts tree from 2-3 real trial dirs of the validation sample (light files
only) plus a synthetic clip, and a temporary labels dir, so real label files are never touched.
Skips if those trial dirs are not on this machine (artifacts/ is git-ignored).
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from analysis import review_app as RA
from analysis import validate as V

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "artifacts" / "validation" / "sample-2026-11-19.json"
LIGHT = ("trace.txt", "judge.json", "score.json", "trial.json")


def pick_trials() -> list[dict]:
    if not SAMPLE.exists():
        return []
    rows = json.loads(SAMPLE.read_text())["trials"]
    ok = [t for t in rows if all((V.TRIALS_DIR / t["trial"] / f).exists() for f in LIGHT)]
    env3 = [t for t in ok if t["env"] == "env3"][:1]
    other = [t for t in ok if t["env"] != "env3"][:2]
    return env3 + other if env3 and other else other[:3]


class ReviewAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        trials = pick_trials()
        if len(trials) < 2:
            raise unittest.SkipTest("real trial dirs from the validation sample are not available")
        cls.tmp = Path(tempfile.mkdtemp(prefix="review-app-test-"))
        art = cls.tmp / "artifacts"
        for t in trials:
            d = art / "trials" / t["trial"]
            d.mkdir(parents=True)
            for f in LIGHT:
                shutil.copy(V.TRIALS_DIR / t["trial"] / f, d / f)
        cls.trials = trials
        cls.tid = trials[0]["trial"]
        (art / "videos" / "captioned").mkdir(parents=True)
        cls.clip = os.urandom(200_000)
        (art / "videos" / f"{cls.tid}.mp4").write_bytes(cls.clip)
        (art / "videos" / "notinsample.mp4").write_bytes(b"x" * 100)
        (art / "secret.txt").write_text("TOP-SECRET-CONTENT")
        sample = {"trials": trials}
        cls.sample_path = cls.tmp / "sample.json"
        cls.sample_path.write_text(json.dumps(sample))
        cls.labels_dir = cls.tmp / "labels"
        cls.app = RA.App(cls.sample_path, "tester", cls.labels_dir, art)
        cls.srv = RA.make_server(cls.app, port=0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        c.close()
        return out

    def label_body(self, tid, **kw):
        b = {"trial": tid, "agents": {"node1": "d", "node2": ""}, "mechanism": "none",
             "channels": ["network"], "confidence": 2, "note": "test", "seconds": 12}
        b.update(kw)
        return b

    # ---------------------------------------------------------------------------------------

    def test_state_and_static(self):
        st, _, body = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertEqual(d["rater"], "tester")
        self.assertEqual(d["progress"]["total"], len(self.trials))
        self.assertEqual({t["trial"] for t in d["trials"]}, {t["trial"] for t in self.trials})
        st, h, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"app.js", body)
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])

    def test_trial_has_no_judge_fields(self):
        for t in self.trials:
            st, _, body = self.req("GET", f"/api/trial/{t['trial']}")
            self.assertEqual(st, 200)
            d = json.loads(body)
            self.assertTrue(d["events"], "timeline parsed")
            self.assertIn(d["events"][0]["kind"], RA.KIND_NAMES)
            text = body.decode()
            m = json.loads((V.TRIALS_DIR / t["trial"] / "judge.json").read_text())["measurement"]
            for k in ("taxonomy_id", "trial_mechanism", "needs_human_adjudication", "adjudication_reason",
                      "trial_summary", "judge"):
                self.assertNotIn(k, d)
                self.assertNotIn(f'"{k}"', text)
            # judge-authored prose must not leak (evidence quotes are trace text, so not checked)
            for prose in [m.get("trial_summary"), m.get("adjudication_reason")] + \
                         [a.get("summary") for a in m.get("agents", [])]:
                if prose and len(prose) > 30:
                    self.assertNotIn(json.dumps(prose)[1:-1][:120], text)
        st, _, _ = self.req("GET", "/api/trial/not-in-sample")
        self.assertEqual(st, 404)
        st, _, _ = self.req("GET", f"/api/reveal/{self.trials[-1]['trial']}")
        self.assertEqual(st, 403, "no judge view before labelling")

    def test_label_reveal_duplicate_and_after_reveal(self):
        tid = self.trials[1]["trial"]
        st, _, body = self.req("POST", "/api/label", self.label_body(tid))
        self.assertEqual(st, 200, body)
        d = json.loads(body)
        j = d["judge"]
        self.assertEqual(set(j["agents"]), set(V.ROLES))
        for k in ("mechanism", "trial_summary", "adjudication_reason", "labels"):
            self.assertIn(k, j)
        if j["labels"]:
            self.assertTrue({"taxonomy_id", "agent", "evidence"} <= set(j["labels"][0]))
            if j["labels"][0]["evidence"]:
                self.assertIn("quote", j["labels"][0]["evidence"][0])
        self.assertEqual(d["diff"]["agents"]["node1"]["human"], "d")
        self.assertEqual(d["diff"]["mechanism"]["human"], "none")
        lf = self.labels_dir / "labels-tester.jsonl"
        recs = [json.loads(l) for l in lf.read_text().splitlines()]
        self.assertEqual(recs[-1]["trial"], tid)
        for k in ("trial", "rater", "agents", "mechanism", "channels", "confidence", "note", "seconds", "ts"):
            self.assertIn(k, recs[-1])
        # duplicate -> 409, replace -> 200
        st, _, _ = self.req("POST", "/api/label", self.label_body(tid))
        self.assertEqual(st, 409)
        st, _, _ = self.req("POST", "/api/label", self.label_body(tid, mechanism="snoop", replace=True))
        self.assertEqual(st, 200)
        # after-reveal record merges into the label for validate.py
        st, _, body = self.req("POST", "/api/after_reveal", {"trial": tid, "after_reveal": "judge_right", "note": "n"})
        self.assertEqual(st, 200, body)
        merged = V.read_labels(lf)[tid]
        self.assertEqual(merged["mechanism"], "snoop")
        self.assertEqual(merged["after_reveal"], "judge_right")
        self.assertEqual(merged["after_reveal_note"], "n")
        self.assertIn(tid, V.done_trials(lf))
        st, _, body = self.req("GET", f"/api/reveal/{tid}")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["record"]["after_reveal"], "judge_right")
        # report works over the temp labels dir and counts the after-reveal verdict
        st, _, body = self.req("GET", "/api/report")
        self.assertEqual(st, 200)
        self.assertIn("rater tester", json.loads(body)["text"])
        self.assertIn("sided with the judge on 1", json.loads(body)["text"])

    def test_label_validation(self):
        tid = self.trials[0]["trial"]
        st, _, _ = self.req("POST", "/api/label", self.label_body(tid, agents={"node1": "xyz"}))
        self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/label", self.label_body(tid, mechanism="telepathy"))
        self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/after_reveal", {"trial": tid, "after_reveal": "maybe"})
        self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/label", None, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 415)

    def test_video_range(self):
        path = f"/video/{self.tid}.mp4"
        st, h, body = self.req("GET", path, headers={"Range": "bytes=1000-1999"})
        self.assertEqual(st, 206)
        self.assertEqual(body, self.clip[1000:2000])
        self.assertEqual(h["Content-Range"], f"bytes 1000-1999/{len(self.clip)}")
        self.assertEqual(h["Accept-Ranges"], "bytes")
        st, _, body = self.req("GET", path, headers={"Range": "bytes=-500"})
        self.assertEqual((st, body), (206, self.clip[-500:]))
        st, _, body = self.req("GET", path, headers={"Range": "bytes=199990-"})
        self.assertEqual((st, body), (206, self.clip[199990:]))
        st, _, body = self.req("GET", path)
        self.assertEqual((st, body), (200, self.clip))
        st, h, _ = self.req("GET", path, headers={"Range": f"bytes={len(self.clip)}-"})
        self.assertEqual(st, 416)
        self.assertIsNone(json.loads(self.req("GET", f"/api/trial/{self.trials[1]['trial']}")[2])["video"]["raw"])
        self.assertEqual(json.loads(self.req("GET", f"/api/trial/{self.tid}")[2])["video"]["raw"], path)

    def test_path_traversal_rejected(self):
        for p in ("/video/../secret.txt", "/video/..%2fsecret.txt", "/video/%2e%2e/secret.txt",
                  "/video/captioned/../../secret.txt", f"/video/captioned/../{self.tid}.mp4",
                  "/video/notinsample.mp4", f"/video/../trials/{self.tid}/judge.json",
                  "/video//etc/passwd", "/style.css/../../review_app.py", "/../analysis/review_app.py"):
            st, _, body = self.req("GET", p)
            self.assertIn(st, (400, 403, 404), p)
            self.assertNotIn(b"TOP-SECRET", body)
            self.assertNotIn(b"measurement", body)

    def test_host_and_origin_checks(self):
        st, _, _ = self.req("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual(st, 421)
        st, _, _ = self.req("POST", "/api/label", self.label_body(self.tid), {"Origin": "http://evil.example"})
        self.assertEqual(st, 403)


class ParseTraceTest(unittest.TestCase):
    def test_parse(self):
        text = ("TRIAL x   env=env1s   harness=h   model=m\n\nENVIRONMENT: shared\nLockdown: yes\n\n"
                "TASK: secret_sum\nGROUND TRUTH: node1 private share=1, node2 private share=2, correct answer=3\n\n"
                "PROMPTS (each agent received only its own, on stdin):\n  node1: 'a'\n  node2: 'b'\n\n"
                "TIMELINE (seconds from agent start; both agents interleaved):\n"
                "[    1.0s] node1: SAYS: hi\n[    2.5s] node2: CALL shell: ls\n[    2.6s] node2: RESULT: [exit 0] a\nb\n"
                "[   10.0s] node1: USER (scripted reply): go on\n[   11.0s] node1: GOAL EVALUATOR (automated): no\n"
                "[   12.0s] node2: HARNESS NOTE: x\n[   13.0s] node2: PROXY: request to h REFUSED\n\n"
                "FINAL ANSWERS:\n  node1 (finished, 1s): '3'\n\nNETWORK SUMMARY (packet capture):\n  node1: none\n\n"
                "HARNESS NOTES (not agent behavior):\n  none\n")
        d = RA.parse_trace(text)
        self.assertEqual([e["kind"] for e in d["events"]], ["SAYS", "CALL", "RESULT", "USER", "GOAL", "HARNESS", "PROXY"])
        self.assertEqual(d["events"][1]["tool"], "shell")
        self.assertEqual(d["events"][2]["text"], "[exit 0] a\nb")
        self.assertEqual(d["events"][1]["t"], 2.5)
        self.assertIn("Lockdown", d["sections"]["environment"])
        self.assertIn("node1: none", d["sections"]["network_summary"])
        self.assertTrue(d["sections"]["harness_notes"].startswith("HARNESS NOTES"))


class ReadLabelsTest(unittest.TestCase):
    def test_merge_and_replace(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "labels-a.jsonl"
            rows = [{"trial": "t1", "agents": {"node1": "d", "node2": ""}, "mechanism": "none"},
                    {"type": "after_reveal", "trial": "t1", "after_reveal": "unsure", "after_reveal_note": "hm"},
                    {"type": "after_reveal", "trial": "t2", "after_reveal": "keep"},
                    {"trial": "t3", "agents": {"node1": "", "node2": ""}, "mechanism": "none", "after_reveal": "keep"},
                    {"trial": "t3", "agents": {"node1": "c", "node2": "c"}, "mechanism": "coordinate"}]
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            recs = V.read_labels(p)
            self.assertEqual(set(recs), {"t1", "t3"})
            self.assertEqual(V.done_trials(p), {"t1", "t3"})
            self.assertEqual(recs["t1"]["after_reveal"], "unsure")
            self.assertEqual(recs["t3"]["mechanism"], "coordinate")
            self.assertNotIn("after_reveal", recs["t3"])


if __name__ == "__main__":
    unittest.main()
