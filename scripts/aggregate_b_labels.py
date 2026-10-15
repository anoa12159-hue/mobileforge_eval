"""
Aggregate user-provided B-bucket labels and emit a sub-bucket distribution.

Inputs:
  - experiments/failures/b_bucket_sample.json     (frozen sample list)
  - User-provided JSON exported from b_bucket_label.html (default location:
    experiments/failures/b_bucket_labels.json)

Outputs:
  - experiments/failures/b_bucket_breakdown.md
  - experiments/failures/b_bucket_breakdown.json   (per-record + aggregate)

Reports:
  - per-bucket (B1/B2/B3/SKIP) totals and shares
  - per-model x sub-bucket matrix
  - per-type  x sub-bucket matrix
  - top apps per sub-bucket
  - extrapolated estimates for the full 633 B-bucket population, with
    Wilson 95% confidence intervals.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def load_user_labels(path: Path) -> dict[str, str]:
    """Returns map (app::model::test_id -> label)."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for r in data.get("records", []):
        if not r.get("label"):
            continue
        out[f"{r['app']}::{r['model']}::{r['test_id']}"] = r["label"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=Path("experiments/failures/b_bucket_sample.json"))
    parser.add_argument("--labels", type=Path, default=Path("experiments/failures/b_bucket_labels.json"))
    parser.add_argument("--taxonomy", type=Path, default=Path("experiments/failures/taxonomy.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/failures"))
    args = parser.parse_args()

    sample = json.loads(args.sample.read_text(encoding="utf-8"))["samples"]
    user_labels = load_user_labels(args.labels)
    tax = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    b_total = sum(1 for r in tax["records"] if r["bucket"] == "B_NO_OP_CLICK")
    b_clickable = sum(
        1 for r in tax["records"]
        if r["bucket"] == "B_NO_OP_CLICK" and r.get("type") in ("tab_navigation", "parent_child_navigation")
    )

    enriched: list[dict[str, Any]] = []
    for s in sample:
        key = f"{s['app']}::{s['model']}::{s['test_id']}"
        enriched.append({**s, "label": user_labels.get(key)})

    n_total = len(enriched)
    n_labelled = sum(1 for r in enriched if r["label"])
    counts = Counter(r["label"] for r in enriched if r["label"])

    by_model: dict[str, Counter] = defaultdict(Counter)
    by_type: dict[str, Counter] = defaultdict(Counter)
    by_app: dict[str, Counter] = defaultdict(Counter)
    for r in enriched:
        if not r["label"]:
            continue
        by_model[r["label"]][r["model"]] += 1
        by_type[r["label"]][r.get("type", "?")] += 1
        by_app[r["label"]][r["app"]] += 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "b_bucket_breakdown.json").write_text(
        json.dumps({
            "n_total": n_total,
            "n_labelled": n_labelled,
            "counts": dict(counts),
            "per_record": enriched,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# B-bucket sub-classification: B1 / B2 / B3 / SKIP")
    lines.append("")
    lines.append(f"> Stratified sample of **{n_total}** B_NO_OP_CLICK records (out of {b_clickable} click-based "
                 f"B-records, {b_total} total B-records). Labelled by hand using the HTML labeller. "
                 f"Currently **{n_labelled}** of {n_total} labelled.")
    lines.append("")
    if n_labelled == 0:
        lines.append("> No labels imported yet. Run the labeller (`b_bucket_label.html`), export the JSON, "
                     "place it at `experiments/failures/b_bucket_labels.json`, and re-run this script.")
        (args.out_dir / "b_bucket_breakdown.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {args.out_dir / 'b_bucket_breakdown.md'} (no labels imported yet).")
        return

    lines.append("## 1. Sub-bucket counts on the sample")
    lines.append("")
    lines.append("| Sub-bucket | Definition | Count | Share of labelled |")
    lines.append("|---|---|---:|---:|")
    defs = {
        "B1": "Non-interactive element — visual element present but no onClick / `<Link>`",
        "B2": "Stub page — from-page is a 'Coming soon' placeholder; nothing functional",
        "B3": "Same-page section — to-page content rendered inline on from-page (different IA choice)",
        "B4": "Wrong page at expected route — from-page render does not match from-page reference (and is not a stub)",
        "SKIP": "Cannot tell from the screenshots alone",
    }
    for sb in ("B1", "B2", "B3", "B4", "SKIP"):
        c = counts.get(sb, 0)
        share = c / n_labelled * 100
        lines.append(f"| `{sb}` | {defs[sb]} | {c} | {share:.1f}% |")
    lines.append("")

    lines.append("## 2. Extrapolation to the full B population")
    lines.append("")
    lines.append(f"Wilson 95% CIs, scaled to the **{b_clickable} click-based B records** (and the **{b_total} total B records**):")
    lines.append("")
    lines.append("| Sub-bucket | Sample share | 95% CI | Estimated count (click-based) | Estimated count (total B) |")
    lines.append("|---|---:|---:|---:|---:|")
    for sb in ("B1", "B2", "B3", "B4", "SKIP"):
        c = counts.get(sb, 0)
        p, lo, hi = wilson_ci(c, n_labelled)
        est_clk = int(round(p * b_clickable))
        est_clk_lo = int(round(lo * b_clickable))
        est_clk_hi = int(round(hi * b_clickable))
        est_tot = int(round(p * b_total))
        est_tot_lo = int(round(lo * b_total))
        est_tot_hi = int(round(hi * b_total))
        lines.append(
            f"| `{sb}` | {p*100:.1f}% | [{lo*100:.1f}%, {hi*100:.1f}%] | "
            f"{est_clk} [{est_clk_lo}, {est_clk_hi}] | {est_tot} [{est_tot_lo}, {est_tot_hi}] |"
        )
    lines.append("")
    lines.append("Note: \"total B\" extrapolation assumes the page_exists slice (84 records) follows the same "
                 "distribution; this is conservative because page_exists failures are dominated by routing issues "
                 "rather than B1/B2/B3 patterns. Use the click-based estimate as the headline figure.")
    lines.append("")

    lines.append("## 3. Per-model sub-bucket distribution (sample counts)")
    lines.append("")
    models = sorted({r["model"] for r in enriched if r["label"]})
    lines.append("| Sub-bucket | " + " | ".join(f"`{m}`" for m in models) + " | Total |")
    lines.append("|---|" + "---|" * (len(models) + 1))
    for sb in ("B1", "B2", "B3", "B4", "SKIP"):
        cells = [str(by_model[sb].get(m, 0)) for m in models]
        lines.append(f"| `{sb}` | " + " | ".join(cells) + f" | **{sum(by_model[sb].values())}** |")
    lines.append("")

    lines.append("## 4. Per-scenario-type sub-bucket distribution (sample counts)")
    lines.append("")
    types = ["tab_navigation", "parent_child_navigation"]
    lines.append("| Sub-bucket | " + " | ".join(f"`{t}`" for t in types) + " | Total |")
    lines.append("|---|" + "---|" * (len(types) + 1))
    for sb in ("B1", "B2", "B3", "B4", "SKIP"):
        cells = [str(by_type[sb].get(t, 0)) for t in types]
        lines.append(f"| `{sb}` | " + " | ".join(cells) + f" | **{sum(by_type[sb].values())}** |")
    lines.append("")

    lines.append("## 5. Top affected apps per sub-bucket (in sample)")
    lines.append("")
    for sb in ("B1", "B2", "B3", "B4"):
        if not by_app[sb]:
            continue
        top = by_app[sb].most_common(5)
        lines.append(f"- **`{sb}`**: " + ", ".join(f"`{a}`={c}" for a, c in top))
    lines.append("")

    (args.out_dir / "b_bucket_breakdown.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out_dir / 'b_bucket_breakdown.md'} ({n_labelled}/{n_total} labelled).")


if __name__ == "__main__":
    main()
