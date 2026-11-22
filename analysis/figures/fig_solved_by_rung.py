#!/usr/bin/env python3
"""Figure: solved % by prompt level, one small panel per model, env1 vs env2 lines.

    python3 analysis/figures/fig_solved_by_rung.py        # CSV -> docs/figures/fig_solved_by_rung.{pdf,png}
    python3 analysis/figures/fig_solved_by_rung.py --from-trials artifacts/trials/{lv1,lg1,lm1,lx1,lp1}-*
        # on the trial host: rewrite the CSV from score.json, degraded trials excluded, then plot

The CSV (solved_by_rung.csv) records where each count came from. Bands: 95% Wilson intervals.
Colors match fig_main_solved.py (categorical slots 1-2, validated for CVD separation).
"""
import argparse
import collections
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = Path(__file__).with_name("solved_by_rung.csv")
OUT = ROOT / "docs" / "figures"
# (display name, harness, score.json model ids)
MODELS = [
    ("Haiku 5.5", "Claude Code", {"claude-haiku-5-5"}),
    ("Sonnet 5.5", "Claude Code", {"claude-sonnet-5-5"}),
    ("Opus 5.5", "Claude Code", {"claude-opus-5-5"}),
    ("GPT-6 Luna", "Codex", {"gpt-6-luna"}),
    ("GPT-6.1 Sol", "Codex", {"gpt-6.1-sol"}),
    ("GPT-6 Astra", "Codex", {"gpt-6-astra"}),
    ("GLM 5", "pi", {"amazon-bedrock/zai.glm-5"}),
]
ENVS = {"env1s": "env1", "env2s": "env2"}
ENV1, ENV2 = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#ffffff"


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * p, 100 * max(0.0, c - h), 100 * min(1.0, c + h)


def from_trials(dirs):
    """Clean counts from score.json (same definitions as summarize_sweep.py)."""
    sys.path.insert(0, str(ROOT))
    from analysis.summarize_sweep import load
    by_id = {mid: (name, harness) for name, harness, ids in MODELS for mid in ids}
    cnt = collections.defaultdict(lambda: [0, 0])
    for d in dirs:
        r = load(Path(d))
        if r is None or r["degraded"] or r["model"] not in by_id or r["env"] not in ENVS or r.get("phrasing", "v0") != "v0":
            continue
        c = cnt[(by_id[r["model"]], ENVS[r["env"]], r["rung"])]
        c[0] += r["solved"]
        c[1] += 1
    with CSV.open("w", newline="") as f:
        f.write("# Solved trials per model x env x prompt rung (rule-based), degraded trials excluded.\n"
                "# Written by fig_solved_by_rung.py --from-trials.\n")
        w = csv.writer(f)
        w.writerow(["model", "harness", "env", "rung", "solved", "n", "source"])
        for name, harness, _ in MODELS:
            for env in ("env1", "env2"):
                for rung in range(1, 6):
                    k, n = cnt[((name, harness), env, rung)]
                    w.writerow([name, harness, env, rung, k, n, "clean"])
    print("wrote", CSV)


def read_csv():
    rows = [r for r in csv.DictReader(l for l in CSV.open() if not l.startswith("#"))]
    return {(r["model"], r["env"], int(r["rung"])): (int(r["solved"]), int(r["n"]), r["source"]) for r in rows}


def plot(data):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "font.family": "sans-serif", "axes.labelcolor": INK,
                         "xtick.color": INK2, "ytick.color": INK2, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(1, len(MODELS), figsize=(7.0, 2.35), sharey=True)
    fig.patch.set_facecolor(SURFACE)
    rungs = [1, 2, 3, 4, 5]
    for ax, (name, harness, _) in zip(axes, MODELS):
        ax.set_facecolor(SURFACE)
        for env, color, marker in (("env1", ENV1, "o"), ("env2", ENV2, "s")):
            ps, los, his = zip(*(wilson(*data[(name, env, r)][:2]) for r in rungs))
            ax.fill_between(rungs, los, his, color=color, alpha=0.14, linewidth=0, zorder=2)
            ax.plot(rungs, ps, color=color, linewidth=1.6, marker=marker, markersize=3.6,
                    markeredgecolor=SURFACE, markeredgewidth=0.6, zorder=3, clip_on=False)
        raw = any(data[(name, e, r)][2] == "raw" for e in ("env1", "env2") for r in rungs)
        dagger = "$^\\dagger$" if name in ("Sonnet 5.5", "Opus 5.5") else ""
        ax.set_title(name + dagger, fontsize=8, color=INK, pad=3)
        ax.set_xticks(rungs)
        ax.set_xlim(0.7, 5.3)
        ax.set_ylim(0, 100)
        ax.set_yticks(range(0, 101, 25))
        ax.yaxis.grid(True, color=GRID, linewidth=0.7, zorder=0)
        ax.tick_params(length=0, pad=2)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(INK2)
        if raw and dagger:
            print(f"note: {name} uses raw counts (degraded runs included) - regenerate with --from-trials")
    axes[0].set_ylabel("Solved trials (%)")
    fig.supxlabel("Prompt level", fontsize=8, color=INK, y=0.095)
    # harness-family labels above each group of panels
    handles = [plt.Line2D([], [], color=ENV1, marker="o", linewidth=1.6, markersize=3.6),
               plt.Line2D([], [], color=ENV2, marker="s", linewidth=1.6, markersize=3.6)]
    fig.legend(handles, ["env1 (shared machine)", "env2 (shared network)"], loc="lower center",
               bbox_to_anchor=(0.5, 0.0), ncol=2, frameon=False, fontsize=7.5, handlelength=1.8,
               columnspacing=2.0, labelcolor=INK)
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.27, top=0.8, wspace=0.12)
    # after the final layout, so the labels and rules sit exactly over their panels' plot areas
    for first, last, label in ((0, 2, "Claude Code"), (3, 5, "Codex"), (6, 6, "pi")):
        x0 = axes[first].get_position().x0
        x1 = axes[last].get_position().x1
        fig.text((x0 + x1) / 2, 0.985, label, ha="center", va="top", fontsize=8, color=INK2)
        fig.add_artist(plt.Line2D([x0, x1], [0.91, 0.91], color=GRID, linewidth=1.0))
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"fig_solved_by_rung.{ext}", dpi=300, facecolor=SURFACE)
    print("wrote", OUT / "fig_solved_by_rung.pdf", "and .png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-trials", nargs="+", metavar="TRIAL_DIR")
    a = ap.parse_args()
    if a.from_trials:
        from_trials(a.from_trials)
    plot(read_csv())


if __name__ == "__main__":
    main()
