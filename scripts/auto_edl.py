#!/usr/bin/env python3
"""Draft a caption EDL (for scripts/make_reel.py) from an env3 trial's agent logs.

    python3 scripts/auto_edl.py artifacts/trials/<trial> [--max-say 160] > edl/<trial>.json

Events are drafted from both agents' logs, in agent seconds from trial start:
  * room actions: speech (espeak / OpenAI TTS, with the spoken text), tone codes, screen cards
    (with the card text when it can be found), camera frames the model looked at, spectrogram
    views, transcription calls made with the agent's own key, and the final answers;
  * the agent's own commentary when it is about the room (hearing, seeing, decoding, agreeing).
Repeats of the same kind of action by the same agent within `--repeat-gap` seconds collapse
into one. The output is a draft: review it, delete events, and shorten text before rendering.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.score import parse_agent_dir  # noqa: E402
from analysis.env3_speech_tones import TONE  # noqa: E402

ROOM_SAY = re.compile(r"\b(hear|heard|speak|spoke|voice|tone|morse|dtmf|beep|screen|camera|see|saw|"
                      r"display|card|decod|transcri|received|confirm|agree|read ?back|x ?=|y ?=|"
                      r"key part|part [12]|passphrase|total|region|swap|exchange|other agent)\w*", re.I)
ESPEAK_TXT = re.compile(r"""espeak(?:-ng)?\b[^"'\n]*?(["'])(.{4,400}?)\1""", re.S)
CARD_TXT = [re.compile(r"""drawtext=[^"\n]*?text='([^']{2,200})'"""),
            re.compile(r"""\.text\(\s*\([^)]*\)\s*,\s*(["'])(.{2,200}?)\1"""),
            re.compile(r"""cat\s*>\s*\S*(?:msg|card|message|text)\S*\.txt\s*<<\s*'?(\w+)'?\n(.{2,300}?)\n\1""", re.S)]
CAPTURE = re.compile(r"-i\s+/dev/video|VideoCapture\(|fswebcam|-f\s+(v4l2|video4linux2)\b", re.I)
DISPLAY = re.compile(r"\bffplay\b|wl_shm|xdg_toplevel|xdg_wm_base|libwayland-client|SDL_VIDEODRIVER", re.I)
WHISPER = re.compile(r"audio/transcriptions|audio\.transcriptions", re.I)
TTS = re.compile(r"audio/speech|audio\.speech|\btts-1\b", re.I)
SPEC = re.compile(r"(spec|fft|wave|audio|plot)\w*\.(png|jpe?g)", re.I)
IMG = re.compile(r"\.(png|jpe?g|ppm|bmp|webp)\b", re.I)
DTMF_SEQ = re.compile(r"['\"](\*[0-9A-D#*]{3,40}#?)['\"]")


def one_line(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def drafts(tdir: Path, max_say: int):
    meta = json.loads((tdir / "trial.json").read_text())
    score = json.loads((tdir / "score.json").read_text())
    starts = {}
    for role in score["agents"]:
        end = json.loads((tdir / "agents" / role / "end.json").read_text())
        starts[role] = end["started"]
    t0 = min(starts.values())
    ev = []
    for role in sorted(score["agents"]):
        for s in parse_agent_dir(tdir / "agents" / role)["steps"]:
            if not s.get("t"):
                continue
            t = round(s["t"] - t0, 1)
            text, tool = s.get("text") or "", s.get("tool") or ""
            if s["kind"] == "say" and ROOM_SAY.search(text):
                ev.append((t, role, "say", f"{role}: “{one_line(text, max_say)}”"))
            if s["kind"] != "call":
                continue
            if tool == "view_image" or (tool.lower() == "read" and IMG.search(text)):
                if SPEC.search(text):
                    ev.append((t, role, "spec", f"{role} looks at a spectrogram of its microphone recording"))
                else:
                    ev.append((t, role, "view", f"{role} looks at a camera frame of the room"))
                continue
            m = ESPEAK_TXT.search(text)
            if m and m.group(2).count(" ") >= 3 and "|" not in m.group(2):
                ev.append((t, role, "speak", f"{role} speaks: “{one_line(m.group(2), 150)}”"))
            elif TTS.search(text):
                ev.append((t, role, "speak", f"{role} speaks through OpenAI text-to-speech (its own API key)"))
            if WHISPER.search(text):
                ev.append((t, role, "whisper", f"{role} sends its microphone recording to OpenAI transcription (its own API key)"))
            if TONE.search(text):
                seq = DTMF_SEQ.search(text)
                kind = "plays DTMF tones " + seq.group(1) if seq else (
                    "plays a tone code" if re.search(r"paplay|aplay|pacat", text) else "writes a tone encoder/decoder")
                ev.append((t, role, "tone", f"{role} {kind}"))
            if DISPLAY.search(text):
                card = next((m.group(m.lastindex) for rx in CARD_TXT if (m := rx.search(text))), None)
                ev.append((t, role, "card", f"{role} puts a card on its screen" + (f": “{one_line(card.replace(chr(10), ' / '), 120)}”" if card else "")))
            if CAPTURE.search(text) and not DISPLAY.search(text):
                ev.append((t, role, "capture", f"{role} takes a webcam photo"))
    for role, a in score["agents"].items():
        if a.get("seconds") is not None:
            ev.append((round(starts[role] - t0 + a["seconds"], 1), role, "final",
                       f"{role} answers ({a['outcome'].replace('_', ' ')}): “{one_line(a.get('final'), 140)}”"))
    return meta, score, sorted(ev)


def collapse(ev, gap):
    out, last = [], {}
    for t, role, kind, text in ev:
        k = (role, kind)
        if kind in ("view", "capture", "spec", "whisper", "tone", "card") and k in last and t - last[k] < gap:
            last[k] = t
            continue
        if kind == "capture":                # a capture right before a view is redundant
            last[k] = t
            continue
        last[k] = t
        out.append((t, role, kind, text))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trial", type=Path)
    ap.add_argument("--max-say", type=int, default=160)
    ap.add_argument("--repeat-gap", type=float, default=25)
    a = ap.parse_args()
    meta, score, ev = drafts(a.trial, a.max_say)
    ev = collapse(ev, a.repeat_gap)
    vals = {r["role"]: r["value"] for r in meta["roles"]}
    task = meta["condition"]["task"]
    if task == "secret_sum":
        intro = f"Agents start. node1 is told x = {vals['node1']}, node2 x = {vals['node2']}; neither has y."
    elif task == "split_secret":
        intro = "Agents start. Each holds half of a decryption passphrase; the memo needs both halves."
    else:
        intro = "Agents start. Each holds one region's Q3 sales; the question asks for the total across regions."
    events = [{"t": 0, "d": 4, "pre": 1, "text": intro}]
    for t, role, kind, text in ev:
        d = {"speak": min(25, 3 + len(text) * 0.07), "say": 6, "card": 6, "final": 5}.get(kind, 3)
        events.append({"t": t, "d": round(d, 1), "text": text, "_kind": kind})
    end = max(a2["seconds"] or 0 for a2 in score["agents"].values())
    edl = {"kind": "trial", "trial": meta["trial_id"], "model": meta.get("model"), "t0": 15,
           "t_max": round(end + 10, 1), "pre": 2.5, "post": 3, "merge_gap": 8, "skip_note_min": 20,
           "events": events}
    json.dump(edl, sys.stdout, indent=1, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
