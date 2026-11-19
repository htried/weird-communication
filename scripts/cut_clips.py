#!/usr/bin/env python3
"""Cut per-trial clips from the OBS room recording (env3), with a burned-in label and clock.

Run where ffmpeg and the recordings are (lab1):
    python3 cut_clips.py clips.json <recordings dir> <out dir>

clips.json: [{"trial", "model", "file", "ss" (s into file), "dur" (s), "clock0" (epoch at ss)}].
The clock overlay is wall-clock time (host TZ) computed from the recording's start time, so it
lines up with the anecdote timelines (EST) to within the recorder's clock error.
"""
import json
import subprocess
import sys
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def main():
    clips = json.loads(Path(sys.argv[1]).read_text())
    src, out = Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    for c in clips:
        dest = out / f"{c['trial']}.mp4"
        if dest.exists():
            print("exists", dest.name, flush=True)
            continue
        label = esc(f"{c['trial']}  {c['model']}")
        clock = f"%{{pts\\:localtime\\:{c['clock0']}\\:%H\\\\\\:%M\\\\\\:%S}}"
        box = "box=1:boxcolor=black@0.6:boxborderw=6:fontcolor=white"
        vf = (f"scale=1280:-2,"
              f"drawtext=fontfile={FONT}:text='{label}':x=12:y=12:fontsize=26:{box},"
              f"drawtext=fontfile={FONT}:text='{clock}  EST':x=12:y=52:fontsize=26:{box}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(c["ss"]), "-i", str(src / c["file"]),
               "-t", str(c["dur"]), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dest)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(("ok " if r.returncode == 0 else "FAIL ") + dest.name, r.stderr[-300:], flush=True)


if __name__ == "__main__":
    main()
