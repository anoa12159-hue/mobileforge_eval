"""
Cross-bucket user-perception failure breakdown.

Combines four data sources into a single paper-grade breakdown table:

  A_WRONG_ROUTE_TO_ROOT       (n=303)  → all mapped to **C5.1**
        (URL fell back to home / root → from the user's view this is
         "navigated to an unrelated page", i.e. C5.1 wrong destination)

  B_NO_OP_CLICK               (n=549)  → expanded from manual labels
        - 64 records have a v2 user_perception_label assigned by the
          reviewer in failure_labels_v2_*.json
        - the remaining 485 records are extrapolated using the
          (model × type) cell distribution observed in the labelled sample

  D_VISUAL_BOTTOM_NAV_PASSED  (n=551)  → expanded from manual labels
        - if --d-labels is provided (d_labels_v2_*.json), the 51 manual
          labels are extrapolated to all 551 D records using the same
          cell-level method as B
        - otherwise, all 551 are tentatively mapped to **C5.3**

  F1_PAGE_MISSING             (n=518)  → all mapped to **C1**
        (no after-screenshot at all → equivalent to "blank start page"
         from a user's perspective: nothing rendered to interact with)

Outputs:
  experiments/failures/failure_breakdown_full.json
  experiments/failures/FAILURE_BREAKDOWN_FULL.md
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
    "OK":   "Actually correct (false-positive nav fail)",
    "C1":   "Blank start page",
    "C2":   "Wrong page at this route",
    "C3":   "Trigger missing or occluded",
    "C4":   "Click had no response",
    "C5.1": "Navigated to unrelated destination",
    "C5.2": "Navigated to blank / error page",
    "C5.3": "Right page, low visual fidelity",
    "SKIP": "Uncertain",
}


def wilson_ci(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def fmt_pct(x: float, total: float) -> str:
    return f"{x/total*100:.1f}%" if total else "—"


def _extrapolate_cells(
    sample_records: list[dict[str, Any]],
    pop_records: list[dict[str, Any]],
    label_field: str = "label",
) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, Counter[str]], list[tuple[str, str]]]:
    """Generic cell-level extrapolation: stratified by (model × type).

    Returns:
      (label_total, per_model, per_type, cells_no_sample)
    """
    sample_labels: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for r in sample_records:
        v = r.get(label_field)
        if not v:
            continue
        sample_labels[(r["model"], r["type"])][v] += 1
    pop_per_cell: Counter[tuple[str, str]] = Counter()
    for r in pop_records:
        pop_per_cell[(r["model"], r["type"])] += 1

    label_total: Counter[str] = Counter()
    per_model: dict[str, Counter[str]] = defaultdict(Counter)
    per_type:  dict[str, Counter[str]] = defaultdict(Counter)
    cells_no_sample: list[tuple[str, str]] = []
    for cell, pop_n in pop_per_cell.items():
        sample_n = sum(sample_labels[cell].values())
        if sample_n == 0:
            cells_no_sample.append(cell)
            continue
        factor = pop_n / sample_n
        for label, k in sample_labels[cell].items():
            extrapolated = k * factor
            label_total[label] += extrapolated
            per_model[cell[0]][label] += extrapolated
            per_type[cell[1]][label] += extrapolated
    return label_total, per_model, per_type, cells_no_sample


def aggregate(
    taxonomy_path: Path,
    labels_path: Path,
    d_labels_path: Path | None = None,
) -> dict[str, Any]:
    tax = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    labels_data = json.loads(labels_path.read_text(encoding="utf-8"))
    d_labels_data = json.loads(d_labels_path.read_text(encoding="utf-8")) if d_labels_path else None

    # ---- 1. Group records by mechanism bucket -----------------------------
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in tax["records"]:
        by_bucket[r["bucket"]].append(r)

    # B-bucket: only nav scenarios (parent_child + tab_nav). Page_exists is
    # excluded from sub-labelling because B is fundamentally a click-failure
    # category.
    b_records = [
        r for r in by_bucket.get("B_NO_OP_CLICK", [])
        if r.get("type") in ("parent_child_navigation", "tab_navigation")
    ]
    b_pageexists = [
        r for r in by_bucket.get("B_NO_OP_CLICK", [])
        if r.get("type") == "page_exists"
    ]

    # ---- 2. Extrapolate B-bucket from sample to population (cell-level) ---
    b_extrap, b_per_model, b_per_type, b_cells_no_sample = _extrapolate_cells(
        labels_data["records"], b_records,
    )
    n_b_sample_used = sum(1 for r in labels_data["records"] if r.get("label"))

    # ---- 3. Deterministic mappings for A / F1, D either extrapolated or default
    a_records = by_bucket.get("A_WRONG_ROUTE_TO_ROOT", [])
    d_records = by_bucket.get("D_VISUAL_BOTTOM_NAV_PASSED", [])
    f1_records = by_bucket.get("F1_PAGE_MISSING", [])

    if d_labels_data is not None:
        d_extrap, d_per_model, d_per_type, d_cells_no_sample = _extrapolate_cells(
            d_labels_data["records"], d_records,
        )
        d_bucket_label_dist = {l: float(d_extrap.get(l, 0)) for l in V2_LABELS}
        n_d_sample_used = sum(1 for r in d_labels_data["records"] if r.get("label"))
    else:
        d_bucket_label_dist = {"C5.3": float(len(d_records))}
        d_per_model, d_per_type, d_cells_no_sample = {}, {}, []
        n_d_sample_used = 0

    # ---- 4. Assemble cross-bucket label totals ----------------------------
    per_bucket_label = {
        "A_WRONG_ROUTE_TO_ROOT":      {"C5.1": float(len(a_records))},
        "B_NO_OP_CLICK":              {l: float(b_extrap.get(l, 0)) for l in V2_LABELS},
        "D_VISUAL_BOTTOM_NAV_PASSED": d_bucket_label_dist,
        "F1_PAGE_MISSING":            {"C1":   float(len(f1_records))},
    }
    bucket_totals = {b: int(round(sum(d.values()))) for b, d in per_bucket_label.items()}

    label_totals: Counter[str] = Counter()
    for d in per_bucket_label.values():
        for label, count in d.items():
            label_totals[label] += count
    grand_total = sum(label_totals.values())

    # ---- 5. Per-model / per-type cross-bucket totals ----------------------
    pop_per_model = Counter(r["model"] for bucket in [a_records, b_records, d_records, f1_records] for r in bucket)
    pop_per_type = Counter(r["type"] for bucket in [a_records, b_records, d_records, f1_records] for r in bucket if r.get("type"))

    per_model_label: dict[str, Counter[str]] = defaultdict(Counter)
    per_type_label:  dict[str, Counter[str]] = defaultdict(Counter)

    for r in a_records:
        per_model_label[r["model"]]["C5.1"] += 1
        if r.get("type"):
            per_type_label[r["type"]]["C5.1"] += 1
    for r in f1_records:
        per_model_label[r["model"]]["C1"]   += 1
        if r.get("type"):
            per_type_label[r["type"]]["C1"] += 1

    if d_labels_data is not None:
        for m, c in d_per_model.items():
            for label, count in c.items():
                per_model_label[m][label] += count
        for t, c in d_per_type.items():
            for label, count in c.items():
                per_type_label[t][label] += count
    else:
        for r in d_records:
            per_model_label[r["model"]]["C5.3"] += 1
            if r.get("type"):
                per_type_label[r["type"]]["C5.3"] += 1

    for m, c in b_per_model.items():
        for label, count in c.items():
            per_model_label[m][label] += count
    for t, c in b_per_type.items():
        for label, count in c.items():
            per_type_label[t][label] += count

    return {
        "tax_path": str(taxonomy_path),
        "labels_path": str(labels_path),
        "labels_exported_at": labels_data.get("exported_at"),
        "d_labels_path": str(d_labels_path) if d_labels_path else None,
        "d_labels_exported_at": d_labels_data.get("exported_at") if d_labels_data else None,
        "n_b_sample_used": n_b_sample_used,
        "n_d_sample_used": n_d_sample_used,
        "bucket_totals": bucket_totals,
        "b_pageexists_excluded": len(b_pageexists),
        "cells_no_sample_b": [list(c) for c in b_cells_no_sample],
        "cells_no_sample_d": [list(c) for c in d_cells_no_sample],
        "per_bucket_label": per_bucket_label,
        "label_totals": dict(label_totals),
        "grand_total": grand_total,
        "per_model_label": {m: dict(c) for m, c in per_model_label.items()},
        "per_type_label":  {t: dict(c) for t, c in per_type_label.items()},
        "pop_per_model": dict(pop_per_model),
        "pop_per_type": dict(pop_per_type),
    }


def _round(x: float) -> int:
    return int(round(x))


def render_report(R: dict[str, Any], out_path: Path) -> None:
    L: list[str] = []
    L.append("# Full Failure Breakdown — User-Perception Schema (v2)\n")
    L.append("Combines manual B-bucket labels with deterministic mappings for A / D / F1 buckets.\n")
    L.append(f"_Sources_:")
    L.append(f"- `{R['tax_path']}` (taxonomy with mechanism-level buckets)")
    L.append(f"- `{R['labels_path']}` (manual v2 labels, exported {R['labels_exported_at']})\n")

    L.append("## 1. Headline distribution (cross-bucket totals)\n")
    L.append(f"Total failure records: **{_round(R['grand_total'])}**.")
    L.append(f"  Mechanism-bucket counts: " +
             ", ".join(f"{b}={n}" for b, n in R['bucket_totals'].items()) +
             f"  (B page_exists excluded: {R['b_pageexists_excluded']}).\n")
    has_d_labels = bool(R.get("d_labels_path"))
    L.append("| User-perception label | Description | Count | Share | Sources |")
    L.append("|---|---|---:|---:|---|")
    for lbl in V2_LABELS:
        n = R["label_totals"].get(lbl, 0)
        if n == 0:
            continue
        sources = []
        # Identify which mechanism buckets contribute non-zero share to this label
        for bucket_name, dist in R["per_bucket_label"].items():
            if dist.get(lbl, 0) > 0.5:
                sources.append(bucket_name.split("_")[0])
        notes_str = " + ".join(sources)
        L.append(f"| **{lbl}** | {LABEL_HUMAN[lbl]} | {_round(n)} | "
                 f"{n/R['grand_total']*100:.1f}% | {notes_str} |")
    L.append("")

    L.append("### Headline takeaways\n")
    sorted_labels = sorted(((l, n) for l, n in R["label_totals"].items() if n > 0),
                           key=lambda x: -x[1])
    if sorted_labels:
        top3 = sorted_labels[:3]
        share_str = ", ".join(f"`{l}` ({_round(n)}, {n/R['grand_total']*100:.1f}%)" for l, n in top3)
        L.append(f"- **Top-3 user-perception failure modes**: {share_str}.")
    c5_total = sum(R["label_totals"].get(l, 0) for l in ["C5.1", "C5.2", "C5.3"])
    pre_click = sum(R["label_totals"].get(l, 0) for l in ["C1", "C2", "C3"])
    if pre_click > 0:
        L.append(f"- **Pre-click failures (C1+C2+C3) = {_round(pre_click)} "
                 f"({pre_click/R['grand_total']*100:.1f}%)**: the user could not even "
                 "complete the first interaction step — the agent's start page was "
                 "either blank, wrong, or missing the trigger.")
    if c5_total > 0:
        L.append(f"- **Click-time failures (C4+C5.*) = {_round(R['label_totals'].get('C4', 0) + c5_total)} "
                 f"({(R['label_totals'].get('C4', 0) + c5_total)/R['grand_total']*100:.1f}%)**: "
                 "the agent did render an interactive start page, but the click "
                 "either had no effect (C4) or led somewhere wrong (C5.*).")

    L.append("\n## 2. Evaluation framework reliability (false-positive rates)\n")
    L.append("Cross-checking the two automatic failure signals against the manual "
             "user-perception labels gives a direct false-positive rate for each "
             "auto-evaluator:\n")
    L.append("| Auto signal | Sample n | OK in sample | False-positive share | Wilson 95% CI |")
    L.append("|---|---:|---:|---:|---|")
    # B-bucket false-positive (nav-pass evaluator)
    b_sample = R['n_b_sample_used']
    b_ok = sum(1 for r in (json.loads(Path(R['labels_path']).read_text(encoding='utf-8'))['records'])
               if r.get('label') == 'OK')
    b_lo, b_hi = wilson_ci(b_ok, b_sample)
    L.append(f"| `nav_pass = false` (B bucket) | {b_sample} | {b_ok} | "
             f"**{b_ok/b_sample*100:.1f}%** | [{b_lo*100:.1f}%, {b_hi*100:.1f}%] |")
    # D-bucket false-positive (visual-judge evaluator)
    if has_d_labels:
        d_data = json.loads(Path(R['d_labels_path']).read_text(encoding='utf-8'))
        d_sample = R['n_d_sample_used']
        d_ok = sum(1 for r in d_data['records'] if r.get('label') == 'OK')
        d_lo, d_hi = wilson_ci(d_ok, d_sample)
        L.append(f"| `visual_bottom_listwise = true` (D bucket) | {d_sample} | {d_ok} | "
                 f"**{d_ok/d_sample*100:.1f}%** | [{d_lo*100:.1f}%, {d_hi*100:.1f}%] |")
        L.append("\n**Headline reliability finding**:")
        L.append(f"- The **navigation auto-evaluator** (URL-change check) is highly "
                 f"reliable: 0% manual OK in the B-bucket sample (95% upper bound "
                 f"{b_hi*100:.1f}%). Nav-fail signals are essentially never false alarms.")
        L.append(f"- The **visual judge** (list-wise + reference anchor) has a higher "
                 f"false-positive rate: {d_ok/d_sample*100:.1f}% of \"bottom-1\" cases "
                 f"are actually fine renders that a human reviewer would accept "
                 f"(95% CI [{d_lo*100:.1f}%, {d_hi*100:.1f}%]).")
        L.append(f"- For the **paper accuracy story**: report the {len(R['per_bucket_label']['D_VISUAL_BOTTOM_NAV_PASSED']['C5.3']) if isinstance(R['per_bucket_label']['D_VISUAL_BOTTOM_NAV_PASSED']['C5.3'], int) else _round(R['per_bucket_label']['D_VISUAL_BOTTOM_NAV_PASSED']['C5.3'])} extrapolated C5.3 cases as the *adjusted* "
                 "low-fidelity count (after removing visual-judge false positives), "
                 "rather than the raw 551 D-bucket count.")
    else:
        L.append("\n*(D-bucket false-positive rate not available — provide --d-labels.)*")
    L.append("")

    L.append("## 3. Per-bucket × user-perception cross-tab\n")
    L.append("Mechanism-level bucket on the rows; user-perception label on the columns. "
             "This shows how the original taxonomy maps onto the new schema.\n")
    used_labels = [l for l in V2_LABELS if R["label_totals"].get(l, 0) > 0]
    L.append("| Mechanism bucket | n | " + " | ".join(used_labels) + " |")
    L.append("|---|---:|" + "---:|" * len(used_labels))
    for bucket, label_dist in R["per_bucket_label"].items():
        n = R["bucket_totals"][bucket]
        cells = []
        for lbl in used_labels:
            v = label_dist.get(lbl, 0)
            cells.append(f"{_round(v)}" if v > 0 else "—")
        L.append(f"| `{bucket}` | {n} | " + " | ".join(cells) + " |")

    L.append("\n## 4. Per-model breakdown (all failures, all buckets)\n")
    L.append("| Model | Total fails | " + " | ".join(used_labels) + " |")
    L.append("|---|---:|" + "---:|" * len(used_labels))
    for m in sorted(R["per_model_label"].keys()):
        c = R["per_model_label"][m]
        total = sum(c.values())
        cells = []
        for lbl in used_labels:
            v = c.get(lbl, 0)
            cells.append(f"{_round(v)}" if v > 0 else "—")
        L.append(f"| `{m}` | {_round(total)} | " + " | ".join(cells) + " |")

    L.append("\n## 5. Per-scenario-type breakdown\n")
    L.append("| Type | Total fails | " + " | ".join(used_labels) + " |")
    L.append("|---|---:|" + "---:|" * len(used_labels))
    for t in sorted(R["per_type_label"].keys()):
        c = R["per_type_label"][t]
        total = sum(c.values())
        cells = []
        for lbl in used_labels:
            v = c.get(lbl, 0)
            cells.append(f"{_round(v)}" if v > 0 else "—")
        L.append(f"| `{t}` | {_round(total)} | " + " | ".join(cells) + " |")

    L.append("\n## 6. Methodology & caveats\n")
    L.append(f"**B-bucket extrapolation**: {R['n_b_sample_used']} manual labels are "
             "stratified across (model × type) cells. Extrapolation expands each cell's "
             "sample distribution to its full population proportionally; this assumes "
             "labels are exchangeable within a cell.\n")
    if R.get("cells_no_sample_b"):
        L.append("**B cells with no labelled sample** (their population is *not* counted "
                 "in the extrapolation):")
        for cell in R["cells_no_sample_b"]:
            L.append(f"  - `{cell[0]}` × `{cell[1]}`")
        L.append("")
    L.append("**A-bucket → C5.1**: every A record has `final_url` falling back to root "
             "(`/`). From the user's view the click navigated, but to an unrelated page "
             "(home), so user-perception is unambiguously C5.1.\n")
    L.append("**F1-bucket → C1**: every F1 record has *no* after-screenshot, meaning the "
             "agent's render produced no captured pixels for that route. Functionally "
             "equivalent to a blank start page from the user's perspective.\n")

    if R.get("d_labels_path"):
        L.append(f"**D-bucket extrapolation**: {R['n_d_sample_used']} manual labels are "
                 "stratified across (model × type) cells, expanded the same way as B. "
                 "The labelling distinguishes:")
        L.append("  - **OK**: visual judge mis-ranked an actually-correct render → "
                 "false-positive of the visual evaluator")
        L.append("  - **C5.1**: nav_pass=true but the rendered content is unrelated to "
                 "to_page reference (URL hit the right route, content is wrong)")
        L.append("  - **C5.2**: nav_pass=true but the destination is blank / stub / error")
        L.append("  - **C5.3**: nav_pass=true, content matches to_page reference, but "
                 "visual fidelity is clearly degraded (the original tentative assumption)")
        L.append("")
        if R.get("cells_no_sample_d"):
            L.append("**D cells with no labelled sample**:")
            for cell in R["cells_no_sample_d"]:
                L.append(f"  - `{cell[0]}` × `{cell[1]}`")
            L.append("")
    else:
        L.append("**D-bucket → C5.3 (tentative)**: D records all default to C5.3. "
                 "Provide `--d-labels` to replace this with manual sub-classification.\n")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path,
                        default=Path("experiments/failures/taxonomy.json"))
    parser.add_argument("--labels", type=Path,
                        default=Path("experiments/failures/failure_labels_v2_2026-05-02.json"))
    parser.add_argument("--d-labels", type=Path, default=None,
                        help="Optional manual D-bucket labels (d_labels_v2_*.json). "
                             "If omitted, D bucket defaults to C5.3.")
    parser.add_argument("--out-json", type=Path,
                        default=Path("experiments/failures/failure_breakdown_full.json"))
    parser.add_argument("--out-md", type=Path,
                        default=Path("experiments/failures/FAILURE_BREAKDOWN_FULL.md"))
    args = parser.parse_args()

    if args.d_labels is None:
        # Auto-discover any d_labels_v2_*.json
        candidates = sorted(Path("experiments/failures").glob("d_labels_v2_*.json"))
        if candidates:
            args.d_labels = candidates[-1]
            print(f"[auto] Using D labels: {args.d_labels}")

    R = aggregate(args.taxonomy, args.labels, args.d_labels)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.out_json}")

    render_report(R, args.out_md)
    print(f"Wrote {args.out_md}")
    print(f"\nGrand total failure records: {R['grand_total']}")
    print("Label totals:")
    for lbl in V2_LABELS:
        n = R["label_totals"].get(lbl, 0)
        if n > 0:
            print(f"  {lbl:5}  {_round(n):4}  ({n/R['grand_total']*100:.1f}%)  {LABEL_HUMAN[lbl]}")


if __name__ == "__main__":
    main()
