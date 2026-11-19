"""Phrasing ablation (plan 2026-11-19 Part B): solved and judge contact per (model, rung, env), original
wording (v0, from lm1/lx1/lp1) vs factual rewordings f1-f3 (la1/la2), x+y scripted, degraded runs
excluded. Run from the repo root on the host with the trials: python3 analysis/phrasing_ablation.py"""

import json, glob, sys, math, collections
sys.path.insert(0, "analysis"); from validate import judge_view
from pathlib import Path
MODELS = {"gpt-6.1-sol": "Sol", "gpt-6-astra": "Astra", "claude-opus-5-5": "Opus", "amazon-bedrock/zai.glm-5": "GLM"}
def rows(pattern, phrasing_override=None):
    out = []
    for f in glob.glob(pattern):
        d = Path(f).parent
        s = json.load(open(f)); m = MODELS.get(s["model"])
        if not m or s["task"] != "secret_sum" or s["hatch"] != "scripted" or s["rung"] not in (2, 4, 5): continue
        if any(a.get("degradations") for a in s["agents"].values()): continue
        jv = judge_view(d)
        outs = [a["outcome"] for a in s["agents"].values()]
        out.append({"model": m, "env": s["env"], "rung": s["rung"], "ph": phrasing_override or s.get("phrasing", "v0"),
                    "solved": "correct" in outs, "contact": bool(jv) and any(c in v for v in jv["agents"].values() for c in "bc")})
    return out
R = rows("artifacts/trials/lm1-*/score.json") + rows("artifacts/trials/lx1-*/score.json") + rows("artifacts/trials/lp1-*/score.json") \
    + rows("artifacts/trials/la1-*/score.json") + rows("artifacts/trials/la2-*/score.json") + rows("artifacts/trials/la1c-*/score.json", "v0ctl")
def fisher(a, b, c, d):  # two-sided, 2x2 [[a,b],[c,d]]
    n1, n2, k = a + b, c + d, a + c; N = n1 + n2
    p = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / math.comb(N, k)
    obs = p(a); return min(1.0, sum(p(x) for x in range(max(0, k - n2), min(k, n1) + 1) if p(x) <= obs * (1 + 1e-9)))
g = collections.defaultdict(list)
for r in R: g[(r["model"], r["rung"], r["env"], r["ph"])].append(r)
print("SOLVED (and judge contact) per cell: v0 = original wording; f1-f3 = factual rewordings")
for metric in ("solved", "contact"):
    print(f"\n=== {metric}")
    print(f"{'model':<6}{'rung':>5} {'env':<6}" + "".join(f"{p:>9}" for p in ("v0", "f1", "f2", "f3")) + "   v0 vs f1-3 pooled (Fisher p)")
    for m in ("Sol", "Astra", "Opus", "GLM"):
        for rung in (2, 4, 5):
            for env in ("env1s", "env2s"):
                cells = {p: g.get((m, rung, env, p), []) for p in ("v0", "f1", "f2", "f3")}
                fr = lambda L: f"{sum(x[metric] for x in L)}/{len(L)}" if L else "-"
                v0 = cells["v0"]; fx = cells["f1"] + cells["f2"] + cells["f3"]
                a, b = sum(x[metric] for x in v0), len(v0) - sum(x[metric] for x in v0)
                c, d = sum(x[metric] for x in fx), len(fx) - sum(x[metric] for x in fx)
                pv = fisher(a, b, c, d) if v0 and fx else float("nan")
                share = lambda k, n: f"{100*k/n:.0f}%" if n else "-"
                print(f"{m:<6}{rung:>5} {env:<6}" + "".join(f"{fr(cells[p]):>9}" for p in ("v0", "f1", "f2", "f3"))
                      + f"   {share(a, a+b):>5} vs {share(c, c+d):>5}  p={pv:.2f}")
ctl = [r for r in R if r["ph"] == "v0ctl"]
print("\nla1c drift control (original wording, run today):", [(r["model"], r["env"], r["rung"], r["solved"]) for r in ctl])

t = collections.Counter()
for r in R:
    if r["ph"] == "v0ctl": continue
    for k in ("v0" if r["ph"] == "v0" else "f", r["ph"]):
        t[(k, "n")] += 1; t[(k, "solved")] += r["solved"]; t[(k, "contact")] += r["contact"]
print("\nPOOLED (all models, rungs 2/4/5, both envs):")
for k in ("v0", "f", "f1", "f2", "f3"):
    n = t[(k, "n")]; s = t[(k, "solved")]; c = t[(k, "contact")]
    print(f"  {k:<3} n={n:>3}  solved {s:>3} ({100*s/n:.0f}%)  contact {c:>3} ({100*c/n:.0f}%)")
