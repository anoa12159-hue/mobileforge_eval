"""
Aggregate v2 user-perception failure labels.

Inputs:
  experiments/failures/failure_labels_v2_*.json  (or path via --labels)
  experiments/failures/taxonomy.json              (full B-bucket population)

Outputs:
  experiments/failures/failure_observations.json    structured aggregate data
  experiments/failures/FAILURE_OBSERVATIONS_REPORT.md   markdown report
  experiments/failures/taxonomy.json                in-place: each labelled
        record gets a `user_perception_label` field

Computes:
  - Headline distribution over the 9 v2 labels
    (OK / C1 / C2 / C3 / C4 / C5.1 / C5.2 / C5.3 / SKIP)
  - Per-model breakdown (6 models × labels)
  - Per-type breakdown (parent_child_navigation / tab_navigation × labels)
  - Per-app frequency (descending)
  - Wilson 95% CI extrapolation from the 64-sample stratified pool to the
    full B-bucket population (n = 549, excluding page_exists)
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


V2_LABELS = ["OK", "C1", "C2", "C3", "C4", "C5.1", "C5.2", "C5.3", "SKIP"]

LABEL_HUMAN = {
    "OK":   "实际符合预期 (false-positive nav fail)",
    "C1":   "起始页空白",
    "C2":   "非预期的起始页 (路由映射错误)",
    "C3":   "起始页目标交互区域不可达 (缺失或被遮挡)",
    "C4":   "起始页有目标交互区域，但点击无反应",
    "C5.1": "跳转或弹出不相关的页面",
    "C5.2": "跳转空白页 / 报错页",
    "C5.3": "跳转页面视觉差距过大",
    "SKIP": "看不准",
}


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def fmt_pct_ci(k: int, n: int) -> str:
    if n == 0:
        return "—"
    lo, hi = wilson_ci(k, n)
    return f"{k/n*100:.1f}% [{lo*100:.1f}, {hi*100:.1f}]"


def aggregate(labels_path: Path, taxonomy_path: Path) -> dict[str, Any]:
    labels_data = json.loads(labels_path.read_text(encoding="utf-8"))
    records = labels_data["records"]

    sample_counts = Counter(r["label"] for r in records if r["label"])
    total_sample = sum(sample_counts.values())

    tax = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    pop = [
        r for r in tax["records"]
        if r["bucket"] == "B_NO_OP_CLICK"
        and r.get("type") in ("parent_child_navigation", "tab_navigation")
    ]
    pop_total = len(pop)
    pop_per_model = Counter(r["model"] for r in pop)
    pop_per_type = Counter(r["type"] for r in pop)

    per_model: dict[str, Counter[str]] = defaultdict(Counter)
    per_type:  dict[str, Counter[str]] = defaultdict(Counter)
    per_app:   Counter[str] = Counter()
    sample_per_model: Counter[str] = Counter()
    sample_per_type:  Counter[str] = Counter()
    for r in records:
        if not r["label"]:
            continue
        per_model[r["model"]][r["label"]] += 1
        per_type[r["type"]][r["label"]] += 1
        per_app[r["app"]] += 1
        sample_per_model[r["model"]] += 1
        sample_per_type[r["type"]] += 1

    headline = []
    for lbl in V2_LABELS:
        k = sample_counts.get(lbl, 0)
        lo, hi = wilson_ci(k, total_sample)
        headline.append({
            "label": lbl,
            "human": LABEL_HUMAN[lbl],
            "sample_count": k,
            "sample_share": k / total_sample if total_sample else 0,
            "ci95_lo": lo,
            "ci95_hi": hi,
            "extrapolated_count": round(k / total_sample * pop_total) if total_sample else 0,
        })

    return {
        "labels_path": str(labels_path),
        "labels_exported_at": labels_data.get("exported_at"),
        "n_sample": total_sample,
        "n_population_b_bucket": pop_total,
        "headline": headline,
        "per_model": {m: dict(c) for m, c in per_model.items()},
        "sample_per_model": dict(sample_per_model),
        "pop_per_model": dict(pop_per_model),
        "per_type":  {t: dict(c) for t, c in per_type.items()},
        "sample_per_type": dict(sample_per_type),
        "pop_per_type": dict(pop_per_type),
        "per_app": dict(per_app.most_common()),
    }


def render_report(result: dict[str, Any], out_path: Path) -> None:
    L: list[str] = []
    L.append("# B-bucket Failure Observation Report (v2 user-perception schema)\n")
    L.append(f"_Source labels_: `{result['labels_path']}` "
             f"(exported {result['labels_exported_at']})\n")
    L.append(f"_Sample_: **{result['n_sample']}** stratified records "
             f"out of **{result['n_population_b_bucket']}** total B-bucket records "
             f"(`parent_child_navigation` + `tab_navigation`).\n")

    L.append("## 1. Headline distribution\n")
    L.append("Wilson 95% CI on each share. The `extrapolated count` projects the "
             "sample share onto the full B-bucket population (n = "
             f"{result['n_population_b_bucket']}).\n")
    L.append("| Label | Meaning | Sample n | Share (95% CI) | Extrapolated count |")
    L.append("|---|---|---:|---|---:|")
    for h in result["headline"]:
        L.append(f"| **{h['label']}** | {h['human']} | {h['sample_count']} | "
                 f"{fmt_pct_ci(h['sample_count'], result['n_sample'])} | "
                 f"{h['extrapolated_count']} |")

    L.append("\n### Headline takeaways\n")
    by_share = sorted(result["headline"], key=lambda h: -h["sample_share"])
    top = [h for h in by_share if h["sample_count"] > 0][:3]
    if top:
        L.append("- **Most common failure modes**: " +
                 " ".join(f"`{h['label']}` ({h['sample_count']}/{result['n_sample']}, "
                          f"{h['sample_share']*100:.1f}%)" for h in top) + ".")
    ok = next(h for h in result["headline"] if h["label"] == "OK")
    if ok["sample_count"] == 0:
        L.append("- **No false-positive nav failures (OK = 0)**: every B-bucket "
                 "case in the sample really did fail from the user's perspective. "
                 "The auto-evaluator's `nav_pass` signal is *not* over-reporting "
                 "failures on this bucket.")
    else:
        L.append(f"- **OK = {ok['sample_count']}/{result['n_sample']} "
                 f"({ok['sample_share']*100:.1f}%)**: this fraction of "
                 "auto-flagged failures actually succeeded from the user's "
                 "perspective (e.g. the click opened an inline section that "
                 "matched the to_page reference). This is the false-positive "
                 "rate of the navigation auto-evaluator on B-bucket cases.")
    c5 = sum(h["sample_count"] for h in result["headline"] if h["label"].startswith("C5"))
    if c5 == 0:
        L.append("- **C5.* essentially absent in B**: this is expected — B-bucket "
                 "is defined as *URL did not change*, so the only way to land in "
                 "C5 (\"the screen changed but content was wrong\") is via inline "
                 "modal/drawer/inline expansion. Real navigation-mismatch "
                 "failures live in the **A** bucket (route fell back to root).")

    L.append("\n## 2. Per-model breakdown\n")
    L.append("Sample counts per model. Population n given for context.\n")
    models = sorted(result["per_model"].keys())
    header = "| Model | Sample n | Pop n | " + " | ".join(f"{l}" for l in V2_LABELS) + " |"
    sep    = "|---|---:|---:|" + "---:|" * len(V2_LABELS)
    L.append(header)
    L.append(sep)
    for m in models:
        cells = [str(result["per_model"][m].get(lbl, 0)) for lbl in V2_LABELS]
        L.append(f"| `{m}` | {result['sample_per_model'].get(m, 0)} "
                 f"| {result['pop_per_model'].get(m, 0)} | " + " | ".join(cells) + " |")

    L.append("\n### Per-model insights\n")
    for m in models:
        c = result["per_model"][m]
        n = result["sample_per_model"].get(m, 1)
        if n == 0:
            continue
        ranked = sorted(c.items(), key=lambda kv: -kv[1])
        if ranked:
            top_lbl, top_n = ranked[0]
            L.append(f"- `{m}` (n={n} sampled): top mode is "
                     f"**{top_lbl}** ({top_n}/{n}, {top_n/n*100:.0f}%) — "
                     f"_{LABEL_HUMAN[top_lbl]}_.")

    L.append("\n## 3. Per-scenario-type breakdown\n")
    L.append("| Type | Sample n | Pop n | " + " | ".join(V2_LABELS) + " |")
    L.append("|---|---:|---:|" + "---:|" * len(V2_LABELS))
    for t in sorted(result["per_type"].keys()):
        cells = [str(result["per_type"][t].get(lbl, 0)) for lbl in V2_LABELS]
        L.append(f"| `{t}` | {result['sample_per_type'].get(t, 0)} "
                 f"| {result['pop_per_type'].get(t, 0)} | " + " | ".join(cells) + " |")

    L.append("\n## 4. Per-app frequency (where failures concentrate)\n")
    L.append("Number of *sampled* failures per app (tabnav + parent_child). "
             "Only apps that contributed at least one sampled failure appear.\n")
    L.append("| App | # sampled failures |")
    L.append("|---|---:|")
    for app, n in result["per_app"].items():
        L.append(f"| `{app}` | {n} |")

    L.append("\n## 5. Caveats\n")
    L.append("- Wilson CIs are computed against the *sampled* B-bucket records. "
             "They assume the stratified-by-(model × type) sample is representative "
             "of the full B-bucket population, which holds because cells were "
             "sampled at uniform rate (≤ 6 per cell).")
    L.append("- The aggregate covers the **B bucket only** (URL-no-change failures). "
             "**A bucket** failures (URL fell back to root) and **D bucket** failures "
             "(URL navigated, but visual is bottom-1) are *not* relabelled here. "
             "Under the v2 schema:")
    L.append("  - A-bucket records map mechanically to **C5.1** (`navigated to "
             "an unrelated page`, specifically home). Use "
             "`scripts/classify_a_bucket.py` for the A1 vs A23 split.")
    L.append("  - D-bucket records map mechanically to **C5.3** (`right page, "
             "but visual is far off`); a small fraction may be **OK** if the "
             "to_page render is actually fine and the visual judge mis-ranked them.")
    L.append("  - F1-bucket records (no screenshot at all) map to **C1** "
             "(`blank start page`).")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")


def write_back_to_taxonomy(labels_path: Path, taxonomy_path: Path) -> int:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    label_map = {
        (r["app"], r["model"], r["test_id"]): r["label"]
        for r in labels["records"] if r["label"]
    }
    tax = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    n = 0
    for rec in tax["records"]:
        key = (rec.get("app"), rec.get("model"), rec.get("test_id"))
        if key in label_map:
            rec["user_perception_label"] = label_map[key]
            n += 1
    taxonomy_path.write_text(json.dumps(tax, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("experiments/failures/failure_labels_v2_2026-05-02.json"),
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("experiments/failures/taxonomy.json"),
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path("experiments/failures/failure_observations.json"),
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=Path("experiments/failures/FAILURE_OBSERVATIONS_REPORT.md"),
    )
    parser.add_argument(
        "--no-write-back",
        action="store_true",
        help="Skip in-place updating taxonomy.json with user_perception_label.",
    )
    args = parser.parse_args()

    result = aggregate(args.labels, args.taxonomy)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.out_json}")

    render_report(result, args.out_md)
    print(f"Wrote {args.out_md}")

    if not args.no_write_back:
        n = write_back_to_taxonomy(args.labels, args.taxonomy)
        print(f"Annotated {n} records in {args.taxonomy} with `user_perception_label`.")


if __name__ == "__main__":
    main()
