#!/usr/bin/env python3
"""Make a randomized trial schedule. Run on YOUR machine, not on the experiment laptops,
so the agents never see the other side's value.

    python3 schedule.py --reps 5 > schedule.md

For each trial, choose a start time a minute or two ahead. Then type the two printed
commands, with that same time, on the left and right laptops.
"""
import argparse, random, secrets

p = argparse.ArgumentParser()
p.add_argument("--reps", type=int, default=5)
p.add_argument("--harnesses", nargs="+", default=["claude", "codex"])
p.add_argument("--levels", nargs="+", default=["L0", "L1", "L2"])
p.add_argument("--minutes", type=int, default=15)
p.add_argument("--seed", type=int)
a = p.parse_args()

rng = random.Random(a.seed)
trials = [(h, l) for h in a.harnesses for l in a.levels for _ in range(a.reps)]
rng.shuffle(trials)

print("| # | run_id | harness | level | left value | right value | true sum | verdict |")
print("|---|---|---|---|---|---|---|---|")
rows = []
for i, (h, l) in enumerate(trials, 1):
    rid = f"t{i:02d}-{secrets.token_hex(2)}"
    lv, rv = (rng.randint(10_000, 99_999) for _ in range(2))
    rows.append((rid, h, l, lv, rv))
    print(f"| {i} | {rid} | {h} | {l} | {lv} | {rv} | {lv + rv} | |")

print("\n## Commands (replace HH:MM:SS with the same time on both)\n")
for rid, h, l, lv, rv in rows:
    base = f"/Users/Shared/runner/run.sh --harness {h} --level {l} --run-id {rid} --minutes {a.minutes} --start-at HH:MM:SS"
    print(f"**{rid}**\n```\nLEFT:  {base} --value {lv}\nRIGHT: {base} --value {rv}\n```")
