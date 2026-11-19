#!/usr/bin/env python3
"""Tally token usage and estimated agent cost per trial from the transcripts.

    python3 analysis/cost.py artifacts/trials/<prefix>-*

Claude Code reports its own `total_cost_usd`. Codex and pi report tokens, which are priced
with PRICES below ($ per 1M tokens: input, cached input, output). Judge cost lives in
judge.json provenance and is reported separately.
"""
import json
import sys
from pathlib import Path

PRICES = {
    "zai.glm-5": (1.00, 1.00, 3.20),          # Bedrock us-east-1 standard; Converse gets no cache discount
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.20, 10.00),
    "gpt-6-astra": (10.00, 1.00, 50.00),      # cached-input price assumed at 10% of input
    "deepseek-v4-pro": (1.32, 0.044, 3.96),   # peak list price (Nov 2026, third-party sources); off-peak is half
    "deepseek/deepseek-v4-pro": (0.90, 0.25, 1.50),   # OpenRouter list price, 2026-11-19 (plus ~5.5% credit fee)
    "google/gemini-4-argon": (2.00, 0.10, 10.00),     # OpenRouter list price, 2026-11-19 (doubles 2026-12-01 per Google)
}


def tally(tdir: Path) -> dict:
    meta = json.loads((tdir / "trial.json").read_text())
    model = meta.get("model") or ""
    mid = model.split("/", 1)[-1]
    tot = {"in": 0, "cached": 0, "out": 0, "usd": 0.0}
    for f in (tdir / "agents").glob("*/events.jsonl"):
        for line in f.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = e.get("type")
            if t == "result":                                           # Claude Code
                tot["usd"] += e.get("total_cost_usd") or 0
            elif t == "turn.completed":                                 # Codex
                u = e.get("usage", {})
                tot["in"] += u.get("input_tokens", 0); tot["cached"] += u.get("cached_input_tokens", 0)
                tot["out"] += u.get("output_tokens", 0)
            elif t == "message_end" and isinstance(e.get("message"), dict) \
                    and e["message"].get("role") == "assistant":        # pi
                u = e["message"].get("usage") or {}
                tot["in"] += u.get("input", 0) + u.get("cacheRead", 0); tot["cached"] += u.get("cacheRead", 0)
                tot["out"] += u.get("output", 0)
    if mid in PRICES and tot["usd"] == 0:
        pin, pcache, pout = PRICES[mid]
        tot["usd"] = ((tot["in"] - tot["cached"]) * pin + tot["cached"] * pcache + tot["out"] * pout) / 1e6
    j = tdir / "judge.json"
    ju = json.loads(j.read_text())["provenance"].get("usage") if j.exists() else None
    tot["judge_usd"] = ((ju["input_tokens"] * 2 + ju["output_tokens"] * 10) / 1e6) if ju else 0.0
    tot["model"] = model
    return tot


def main():
    rows = [(Path(t).name, tally(Path(t))) for t in sys.argv[1:] if (Path(t) / "trial.json").exists()]
    for name, r in rows:
        print(f"{name:<52} {r['model']:<32} in={r['in']/1e6:6.2f}M (cached {r['cached']/1e6:5.2f}M) "
              f"out={r['out']/1e3:7.1f}K  agents=${r['usd']:.3f}  judge=${r['judge_usd']:.3f}")
    if rows:
        a = sum(r["usd"] for _, r in rows); j = sum(r["judge_usd"] for _, r in rows)
        print(f"\n{len(rows)} trials: agents ${a:.2f} (${a/len(rows):.3f}/trial), judge ${j:.2f}")


if __name__ == "__main__":
    main()
