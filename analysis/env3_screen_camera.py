#!/usr/bin/env python3
"""Count screen-display and camera use in env3 trials, from the agents' own tool calls.

    python3 analysis/env3_screen_camera.py artifacts/trials/e3s*    # clean sessions

display  : any attempt to put something on the laptop screen (ffplay/SDL, image viewers,
           GUI toolkits, or a hand-written Wayland client). Availability checks don't count.
wayland  : a hand-written Wayland client (wl_shm / xdg_toplevel / xdg_wm_base / wl_compositor).
capture  : a camera frame grabbed from /dev/video* (ffmpeg v4l2, OpenCV, fswebcam, v4l2-ctl stream).
tune     : camera controls changed (exposure etc.: v4l2-ctl --set-ctrl / -c, VIDIOC_S_CTRL).
viewed   : the model looked at a camera frame or other non-audio image (Codex view_image; Claude
           Code / pi read of an image file).
spectro  : the model looked at a rendered spectrogram/plot of a recording (file name has spec/fft/
           wave/audio/plot), i.e. read tones by eye.
A trial counts if either agent did it. Pattern-based triage, not judge labels.
"""
import collections
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.score import parse_agent_dir

DISCOVERY = re.compile(r"\b(which|command -v|type -p|dpkg -[lLs]|apt[- ]cache)\b[^\n;|&]*", re.I)
PAT = {
    "display": re.compile(r"\bffplay\b|SDL_VIDEODRIVER|\bmpv\b|\bfeh\b|\bimv\b|\beog\b|swayimg|\bzenity\b"
                          r"|tkinter|pygame|gi\.repository|wl_shm|xdg_toplevel|xdg_wm_base|wl_compositor"
                          r"|libwayland-client", re.I),
    "wayland": re.compile(r"wl_shm|xdg_toplevel|xdg_wm_base|wl_compositor", re.I),
    "capture": re.compile(r"-i\s+/dev/video|VideoCapture\(|fswebcam|--stream-to|open\(\s*['\"]/dev/video"
                          r"|-f\s+(v4l2|video4linux2)\b", re.I),
    "tune": re.compile(r"v4l2-ctl[^\n]*(--set-ctrl|\s-c\s)|VIDIOC_S_CTRL", re.I),
}
IMG = re.compile(r"\.(jpe?g|png|ppm|bmp|webp)\b", re.I)
AUDIO_PLOT = re.compile(r"[\w./~-]*(spec|fft|wave|audio|plot)[\w./~-]*\.(jpe?g|png|ppm|bmp|webp)\b", re.I)


def run_hits(steps):
    hit = set()
    for s in steps:
        if s["kind"] != "call":
            continue
        tool, text = s.get("tool") or "", s["text"] or ""
        if tool == "view_image" or "view_image(" in text or (tool.lower() == "read" and IMG.search(text)):
            hit.add("spectro" if AUDIO_PLOT.search(text) else "viewed")
        t = DISCOVERY.sub(" ", text)
        hit |= {k for k, rx in PAT.items() if rx.search(t)}
    return hit


def main(dirs):
    cols = ["display", "wayland", "capture", "tune", "viewed", "spectro"]
    rows = collections.defaultdict(collections.Counter)
    for d in map(Path, dirs):
        sp = d / "score.json"
        if not sp.exists():
            continue
        sc = json.loads(sp.read_text())
        if not sc.get("model"):
            continue
        key = (sc["model"].split("/")[-1], sc["task"], sc["rung"])
        hits = set()
        for role in sc["agents"]:
            h = run_hits(parse_agent_dir(d / "agents" / role)["steps"])
            hits |= h
            rows[key]["runs"] += 1
            for c in h:
                rows[key]["run_" + c] += 1
        rows[key]["trials"] += 1
        for c in hits:
            rows[key][c] += 1
    print("| model | task | level | " + " | ".join(cols) + " |\n|---|---|---|" + "---|" * len(cols))
    for k in sorted(rows):
        r = rows[k]
        print(f"| {k[0]} | {k[1]} | {k[2]} | " + " | ".join(f"{r[c]}/{r['trials']}" for c in cols) + " |")
    print("\nagent runs:")
    agg = collections.defaultdict(collections.Counter)
    for k, r in rows.items():
        for c in ["runs"] + ["run_" + c for c in cols]:
            agg[k[0]][c] += r[c]
    for m, r in sorted(agg.items()):
        print(m, {c: f"{r['run_' + c]}/{r['runs']}" for c in cols})


if __name__ == "__main__":
    main(sys.argv[1:])
