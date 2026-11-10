#!/usr/bin/env python3
"""Print judge.json results for trials.

    python3 analysis/judge_report.py artifacts/trials/<id> [...]
"""
import json
import sys
from pathlib import Path

for t in sys.argv[1:]:
    p = Path(t) / "judge.json"
    if not p.exists():
        continue
    j = json.loads(p.read_text())
    m, prov = j["measurement"], j["provenance"]
    u = prov.get("usage") or {}
    adj = f"  ** ADJUDICATE: {m['adjudication_reason']}" if m["needs_human_adjudication"] else ""
    print(f"== {Path(t).name}  [{prov['model']}, {prov['seconds']}s, in={u.get('input_tokens')} "
          f"out={u.get('output_tokens')}]  mechanism={m['trial_mechanism']}{adj}")
    print(f"   {m['trial_summary']}")
    for a in m["agents"]:
        print(f"   {a['agent']}: {a['final_answer_status']}, mechanism={a['mechanism']}, "
              f"obtained_peer={a['obtained_peer_value']}, disclosed={a['disclosed_method']}")
    for lab in m["labels"]:
        q = lab["evidence"][0]["quote"] if lab["evidence"] else ""
        print(f"   {lab['taxonomy_id']} {lab['agent']:<5} {lab['lifecycle_stage']:<9} ch={lab['channel']:<11} "
              f"{lab['perceptibility']:<14} sev={lab['severity']:<6} conf={lab['confidence']:.2f} "
              f"reported={lab['reported']}  | {q[:80]!r}")
    print()
