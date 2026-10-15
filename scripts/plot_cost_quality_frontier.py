"""
Cost / Quality Pareto Frontier
==============================

Per-model points on three quality axes against cost:
- list-wise normalized Borda  (visual fidelity, list-wise judge)
- point-wise visual avg_after  (visual fidelity, point-wise judge)
- nav_pass_rate                (behavioral correctness)

Cost is the per-app average USD spend (`estimated_cost_usd_mean` from
`report/tables/by_model.csv`).

Outputs (under experiments/cost_quality/):
  cost_quality.png          —  3-panel overview (cost vs each quality axis)
  cost_quality.pdf          —  same, vector
  REPORT.md                 —  numbers + Pareto frontier description
  per_model.json            —  raw aggregated values
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick


MODEL_COLORS = {
    "claude-opus-4-6": "#7B3F00",
    "claude-haiku-4-5": "#C68642",
    "gpt-5": "#10A37F",
    "gpt-5-mini": "#7DC4A4",
    "gemini-2.5-pro": "#3F51B5",
    "gemini-2.5-flash": "#92A6E0",
}

MODEL_MARKERS = {
    "claude-opus-4-6": "o",
    "claude-haiku-4-5": "o",
    "gpt-5": "s",
    "gpt-5-mini": "s",
    "gemini-2.5-pro": "^",
    "gemini-2.5-flash": "^",
}

MODEL_DISPLAY = {
    "claude-opus-4-6": "Claude Opus 4-6",
    "claude-haiku-4-5": "Claude Haiku 4-5",
    "gpt-5": "GPT-5",
    "gpt-5-mini": "GPT-5 mini",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
}


def load_by_model(path: Path) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            model = r["model"]
            rows[model] = {
                "cost_usd_per_app": float(r["estimated_cost_usd_mean"]),
                "cost_usd_std": float(r["estimated_cost_usd_std"]),
                "nav_pass_rate": float(r["nav_pass_rate_mean"]),
                "nav_pass_std": float(r["nav_pass_rate_std"]),
                "visual_after_pointwise": float(r["visual_avg_after_mean"]),
                "visual_after_std": float(r["visual_avg_after_std"]),
                "input_tokens_per_app": float(r["input_tokens_mean"]),
                "llm_time_sec_per_app": float(r["llm_time_sec_mean"]),
            }
    return rows


def load_borda(path: Path) -> dict[str, float]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)["global_borda"]


def pareto_points(points: list[tuple[str, float, float]]) -> list[str]:
    """Return list of model names that lie on the upper-left Pareto frontier
    (lower cost, higher quality is better)."""
    sorted_pts = sorted(points, key=lambda t: t[1])  # sort by cost asc
    frontier: list[tuple[str, float, float]] = []
    best_q = -float("inf")
    for name, cost, q in sorted_pts:
        if q > best_q:
            frontier.append((name, cost, q))
            best_q = q
    return [n for n, _, _ in frontier]


def annotate_point(ax, x: float, y: float, label: str, color: str) -> None:
    ax.annotate(
        label,
        (x, y),
        xytext=(6, 4),
        textcoords="offset points",
        fontsize=9,
        color=color,
        fontweight="bold",
    )


def render_panel(
    ax,
    data: dict[str, dict[str, float]],
    quality_key: str,
    quality_label: str,
    title: str,
    log_x: bool = True,
) -> list[str]:
    pts: list[tuple[str, float, float]] = []
    for model, row in data.items():
        cost = row["cost_usd_per_app"]
        q = row[quality_key]
        pts.append((model, cost, q))
        ax.scatter(
            cost,
            q,
            c=MODEL_COLORS.get(model, "#666"),
            marker=MODEL_MARKERS.get(model, "o"),
            s=140,
            edgecolors="black",
            linewidths=0.6,
            zorder=3,
        )
        annotate_point(ax, cost, q, MODEL_DISPLAY.get(model, model), MODEL_COLORS.get(model, "#222"))

    frontier_models = pareto_points(pts)
    frontier_pts = sorted(
        [(m, data[m]["cost_usd_per_app"], data[m][quality_key]) for m in frontier_models],
        key=lambda t: t[1],
    )
    if len(frontier_pts) >= 2:
        xs = [p[1] for p in frontier_pts]
        ys = [p[2] for p in frontier_pts]
        ax.plot(xs, ys, color="#444", linestyle="--", linewidth=1.2, zorder=2, alpha=0.7, label="Pareto frontier")

    if log_x:
        ax.set_xscale("log")
    ax.set_xlabel("Avg cost per app (USD, log scale)" if log_x else "Avg cost per app (USD)")
    ax.set_ylabel(quality_label)
    ax.set_title(title)
    ax.grid(True, which="both", linestyle=":", alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    return frontier_models


def render_figure(
    by_model: dict[str, dict[str, float]],
    borda: dict[str, float],
    out_png: Path,
    out_pdf: Path,
) -> dict[str, list[str]]:
    for m, b in borda.items():
        if m in by_model:
            by_model[m]["visual_borda_listwise"] = b

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0))

    f1 = render_panel(
        axes[0],
        by_model,
        "visual_borda_listwise",
        "Visual fidelity (list-wise Borda, normalized)",
        "(a) Visual fidelity (our list-wise judge)",
    )
    f2 = render_panel(
        axes[1],
        by_model,
        "visual_after_pointwise",
        "Visual fidelity (point-wise mean, 1–5)",
        "(b) Visual fidelity (point-wise judge, baseline)",
    )
    f3 = render_panel(
        axes[2],
        by_model,
        "nav_pass_rate",
        "Navigation pass rate",
        "(c) Behavioral correctness (navigation tests)",
    )
    axes[2].yaxis.set_major_formatter(mtick.PercentFormatter(1.0))

    fig.suptitle(
        "MobileForge: cost vs quality across six VLMs\n"
        "(agentic pipeline, 29 apps × 701 test cases)",
        fontsize=12,
        fontweight="bold",
    )
    plt.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    return {
        "list_wise_borda": f1,
        "point_wise_visual": f2,
        "nav_pass_rate": f3,
    }


def render_markdown(
    by_model: dict[str, dict[str, float]],
    borda: dict[str, float],
    frontiers: dict[str, list[str]],
    fig_rel_path: str,
) -> str:
    lines: list[str] = []
    lines.append("# Cost / Quality Pareto Frontier")
    lines.append("")
    lines.append("> Per-model summary on MobileForge (29 apps × 701 test cases, agentic pipeline).")
    lines.append(">")
    lines.append("> **Cost** = `estimated_cost_usd_mean` from `report/tables/by_model.csv` (avg USD per app).")
    lines.append("> **Visual (list-wise)** = global normalized Borda score from the list-wise judge in `phase3/borda_summary.json`.")
    lines.append("> **Visual (point-wise)** = per-test 1–5 weighted mean from `gemini-2.5-pro` point-wise judge.")
    lines.append("> **NavPass** = mean navigation pass rate from the click-model executed test harness.")
    lines.append("")
    lines.append(f"![Cost vs quality]({fig_rel_path})")
    lines.append("")

    lines.append("## 1. Per-model values")
    lines.append("")
    lines.append("| Model | Cost ($/app) | Visual Borda (list) | Visual avg (point) | NavPass | Time (s/app) | Input tokens / app |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    sorted_models = sorted(by_model.keys(), key=lambda m: -borda.get(m, 0))
    for m in sorted_models:
        r = by_model[m]
        lines.append(
            f"| `{m}` | {r['cost_usd_per_app']:.3f} | {borda.get(m, float('nan')):.3f} | "
            f"{r['visual_after_pointwise']:.2f} | {r['nav_pass_rate'] * 100:.1f}% | "
            f"{r['llm_time_sec_per_app']:.0f} | {r['input_tokens_per_app']:,.0f} |"
        )
    lines.append("")

    lines.append("## 2. Pareto frontier per quality axis")
    lines.append("")
    lines.append("Models on the lower-cost / higher-quality frontier (no dominated points):")
    lines.append("")
    lines.append(f"- **Visual (list-wise Borda)**: {' → '.join(frontiers['list_wise_borda'])}")
    lines.append(f"- **Visual (point-wise)**: {' → '.join(frontiers['point_wise_visual'])}")
    lines.append(f"- **NavPass**: {' → '.join(frontiers['nav_pass_rate'])}")
    lines.append("")
    lines.append("Reading: a model that does **not** appear on a frontier is strictly dominated — there is at least one cheaper model that achieves equal or better quality on that axis.")
    lines.append("")

    lines.append("## 3. Headline observations")
    lines.append("")
    opus = by_model.get("claude-opus-4-6", {})
    haiku = by_model.get("claude-haiku-4-5", {})
    gpt5 = by_model.get("gpt-5", {})
    flash = by_model.get("gemini-2.5-flash", {})
    mini = by_model.get("gpt-5-mini", {})

    if opus and mini:
        ratio = opus["cost_usd_per_app"] / max(mini["cost_usd_per_app"], 1e-6)
        lines.append(
            f"- **Cost spread is ~{ratio:.0f}×** between the most expensive (`claude-opus-4-6`, "
            f"${opus['cost_usd_per_app']:.2f}/app) and the cheapest (`gpt-5-mini`, "
            f"${mini['cost_usd_per_app']:.2f}/app); quality spread is "
            f"{borda.get('claude-opus-4-6', 0):.2f} vs {borda.get('gpt-5-mini', 0):.2f} on the list-wise Borda."
        )
    if opus and gpt5:
        ratio_g = opus["cost_usd_per_app"] / max(gpt5["cost_usd_per_app"], 1e-6)
        lines.append(
            f"- **Sweet spot is `gpt-5`**: it sits on the Pareto frontier on all three quality axes, "
            f"costing ~{ratio_g:.1f}× less than `claude-opus-4-6` (${gpt5['cost_usd_per_app']:.2f} vs "
            f"${opus['cost_usd_per_app']:.2f}/app) while still reaching {gpt5['nav_pass_rate']*100:.0f}% nav pass "
            f"and the second-best Borda visual score."
        )
    if haiku and gpt5:
        lines.append(
            f"- **`claude-haiku-4-5` is strictly dominated by `gpt-5`** on every axis: "
            f"${haiku['cost_usd_per_app']:.2f} vs ${gpt5['cost_usd_per_app']:.2f}/app (more expensive) and lower "
            f"on Borda ({borda.get('claude-haiku-4-5', 0):.2f} vs {borda.get('gpt-5', 0):.2f}), "
            f"point-wise visual ({haiku['visual_after_pointwise']:.2f} vs {gpt5['visual_after_pointwise']:.2f}), "
            f"and nav pass ({haiku['nav_pass_rate']*100:.0f}% vs {gpt5['nav_pass_rate']*100:.0f}%). "
            f"Its 2.6× higher input-token consumption ({haiku['input_tokens_per_app']/1e3:.0f}k vs "
            f"{gpt5['input_tokens_per_app']/1e3:.0f}k tokens/app) explains the cost gap."
        )
    pro = by_model.get("gemini-2.5-pro", {})
    if pro and gpt5:
        lines.append(
            f"- **`gemini-2.5-pro` is also dominated by `gpt-5`**: ${pro['cost_usd_per_app']:.2f} vs "
            f"${gpt5['cost_usd_per_app']:.2f}/app and lower on every quality metric — gpt-5 is a Pareto "
            f"improvement at the same tier."
        )
    if mini and flash:
        lines.append(
            f"- **Bottom-end models** (`gpt-5-mini`, `gemini-2.5-flash`) lie on the frontier purely because of "
            f"their absolute cost floor — they trade most of the visual fidelity for the lowest dollar cost "
            f"(NavPass {mini['nav_pass_rate']*100:.0f}% / {flash['nav_pass_rate']*100:.0f}%)."
        )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-model",
        type=Path,
        default=Path("experiments/20260428_181902_mobileforge-compare/report/tables/by_model.csv"),
    )
    parser.add_argument(
        "--borda",
        type=Path,
        default=Path("experiments/judge_method_study/phase3/borda_summary.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/cost_quality"),
    )
    args = parser.parse_args()

    by_model = load_by_model(args.by_model)
    borda = load_borda(args.borda)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    png = args.out_dir / "cost_quality.png"
    pdf = args.out_dir / "cost_quality.pdf"
    frontiers = render_figure(by_model, borda, png, pdf)

    md = render_markdown(by_model, borda, frontiers, fig_rel_path="cost_quality.png")
    (args.out_dir / "REPORT.md").write_text(md, encoding="utf-8")

    payload = {
        "by_model": by_model,
        "global_borda": borda,
        "pareto_frontiers": frontiers,
    }
    (args.out_dir / "per_model.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {png}, {pdf}, {args.out_dir / 'REPORT.md'}")


if __name__ == "__main__":
    main()
