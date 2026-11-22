#!/usr/bin/env python3
"""Figure: share of solved trials per model in env1 (shared machine) vs env2 (shared network).

    python3 analysis/figures/fig_main_solved.py   # writes docs/figures/fig_main_solved.{pdf,png}

Counts are the rule-based solve counts from docs/results/2026-11-18-n6-summary.md (Claude and
OpenAI at n=6; GLM 5 at n=3). Error bars: 95% Wilson intervals. Palette: categorical slots 1-2
(blue/orange) of the dataviz reference palette, validated for CVD separation on a light surface.
"""
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# (label, harness, env1 solved, env1 n, env2 solved, env2 n)
ROWS = [
    ("Haiku 5.5", "Claude Code", 1, 270, 0, 270),
    ("Sonnet 5.5$^\\dagger$", "Claude Code", 7, 233, 0, 236),
    ("Opus 5.5$^\\dagger$", "Claude Code", 118, 184, 20, 183),
    ("GPT-6 Luna", "Codex", 30, 270, 0, 270),
    ("GPT-6.1 Sol", "Codex", 171, 270, 151, 270),
    ("GPT-6 Astra", "Codex", 159, 270, 137, 270),
    ("GLM 5", "pi", 82, 135, 15, 135),
]
ENV1, ENV2 = "#2a78d6", "#eb6834"          # categorical slots 1, 2 (light)
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#ffffff"


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * p, 100 * max(0.0, c - h), 100 * min(1.0, c + h)


def main(out_dir: Path):
    plt.rcParams.update({"font.size": 8.5, "font.family": "sans-serif", "axes.edgecolor": INK2,
                         "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, ax = plt.subplots(figsize=(7.0, 2.7))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    width, gap = 0.36, 0.02                 # small surface gap between the paired bars
    xs = list(range(len(ROWS)))
    for i, (_, _, k1, n1, k2, n2) in enumerate(ROWS):
        for off, k, n, color in ((-(width + gap) / 2, k1, n1, ENV1), ((width + gap) / 2, k2, n2, ENV2)):
            p, lo, hi = wilson(k, n)
            ax.bar(i + off, p, width, color=color, edgecolor=SURFACE, linewidth=0.6, zorder=3)
            # max(): at k=0 the Wilson lower bound is 0 up to float error, which can be +1e-16
            ax.errorbar(i + off, p, yerr=[[max(p - lo, 0.0)], [max(hi - p, 0.0)]], fmt="none", ecolor=INK2, elinewidth=0.8,
                        capsize=2.2, capthick=0.8, zorder=4)
            ax.text(i + off, hi + 1.8, f"{p:.1f}%" if 0 < p < 1 else f"{p:.0f}%", ha="center", va="bottom", fontsize=7, color=INK2, zorder=5)
    # harness-family separators and labels
    for b in (2.5, 5.5):
        ax.axvline(b, color=GRID, linestyle=(0, (3, 3)), linewidth=0.9, zorder=1)
    for x, name in ((1, "Claude Code"), (4, "Codex"), (6, "pi")):
        ax.text(x, 103, name, ha="center", va="bottom", fontsize=8, color=INK2)
    ax.set_xticks(xs)
    ax.set_xticklabels([r[0] for r in ROWS])
    ax.set_xlim(-0.6, len(ROWS) - 0.4)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("Solved trials (%)")
    ax.yaxis.grid(True, color=GRID, linewidth=0.7, zorder=0)
    ax.tick_params(axis="x", length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(INK2)
    handles = [plt.Rectangle((0, 0), 1, 1, color=ENV1), plt.Rectangle((0, 0), 1, 1, color=ENV2)]
    ax.legend(handles, ["env1 (shared machine)", "env2 (shared network)"], loc="upper left",
              bbox_to_anchor=(0.0, 1.0), frameon=False, ncol=1, fontsize=8, handlelength=1.2,
              labelcolor=INK)
    fig.tight_layout(pad=0.3)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"fig_main_solved.{ext}", dpi=300, facecolor=SURFACE)
    print("wrote", out_dir / "fig_main_solved.pdf", "and .png")


if __name__ == "__main__":
    main(Path(__file__).resolve().parents[2] / "docs" / "figures")
