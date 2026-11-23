#!/usr/bin/env python3
"""Short highlight reel for the website: title cards plus moments from the captioned room clips,
with the agents' own synthetic voices and tones at normal speed and quiet stretches sped up
(labelled on screen).

    python3 scripts/make_highlight_reel.py [--ffmpeg PATH] [--out site/videos/highlights.mp4]

Inputs are the captioned clips in site/videos/ (times below are clip seconds; see the clip
transcripts in site/data/site.json). Needs an ffmpeg with drawtext.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VID = ROOT / "site" / "videos"
FONT = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
FONT_SANS = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
W, H, FPS = 1280, 720, 30

# ("card", seconds, title, subtitle) | ("clip", file stem, start, end, speed)
REEL = [
    ("card", 4.5, "Two coding agents. Two laptops. No network.",
     "Each has half of what it needs. Nobody tells them to talk."),
    ("card", 2.6, "A tone code, announced aloud", "GPT-6.1 Sol"),
    ("clip", "e3p1-5-scripted-r1", 49.9, 62.9, 1.0),
    ("clip", "e3p1-5-scripted-r1", 94.0, 98.0, 1.0),
    ("clip", "e3p1-5-scripted-r1", 111.4, 123.4, 1.0),
    ("clip", "e3p1-5-scripted-r1", 170.5, 179.1, 2.0),
    ("card", 2.6, "Morse code, then a phone-keypad protocol", "Claude Opus 5.5"),
    ("clip", "e3s2o-shardedsales-goal-r2", 37.5, 46.0, 1.0),
    ("clip", "e3s2o-shardedsales-goal-r2", 85.5, 92.0, 1.0),
    ("clip", "e3s2o-shardedsales-goal-r2", 123.4, 135.5, 2.0),
    ("clip", "e3s2o-shardedsales-goal-r2", 169.2, 177.7, 1.0),
    ("clip", "e3s2o-shardedsales-goal-r2", 193.9, 199.3, 2.0),
    ("card", 2.6, "A key half read off the other screen", "GPT-6 Astra"),
    ("clip", "e3s2a-splitsecret-4-goal-r3", 31.4, 39.8, 1.0),
    ("clip", "e3s2a-splitsecret-4-goal-r3", 39.8, 84.9, 4.0),
    ("clip", "e3s2a-splitsecret-4-goal-r3", 84.9, 92.0, 1.0),
    ("card", 4.0, "Nobody told them to.", "The Agents Are Talking  ·  work in progress"),
]


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")


def atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    parts.append(f"atempo={s:g}")
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out", type=Path, default=VID / "highlights.mp4")
    a = ap.parse_args()
    cmd, fc, idx = [a.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"], [], 0
    for k, item in enumerate(REEL):
        if item[0] == "card":
            _, dur, title, sub = item
            cmd += ["-f", "lavfi", "-t", f"{dur}", "-i", f"color=c=0x121211:s={W}x{H}:r={FPS}",
                    "-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=48000:cl=stereo"]
            fc.append(f"[{idx}:v]drawtext=fontfile='{FONT}':text='{esc(title)}':fontcolor=0xecebe7:fontsize=46:"
                      f"x=(w-text_w)/2:y=(h/2)-58,"
                      f"drawtext=fontfile='{FONT_SANS}':text='{esc(sub)}':fontcolor=0x9a9892:fontsize=28:"
                      f"x=(w-text_w)/2:y=(h/2)+18,fade=t=in:st=0:d=0.3,fade=t=out:st={dur - 0.3}:d=0.3,"
                      f"format=yuv420p,setsar=1[v{k}]")
            fc.append(f"[{idx + 1}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
            idx += 2
        else:
            _, stem, t0, t1, speed = item
            cmd += ["-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", str(VID / f"{stem}.mp4")]
            label = (f",drawtext=fontfile='{FONT_SANS}':text='{speed:g}× speed':fontcolor=white:fontsize=26:"
                     f"box=1:boxcolor=black@0.55:boxborderw=8:x=w-text_w-24:y=22") if speed != 1 else ""
            fc.append(f"[{idx}:v]scale={W}:{H},fps={FPS},setpts=(PTS-STARTPTS)/{speed:g}{label},"
                      f"format=yuv420p,setsar=1[v{k}]")
            fc.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                      f"asetpts=PTS-STARTPTS,{atempo(speed)}[a{k}]")
            idx += 1
    n = len(REEL)
    fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    script = a.out.with_suffix(".filter")
    script.write_text(";\n".join(fc))
    cmd += ["-/filter_complex", str(script), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "27", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(a.out)]
    try:
        subprocess.run(cmd, check=True)
    finally:
        script.unlink(missing_ok=True)
    total = sum(i[1] if i[0] == "card" else (i[3] - i[2]) / i[4] for i in REEL)
    print(f"wrote {a.out} (~{total:.0f} s)")


if __name__ == "__main__":
    main()
