#!/usr/bin/env python3
"""Solved counts per configuration cell, for the website's prompt builder.

    python3 analysis/solved_by_cell.py artifacts/trials/<prefix>-* > cells.json
    python3 analysis/solved_by_cell.py --merge a.json b.json > analysis/figures/solved_by_cell.json

A cell is env x task x level x persistence x wording (v0 = original, f1-f3 = paraphrases of the
top level's sentence) x model. Values are [solved, n] over clean trials: degraded runs
(score.py `degradations`: API errors, safeguard re-routing) are excluded, as everywhere else.
"""
import json
import sys
from pathlib import Path

ENV = {"env1s": "env1", "env2s": "env2", "env3": "env3"}


def count(dirs):
    cells = {}
    for d in map(Path, dirs):
        try:
            s = json.loads((d / "score.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not s.get("model") or s.get("env") not in ENV or s.get("rung") is None:
            continue
        if any(a.get("degradations") for a in s["agents"].values()):
            continue
        key = "|".join([ENV[s["env"]], s["task"], str(s["rung"]), s["hatch"], s.get("phrasing") or "v0",
                        s["model"].split("/")[-1]])
        k, n = cells.get(key, [0, 0])
        cells[key] = [k + any(a["outcome"] == "correct" for a in s["agents"].values()), n + 1]
    return cells


def main():
    if sys.argv[1:2] == ["--merge"]:
        out = {}
        for f in sys.argv[2:]:
            for key, (k, n) in json.loads(Path(f).read_text()).items():
                a = out.get(key, [0, 0])
                out[key] = [a[0] + k, a[1] + n]
    else:
        out = count(sys.argv[1:])
    json.dump(dict(sorted(out.items())), sys.stdout, indent=0)
    print()


if __name__ == "__main__":
    main()
