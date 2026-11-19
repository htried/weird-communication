#!/usr/bin/env python3
"""Captioned, shortened trial clips and a highlight montage from the env3 room recording.

Runs where ffmpeg (with libass) and the per-trial clips are (lab1). Input clips are the ones
cut by scripts/cut_clips.py: they already carry the trial/model label and EST clock in the top
black band, and start `t0` seconds (15 s) before the agents start.

    python3 make_reel.py --clips ~/recordings/clips --out ~/recordings/reel/out EDL.json [...]

Edit decision lists (EDLs) are JSON. Times are agent seconds (+N s from trial.json start_at),
the same convention as the anecdote timelines, so events can be copied from the docs.

Trial EDL ({"kind": "trial"}):
    {"kind": "trial", "trial": "...", "model": "...", "source": "<trial>.mp4", "t0": 15,
     "pre": 2.5, "post": 3, "merge_gap": 8, "skip_note_min": 20,
     "events": [{"t": 44.6, "d": 26, "text": "node1 speaks: \\"...\\""}, ...]}
  Each event becomes a window [t - pre, t + d + post] (per-event "pre"/"post" override).
  Windows closer than merge_gap are merged; the time between windows is dropped. A dropped gap
  longer than skip_note_min gets a "⏩ 1m40s later" note (top right) at the start of the next
  window. An event's caption runs from t until the next event starts or its window ends; the
  first caption of a window also covers its lead-in padding. "t_max" clamps windows to the clip.
  Output: OUT/captioned/<trial>.mp4 (or "out"), plus OUT/work/<trial>.windows.json mapping
  [agent_from, agent_to, output_start] for spot checks.

Montage EDL ({"kind": "montage"}):
    {"kind": "montage", "out": "montage.mp4",
     "items": [{"card": "Title\\nline two", "dur": 8},
               {"edl": "e3p1-5-scripted-r1.json", "ranges": [[40, 75], [125, 150]]}, ...]}
  "card" items are black cards with centered text (ASS override tags allowed, "\\n" = new
  line). "edl" items cut the given agent-time ranges from that trial and reuse its event
  captions (an event that started before a range but is still running is shown from the
  range start). Optional "events" in the item replace the trial's events; gaps between ranges
  longer than the item's "skip_note_min" (default 8 s) get a skip note.

Captions sit in the bottom black band (y >= 548, below the camera image at y=180..540), in white;
"node1"/"node2" are colored to match small panel headers drawn above each camera image
(left = lab1/node1, right = lab2/node2). Long hex strings are abbreviated to first4…last4, and
anything that looks like an API key aborts the run.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

W, H, FPS = 1280, 720, 30
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
C_NODE1 = r"{\c&HFFC864&}"   # light blue (BGR)
C_NODE2 = r"{\c&H3CAAFF&}"   # orange
C_WHITE = r"{\c&HFFFFFF&}"
C_SKIP = r"{\c&H66E6FF&}"    # yellow
SKIP_FONT = "Noto Sans Symbols2"
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|sk_[A-Za-z0-9]{8,}|ABSK[A-Za-z0-9+/=]{8,}|Bearer\s+\S{8,})")
HEX_RE = re.compile(r"\b([0-9a-fA-F]{4})[0-9a-fA-F]{8,}([0-9a-fA-F]{4})\b")

ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,31,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,8,48,48,0,1
Style: Head,DejaVu Sans,22,&H00D0D0D0,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1,0,2,10,10,0,1
Style: Skip,DejaVu Sans,30,&H0066E6FF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1.5,0,9,10,10,0,1
Style: Card,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,110,110,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def clean(text: str) -> str:
    if SECRET_RE.search(text):
        sys.exit(f"refusing caption that looks like it holds a secret: {text[:40]}…")
    text = HEX_RE.sub(r"\1…\2", text)
    text = text.replace("{", "(").replace("}", ")") if "{\\" not in text else text
    return text


def colorize(text: str) -> str:
    text = re.sub(r"\bnode1\b", lambda m: C_NODE1 + "node1" + C_WHITE, text)
    return re.sub(r"\bnode2\b", lambda m: C_NODE2 + "node2" + C_WHITE, text)


def fmt_gap(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}m{sec % 60:02d}s later" if sec >= 60 else f"{sec}s later"


def build_windows(edl: dict) -> list:
    pre, post = edl.get("pre", 2.5), edl.get("post", 3.0)
    gap = edl.get("merge_gap", 8.0)
    lo_limit = -edl.get("t0", 15.0)
    hi_limit = edl.get("t_max", 1e9)
    wins = []
    for e in edl["events"]:
        a = max(lo_limit, e["t"] - e.get("pre", pre))
        b = min(hi_limit, e["t"] + e.get("d", 4.0) + e.get("post", post))
        wins.append([a, b])
    wins.sort()
    merged = []
    for a, b in wins:
        if merged and a - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def caption_lines(pieces: list, events: list, skip_min: float) -> list:
    """pieces: [(a, b, out_start)] in agent time for ONE trial. Returns ASS dialogue lines."""
    out = []
    evs = sorted(events, key=lambda e: e["t"])
    for i, (a, b, o) in enumerate(pieces):
        inside = [e for e in evs if a - 0.01 <= e["t"] < b]
        before = [e for e in evs if e["t"] < a and e["t"] + e.get("d", 4.0) > a]
        if before and (not inside or inside[0]["t"] > a + 0.5):
            inside = [dict(before[-1], t=a)] + inside
        for j, e in enumerate(inside):
            s = max(e["t"], a)
            if j == 0 and s - a <= 4.0:
                s = a  # cover the lead-in padding instead of leaving it uncaptioned
            nxt = inside[j + 1]["t"] if j + 1 < len(inside) else b
            end = min(nxt, b)
            if e.get("hold") is not None:
                end = min(end, s + e["hold"])
            if end - s < 0.3:
                continue
            text = colorize(clean(e["text"])).replace("\n", r"\N")
            out.append(f"Dialogue: 1,{ts(o + s - a)},{ts(o + end - a)},Cap,,0,0,0,,"
                       rf"{{\an8\pos({W // 2},552)}}{text}")
        if i > 0:
            g = a - pieces[i - 1][1]
            if g > skip_min:
                out.append(f"Dialogue: 2,{ts(o)},{ts(o + min(3.0, b - a))},Skip,,0,0,0,,"
                           rf"{{\an9\pos({W - 14},96)}}{{\fn{SKIP_FONT}}}⏩{{\fnDejaVu Sans}} {fmt_gap(g)}")
    return out


def headers(start: float, end: float) -> list:
    return [rf"Dialogue: 0,{ts(start)},{ts(end)},Head,,0,0,0,,{{\an2\pos({W // 4},176)}}"
            rf"left: {C_NODE1}node1{C_WHITE}'s laptop (lab1)",
            rf"Dialogue: 0,{ts(start)},{ts(end)},Head,,0,0,0,,{{\an2\pos({3 * W // 4},176)}}"
            rf"right: {C_NODE2}node2{C_WHITE}'s laptop (lab2)"]


def card_line(start: float, end: float, text: str) -> str:
    text = colorize(clean(text)).replace("\n", r"\N")
    return (rf"Dialogue: 1,{ts(start)},{ts(end)},Card,,0,0,0,,"
            rf"{{\an5\pos({W // 2},{H // 2})\fad(300,300)}}{text}")


def render(parts: list, ass_lines: list, dest: Path, work: Path) -> None:
    """parts: [("clip", path, ss, dur) | ("card", None, 0, dur)] concatenated in order."""
    work.mkdir(parents=True, exist_ok=True)
    ass = work / (dest.stem + ".ass")
    ass.write_text(ASS_HEAD + "\n".join(ass_lines) + "\n", encoding="utf-8")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    fc, idx = [], 0
    for k, (kind, path, ss, dur) in enumerate(parts):
        if kind == "clip":
            cmd += ["-ss", f"{ss:.3f}", "-t", f"{dur + 0.5:.3f}", "-i", str(path)]
            v, a = f"{idx}:v", f"{idx}:a"
            idx += 1
        else:
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"color=c=black:s={W}x{H}:r={FPS}",
                    "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            v, a = f"{idx}:v", f"{idx + 1}:a"
            idx += 2
        fc.append(f"[{v}]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1,setpts=PTS-STARTPTS,"
                  f"tpad=stop_mode=clone:stop_duration=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS[v{k}]")
        fc.append(f"[{a}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"asetpts=PTS-STARTPTS,apad,atrim=duration={dur:.3f},asetpts=PTS-STARTPTS[a{k}]")
    n = len(parts)
    fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    assp = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    fc.append(f"[cv]ass='{assp}'[ov]")
    (work / (dest.stem + ".filter")).write_text(";\n".join(fc))
    cmd += ["-filter_complex_script", str(work / (dest.stem + ".filter")),
            "-map", "[ov]", "-map", "[ca]", *ENC, str(dest)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed for {dest.name}:\n{r.stderr[-2000:]}")
    print(f"ok {dest} ({sum(p[3] for p in parts):.1f} s)", flush=True)


def trial_job(edl: dict, clips: Path, out: Path, work: Path) -> None:
    t0 = edl.get("t0", 15.0)
    src = clips / edl.get("source", edl["trial"] + ".mp4")
    wins = build_windows(edl)
    pieces, o = [], 0.0
    for a, b in wins:
        pieces.append((a, b, o))
        o += b - a
    lines = headers(0, o) + caption_lines(pieces, edl["events"], edl.get("skip_note_min", 20.0))
    parts = [("clip", src, t0 + a, b - a) for a, b, _ in pieces]
    dest = out / edl.get("out", f"captioned/{edl['trial']}.mp4")
    (work / (edl["trial"] + ".windows.json")).write_text(json.dumps(pieces, indent=1))
    render(parts, lines, dest, work)


def montage_job(edl: dict, base: Path, clips: Path, out: Path, work: Path) -> None:
    parts, lines, o = [], [], 0.0
    for item in edl["items"]:
        if "card" in item:
            d = item.get("dur", 4.0)
            parts.append(("card", None, 0, d))
            lines.append(card_line(o + 0.15, o + d - 0.1, item["card"]))
            o += d
            continue
        sub = json.loads((base / item["edl"]).read_text())
        t0 = sub.get("t0", 15.0)
        src = clips / sub.get("source", sub["trial"] + ".mp4")
        pieces = []
        seg_start = o
        for a, b in item["ranges"]:
            pieces.append((a, b, o))
            parts.append(("clip", src, t0 + a, b - a))
            o += b - a
        lines += headers(seg_start, o)
        lines += caption_lines(pieces, item.get("events", sub["events"]), item.get("skip_note_min", 8.0))
    render(parts, lines, out / edl.get("out", "montage.mp4"), work)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edls", nargs="+", type=Path)
    ap.add_argument("--clips", type=Path, required=True, help="dir with <trial>.mp4 from cut_clips.py")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=None, help="dir for .ass/.filter files (default OUT/work)")
    args = ap.parse_args()
    work = args.work or args.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    for p in args.edls:
        edl = json.loads(p.read_text())
        if edl["kind"] == "trial":
            trial_job(edl, args.clips, args.out, work)
        elif edl["kind"] == "montage":
            montage_job(edl, p.parent, args.clips, args.out, work)
        else:
            sys.exit(f"{p}: unknown kind {edl['kind']}")


if __name__ == "__main__":
    main()
