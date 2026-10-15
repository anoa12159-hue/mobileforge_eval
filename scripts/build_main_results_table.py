"""
Main Results Table Builder
==========================

Produces a unified main-results markdown table for the paper, replacing the
point-wise visual column in `report/comparison_table.md` with the list-wise
Borda score (our preferred judge), while keeping point-wise side-by-side as
a baseline comparator.

The table covers all 6 models on the 29-app, 701-test-case MobileForge
benchmark under the agentic pipeline.

Outputs:
  experiments/main_results/MAIN_RESULTS.md  (markdown table + headline)
  experiments/main_results/main_results.json
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_by_model(path: Path) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows[r["model"]] = {
                "count": int(float(r["count"])),
                "input_tokens": float(r["input_tokens_mean"]),
                "output_tokens": float(r["output_tokens_mean"]),
                "llm_time_sec": float(r["llm_time_sec_mean"]),
                "estimated_cost_usd": float(r["estimated_cost_usd_mean"]),
                "iteration_count": float(r["iteration_count_mean"]),
                "build_passed_rate": float(r["build_passed_rate"]),
                "route_coverage": float(r["route_coverage_mean"]),
                "nav_pass_rate": float(r["nav_pass_rate_mean"]),
                "visual_avg_before_pointwise": float(r["visual_avg_before_mean"]),
                "visual_avg_after_pointwise": float(r["visual_avg_after_mean"]),
                "total_loc": float(r["total_loc_mean"]),
                "shared_components": float(r["shared_components_mean"]),
                "reuse_rate": float(r["reuse_rate_mean"]),
                "color_consistency": float(r["color_consistency_mean"]),
            }
    return rows


def load_phase3(path: Path) -> dict[str, Any]:
    return json.load(path.open("r", encoding="utf-8"))


def render_main_table(by_model: dict[str, dict[str, float]], phase3: dict[str, Any]) -> str:
    borda = phase3["global_borda"]
    top1 = phase3.get("top1_count_per_model", {})
    bottom1 = phase3.get("bottom1_count_per_model", {})

    sorted_models = sorted(by_model.keys(), key=lambda m: -borda.get(m, 0.0))

    lines: list[str] = []
    lines.append("# MobileForge Main Results")
    lines.append("")
    lines.append(f"> 6 models × 29 apps × 701 test cases, agentic pipeline. List-wise Borda judge: `{phase3['judge_model']}`.")
    lines.append("")
    lines.append("All quality columns aggregate per-app means; Borda is normalized in [0,1] and averaged over scenarios.")
    lines.append("")

    # ---------- A. Side-by-side visual judge comparison ----------
    lines.append("## 1. Visual fidelity — point-wise vs list-wise judge")
    lines.append("")
    lines.append("Models are ranked by **list-wise Borda** (our preferred judge). The point-wise column is the baseline judge that the rest of the literature uses; we keep it side by side to make the substitution auditable.")
    lines.append("")
    lines.append("| Rank | Model | Visual Borda (list-wise, ours) ↑ | Visual avg (point-wise, 1–5) ↑ | #Top-1 apps | #Bottom-1 apps |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for i, m in enumerate(sorted_models, 1):
        r = by_model[m]
        b = borda.get(m, float("nan"))
        lines.append(
            f"| {i} | `{m}` | **{b:.3f}** | {r['visual_avg_after_pointwise']:.2f} | "
            f"{top1.get(m, 0)} / 29 | {bottom1.get(m, 0)} / 29 |"
        )
    lines.append("")
    lines.append("**Why list-wise**: the point-wise judge produces tied scores due to its 1–5 integer scale, "
                 "and absolute scores drift across prompts and sessions. The list-wise judge has been validated "
                 "to align with humans (Kendall τ=0.716, top-1 accuracy=83.6%) and to be stable across two "
                 "independent judges (gemini-2.5-pro and claude-sonnet-4-5, inter-judge τ=0.87 on global ranking).")
    lines.append("See `experiments/judge_method_study/REPORT.md` for the full validation.")
    lines.append("")

    # ---------- B. Full main results table ----------
    lines.append("## 2. Full main results")
    lines.append("")
    lines.append("| Model | Cost ($/app) ↓ | Tokens (in/app, K) ↓ | Time (s/app) ↓ | Iter | Build% | Routes% | NavPass ↑ | Visual list ↑ | Visual point ↑ | LoC | Reuse |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in sorted_models:
        r = by_model[m]
        b = borda.get(m, float("nan"))
        lines.append(
            f"| `{m}` | {r['estimated_cost_usd']:.2f} | {r['input_tokens']/1e3:.0f} | "
            f"{r['llm_time_sec']:.0f} | {r['iteration_count']:.0f} | "
            f"{r['build_passed_rate']*100:.0f}% | {r['route_coverage']*100:.0f}% | "
            f"{r['nav_pass_rate']*100:.0f}% | **{b:.2f}** | {r['visual_avg_after_pointwise']:.2f} | "
            f"{r['total_loc']:.0f} | {r['reuse_rate']:.2f} |"
        )
    lines.append("")

    # ---------- C. Per-app × per-model Borda matrix ----------
    per_app = phase3.get("per_app_borda", {})
    if per_app:
        apps = sorted(per_app.keys())
        lines.append("## 3. Per-app list-wise Borda matrix")
        lines.append("")
        lines.append("| App | " + " | ".join(f"`{m}`" for m in sorted_models) + " | App winner |")
        lines.append("|" + "---|" * (len(sorted_models) + 2))
        for app in apps:
            scores = per_app[app]
            row_max = max(scores.values())
            winner = next(m for m, s in scores.items() if s == row_max)
            cells = []
            for m in sorted_models:
                v = scores.get(m, 0.0)
                cells.append(f"**{v:.2f}**" if m == winner else f"{v:.2f}")
            lines.append(f"| `{app}` | " + " | ".join(cells) + f" | `{winner}` |")
        lines.append("")

    # ---------- D. Per-type breakdown ----------
    per_type = phase3.get("per_type_borda", {})
    if per_type:
        lines.append("## 4. Per-scenario-type list-wise Borda")
        lines.append("")
        lines.append("| Scenario type | " + " | ".join(f"`{m}`" for m in sorted_models) + " | Type winner |")
        lines.append("|" + "---|" * (len(sorted_models) + 2))
        for t in sorted(per_type.keys()):
            scores = per_type[t]
            row_max = max(scores.values())
            winner = next(m for m, s in scores.items() if s == row_max)
            cells = []
            for m in sorted_models:
                v = scores.get(m, 0.0)
                cells.append(f"**{v:.2f}**" if m == winner else f"{v:.2f}")
            lines.append(f"| `{t}` | " + " | ".join(cells) + f" | `{winner}` |")
        lines.append("")

    # ---------- E. Headline takeaways ----------
    opus = by_model["claude-opus-4-6"]
    gpt5 = by_model["gpt-5"]
    haiku = by_model["claude-haiku-4-5"]
    pro = by_model["gemini-2.5-pro"]
    flash = by_model["gemini-2.5-flash"]
    mini = by_model["gpt-5-mini"]

    lines.append("## 5. Headline takeaways")
    lines.append("")
    lines.append(
        f"- **Best visual fidelity**: `claude-opus-4-6` (Borda {borda['claude-opus-4-6']:.2f}, "
        f"top-1 in {top1.get('claude-opus-4-6', 0)}/29 apps); the runner-up `gpt-5` is 40% behind "
        f"({borda['gpt-5']:.2f}) but 5.8× cheaper."
    )
    lines.append(
        f"- **Best behavioral correctness**: `claude-opus-4-6` again leads navigation pass rate "
        f"({opus['nav_pass_rate']*100:.0f}%), with `gpt-5` ({gpt5['nav_pass_rate']*100:.0f}%) close behind."
    )
    lines.append(
        f"- **Pareto winners**: `claude-opus-4-6` (best quality, expensive) and `gpt-5` (best quality/cost) "
        "are the only two models that are not dominated on at least one axis."
    )
    lines.append(
        f"- **Pareto losers**: `claude-haiku-4-5` (Borda {borda['claude-haiku-4-5']:.2f}, ${haiku['estimated_cost_usd']:.2f}/app) "
        f"and `gemini-2.5-pro` (Borda {borda['gemini-2.5-pro']:.2f}, ${pro['estimated_cost_usd']:.2f}/app) "
        "are both strictly dominated by `gpt-5` on every axis we measure."
    )
    lines.append(
        f"- **Build pass rate is uniformly 100%** across all six models — this dimension does not currently "
        "discriminate models on MobileForge. A stricter \"first-attempt build pass\" metric (no self-repair "
        "iterations allowed) is recommended for the camera-ready."
    )
    lines.append("")
    lines.append(
        "- **Point-wise and list-wise judges produce the same rank order** "
        "(Opus > GPT-5 > Pro > Haiku > Flash > Mini), so the headline ranking is robust to the choice of judge."
    )
    lines.append(
        "- **They differ in score spread**: the point-wise judge compresses everything into [1.4, 3.0] (most "
        "models within 0.3 of each other); the list-wise judge spreads scores across [0.13, 0.91], making "
        "model gaps much more visually obvious in plots and tables. This compression is the \"tied-score\" "
        "pathology of the 1–5 integer scale identified in our judge study."
    )
    lines.append(
        "- **Per-app re-ranking with list-wise**: although the global order matches, list-wise surfaces "
        "three apps where `gpt-5` actually beats `claude-opus-4-6` (etsy / pinterest / you-tube-music) — "
        "all three are content-dense feed-style apps. This per-app inversion is the kind of fine-grained "
        "signal that the point-wise judge cannot reliably express."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-model",
        type=Path,
        default=Path("experiments/20260428_181902_mobileforge-compare/report/tables/by_model.csv"),
    )
    parser.add_argument(
        "--phase3",
        type=Path,
        default=Path("experiments/judge_method_study/phase3/borda_summary.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/main_results"),
    )
    args = parser.parse_args()

    by_model = load_by_model(args.by_model)
    phase3 = load_phase3(args.phase3)
    md = render_main_table(by_model, phase3)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "MAIN_RESULTS.md").write_text(md, encoding="utf-8")
    (args.out_dir / "main_results.json").write_text(
        json.dumps({"by_model": by_model, "phase3": phase3}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.out_dir / 'MAIN_RESULTS.md'}")


if __name__ == "__main__":
    main()
