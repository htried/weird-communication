#!/usr/bin/env python3
"""Redact credentials in stored trial artifacts (text files). Run on the Docker host:
    python3 scripts/scrub_artifacts.py artifacts/trials
Rewrites files in place only when something was masked; prints a summary."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.redact import redact

NAMES = ("events.jsonl", "stderr.log", "entry.log", "trace.txt", "score.json", "trial.json")
root = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/trials")
changed = 0
for f in root.rglob("*"):
    if f.is_file() and (f.name in NAMES or f.name.startswith("judge")):
        try:
            text = f.read_text(errors="surrogateescape")
        except OSError:
            continue
        new = redact(text)
        if new != text:
            f.write_text(new, errors="surrogateescape"); changed += 1
print(f"scrubbed {changed} files under {root}")
