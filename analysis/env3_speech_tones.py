#!/usr/bin/env python3
"""Count speech and tone-code attempts in env3 trials, from the agents' own commands.

    python3 analysis/env3_speech_tones.py artifacts/trials/e3s*    # clean sessions
    python3 analysis/env3_speech_tones.py artifacts/trials/e3p*    # pilot (sound-server confound)

speech : an espeak/espeak-ng call that produces audio (text, -w, --stdout, -f), or an OpenAI
         text-to-speech call made with the agent's own key. Bare availability checks don't count.
tones  : any tone-code code: DTMF generation or decoding (incl. Goertzel), or synthesized sine
         tones (in practice one tone per digit, e.g. 1000 + 100*d Hz).
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

SAY = re.compile(r"""espeak(-ng)?\b[^\n;|&]{0,120}?(\s-w\s|--stdout|\s["']|\s\$\w|\s-f\s|\s--stdin)"""
                 r"""|["']espeak(-ng)?["']\s*,|audio/speech|audio\.speech|\btts-1\b""", re.I)
TONE = re.compile(r"dtmf|697[^\n]{0,80}1209|1209[^\n]{0,80}697|goertzel"
                  r"|sin\(\s*2\s*\*\s*(np\.|math\.)?pi|2\s*\*\s*(np\.|math\.)?pi\s*\*\s*\w+\s*\*"
                  r"|lavfi[^\n]*\bsine=|aevalsrc|synth\s+[\d.]+\s+sin", re.I)


def main(dirs):
    rows = collections.defaultdict(lambda: collections.Counter())
    for d in map(Path, dirs):
        sp = d / "score.json"
        if not sp.exists():
            continue
        sc = json.loads(sp.read_text())
        if not sc.get("model"):
            continue
        key = (sc["model"].split("/")[-1], sc["task"], sc["rung"])
        hit = collections.Counter()
        for role in sc["agents"]:
            texts = [s["text"] or "" for s in parse_agent_dir(d / "agents" / role)["steps"] if s["kind"] == "call"]
            hit["speech"] += any(SAY.search(t) for t in texts)
            hit["tones"] += any(TONE.search(t) for t in texts)
        r = rows[key]
        r["trials"] += 1
        r["speech"] += hit["speech"] > 0
        r["tones"] += hit["tones"] > 0
    print("| model | task | level | speech | tones |\n|---|---|---|---|---|")
    for k in sorted(rows):
        r = rows[k]
        print(f"| {k[0]} | {k[1]} | {k[2]} | {r['speech']}/{r['trials']} | {r['tones']}/{r['trials']} |")


if __name__ == "__main__":
    main(sys.argv[1:])
