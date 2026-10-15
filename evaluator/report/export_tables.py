"""
CSV export — writes flattened CSV tables for pandas / Excel / LaTeX consumption.

Generates: all_runs.csv, by_policy.csv, by_model.csv, by_app.csv, cross_model_policy.csv.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ALL_RUNS_COLUMNS = [
    "run_id", "app", "model", "policy", "suffix",
    "input_tokens", "output_tokens", "total_tokens",
    "llm_calls", "llm_time_sec", "iteration_count", "estimated_cost_usd",
    "build_passed", "tsc_passed", "vite_passed",
    "route_coverage", "page_count", "route_count",
    "nav_total", "nav_passed", "nav_pass_rate",
    "visual_avg_before", "visual_avg_after",
    "total_loc", "file_count", "shared_components",
    "reuse_rate", "dead_component_rate", "color_consistency",
    "phase_count", "verify_fail_count", "write_granularity",
    "status",
]


def export_tables(experiment_dir: str, aggregated: dict[str, Any]) -> list[str]:
    """Write CSV files to report/tables/ and return their paths."""
    ed = Path(experiment_dir)
    tables_dir = ed / "report" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    # all_runs.csv
    all_runs = aggregated.get("all_runs", [])
    if all_runs:
        p = _write_csv(tables_dir / "all_runs.csv", _ALL_RUNS_COLUMNS, all_runs)
        written.append(str(p))

    # by_policy.csv
    by_policy = aggregated.get("by_policy", {})
    if by_policy:
        p = _write_group_csv(tables_dir / "by_policy.csv", "policy", by_policy)
        written.append(str(p))

    # by_model.csv
    by_model = aggregated.get("by_model", {})
    if by_model:
        p = _write_group_csv(tables_dir / "by_model.csv", "model", by_model)
        written.append(str(p))

    # by_app.csv
    by_app = aggregated.get("by_app", {})
    if by_app:
        p = _write_group_csv(tables_dir / "by_app.csv", "app", by_app)
        written.append(str(p))

    # cross_model_policy.csv
    cross = aggregated.get("cross_table", {}).get("model_x_policy", [])
    if cross:
        cols = list(cross[0].keys()) if cross else []
        p = _write_csv(tables_dir / "cross_model_policy.csv", cols, cross)
        written.append(str(p))

    logger.info("Exported %d CSV tables to %s", len(written), tables_dir)
    return written


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> Path:
    """Write a list of dicts as CSV with specified column order."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _write_group_csv(
    path: Path,
    group_key: str,
    grouped: dict[str, dict[str, Any]],
) -> Path:
    """Write a grouped aggregation dict as CSV."""
    if not grouped:
        return path

    # Determine columns from the first group
    sample = next(iter(grouped.values()))
    columns = [group_key] + sorted(sample.keys())

    rows: list[dict[str, Any]] = []
    for key, agg in sorted(grouped.items()):
        row = {group_key: key}
        row.update(agg)
        rows.append(row)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return path
