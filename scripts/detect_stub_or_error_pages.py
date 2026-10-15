"""
Heuristic detector for stub / runtime-error pages
=================================================

Reads `experiments/failures/taxonomy.json`, finds all records whose
`screenshot_after` exists, and computes two cheap visual signals:

- **non_white_ratio**: fraction of pixels whose lightness is < 240 (i.e. not
  the page background). Stub pages and React-Router error pages have very low
  values (typically < 0.05).
- **distinct_colors_log**: log10 of the number of unique RGB triplets,
  approximating visual complexity.

Records with `non_white_ratio < 0.05` are flagged as **likely STUB_OR_ERROR**.

Outputs:
  experiments/failures/stub_or_error_candidates.csv  (sorted by ratio)
  experiments/failures/stub_or_error_summary.md       (counts per bucket × model)
  experiments/failures/sample_images/STUB_OR_ERROR/   (top 10 symlinks for inspection)

Read-only on user data.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


def visual_signals(path: Path, downsample: int = 4) -> tuple[float, float, int, int]:
    """Return (non_white_ratio, log10_distinct_colors, w, h)."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if downsample > 1:
            im = im.resize((max(1, w // downsample), max(1, h // downsample)))
        pixels = list(im.getdata())
        total = len(pixels)
        non_white = sum(1 for r, g, b in pixels if min(r, g, b) < 240)
        distinct = len(set(pixels))
        import math
        return non_white / total, math.log10(max(1, distinct)), w, h


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path, default=Path("experiments/failures/taxonomy.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/failures"))
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="non_white_ratio below this is flagged as stub/error.")
    parser.add_argument("--downsample", type=int, default=4)
    parser.add_argument("--max-symlinks", type=int, default=15)
    args = parser.parse_args()

    data = json.load(args.taxonomy.open("r", encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for r in data["records"]:
        sa = r.get("screenshot_after")
        if not sa or not Path(sa).exists():
            continue
        try:
            ratio, complexity, w, h = visual_signals(Path(sa), downsample=args.downsample)
        except Exception as e:
            continue
        rows.append({
            "app": r["app"],
            "model": r["model"],
            "test_id": r["test_id"],
            "type": r.get("type"),
            "bucket": r["bucket"],
            "non_white_ratio": ratio,
            "distinct_colors_log": complexity,
            "image_w": w,
            "image_h": h,
            "screenshot": sa,
        })

    rows.sort(key=lambda d: d["non_white_ratio"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "stub_or_error_candidates.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    flagged = [r for r in rows if r["non_white_ratio"] < args.threshold]

    sym_dir = args.out_dir / "sample_images" / "STUB_OR_ERROR"
    sym_dir.mkdir(parents=True, exist_ok=True)
    for r in flagged[: args.max_symlinks]:
        dst = sym_dir / f"{r['app']}__{r['model']}__{r['test_id']}.png"
        if dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(os.path.abspath(r["screenshot"]), dst)
        except OSError:
            pass

    by_bucket: Counter[str] = Counter(r["bucket"] for r in flagged)
    by_model_bucket: dict[str, Counter] = defaultdict(Counter)
    for r in flagged:
        by_model_bucket[r["bucket"]][r["model"]] += 1

    md_lines: list[str] = []
    md_lines.append("# Heuristic stub / runtime-error detection")
    md_lines.append("")
    md_lines.append(f"> Threshold: `non_white_ratio < {args.threshold}` (background pixels dominate). "
                    f"This cheaply catches React-Router 404 pages, stub-page placeholders, and blank pages "
                    f"that the rule-based taxonomy could not distinguish from low-fidelity but valid pages.")
    md_lines.append("")
    md_lines.append(f"Total screenshots scanned: **{len(rows)}**")
    md_lines.append(f"Total flagged (likely stub or runtime-error): **{len(flagged)}** ({len(flagged)/max(1,len(rows))*100:.1f}%)")
    md_lines.append("")
    md_lines.append("## Flagged counts per current taxonomy bucket")
    md_lines.append("")
    md_lines.append("| Bucket | Flagged | Total in bucket | Share |")
    md_lines.append("|---|---:|---:|---:|")
    bucket_totals = Counter(r["bucket"] for r in rows)
    for b, c in by_bucket.most_common():
        md_lines.append(f"| `{b}` | {c} | {bucket_totals[b]} | {c/max(1,bucket_totals[b])*100:.1f}% |")
    md_lines.append("")

    md_lines.append("## Per-model flagged counts (within each bucket)")
    md_lines.append("")
    models = sorted({r["model"] for r in flagged})
    md_lines.append("| Bucket | " + " | ".join(f"`{m}`" for m in models) + " | Total |")
    md_lines.append("|---|" + "---|" * (len(models) + 1))
    for b in by_model_bucket:
        cells = [str(by_model_bucket[b].get(m, 0)) for m in models]
        md_lines.append(f"| `{b}` | " + " | ".join(cells) + f" | **{sum(by_model_bucket[b].values())}** |")
    md_lines.append("")

    md_lines.append(f"## Top 10 most-blank flagged screenshots")
    md_lines.append("")
    md_lines.append("| App | Model | Test | Bucket | non_white | distinct_colors_log |")
    md_lines.append("|---|---|---|---|---:|---:|")
    for r in flagged[:10]:
        md_lines.append(
            f"| `{r['app']}` | `{r['model']}` | `{r['test_id']}` | `{r['bucket']}` | "
            f"{r['non_white_ratio']*100:.2f}% | {r['distinct_colors_log']:.2f} |"
        )
    md_lines.append("")
    md_lines.append(f"Symlinks for the top {args.max_symlinks} flagged are under "
                    f"`experiments/failures/sample_images/STUB_OR_ERROR/`.")
    md_lines.append("")

    (args.out_dir / "stub_or_error_summary.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Scanned {len(rows)} screenshots, flagged {len(flagged)} ({len(flagged)/max(1,len(rows))*100:.1f}%). "
          f"Wrote {csv_path}, {args.out_dir / 'stub_or_error_summary.md'}, and "
          f"{min(args.max_symlinks, len(flagged))} symlinks.")


if __name__ == "__main__":
    main()
