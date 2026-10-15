"""
Result aggregator — scans all runs in an experiment, collects eval results,
and aggregates by model / policy / app.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluator.utils import read_json

logger = logging.getLogger(__name__)


def aggregate_results(experiment_dir: str) -> dict[str, Any]:
    """Scan all runs under experiment_dir, collect eval JSON, aggregate."""
    ed = Path(experiment_dir)
    runs_dir = ed / "runs"

    # Also support flat output/ directories (no runs/ subdirectory)
    if not runs_dir.is_dir():
        runs_dir = ed

    run_dirs = sorted(
        d for d in runs_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not run_dirs:
        logger.warning("No run directories found in %s", runs_dir)
        return {}

    all_runs: list[dict[str, Any]] = []
    for rd in run_dirs:
        run_data = _collect_run(rd)
        if run_data:
            all_runs.append(run_data)

    if not all_runs:
        return {}

    by_model = _group_and_average(all_runs, "model")
    by_policy = _group_and_average(all_runs, "policy")
    by_app = _group_and_average(all_runs, "app")
    cross = _cross_table(all_runs, "model", "policy")

    status = read_json(ed / "status.json") or {}

    return {
        "experiment_id": ed.name,
        "total_runs": len(all_runs),
        "completed": sum(1 for r in all_runs if r.get("status") == "evaluated"),
        "failed": sum(1 for r in all_runs if "failed" in r.get("status", "")),
        "all_runs": all_runs,
        "by_model": by_model,
        "by_policy": by_policy,
        "by_app": by_app,
        "cross_table": {"model_x_policy": cross},
        "aggregated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------
# Per-run data collection
# ------------------------------------------------------------------

def _collect_run(run_dir: Path) -> dict[str, Any] | None:
    """Collect all eval results for a single run into a flat dict."""
    eval_dir = run_dir / "eval"
    forge_dir = run_dir / ".designforge"

    if not forge_dir.is_dir():
        return None

    # Parse run_id components
    parts = run_dir.name.split("__")
    app = parts[0] if len(parts) >= 1 else run_dir.name
    model = parts[1] if len(parts) >= 2 else ""
    policy = parts[2] if len(parts) >= 3 else ""
    suffix = parts[3] if len(parts) >= 4 else ""

    run: dict[str, Any] = {
        "run_id": run_dir.name,
        "app": app,
        "model": model,
        "policy": policy,
        "suffix": suffix,
        "status": "generated",
    }

    # Efficiency
    eff = _read_eval(eval_dir, "efficiency.json")
    if eff:
        run.update({
            "input_tokens": eff.get("input_tokens", 0),
            "output_tokens": eff.get("output_tokens", 0),
            "total_tokens": eff.get("total_tokens", 0),
            "llm_calls": eff.get("llm_call_count", 0),
            "llm_time_sec": eff.get("llm_time_sec", 0),
            "iteration_count": eff.get("iteration_count", 0),
            "estimated_cost_usd": eff.get("estimated_cost_usd", 0),
        })
    else:
        # Fallback: read from metrics.json directly
        metrics = read_json(forge_dir / "metrics.json")
        if metrics:
            totals = metrics.get("totals", {})
            run.update({
                "input_tokens": totals.get("total_input_tokens", 0),
                "output_tokens": totals.get("total_output_tokens", 0),
                "total_tokens": totals.get("total_tokens", 0),
                "llm_calls": totals.get("total_calls", 0),
                "llm_time_sec": totals.get("total_llm_time_sec", 0),
            })

    # Build
    build = _read_eval(eval_dir, "build_result.json")
    if build:
        run.update({
            "build_passed": build.get("build_passed", False),
            "tsc_passed": build.get("tsc_passed", False),
            "vite_passed": build.get("vite_passed", False),
            "route_coverage": build.get("route_coverage", 0),
            "page_count": build.get("page_count", 0),
            "route_count": build.get("route_count", 0),
        })

    # Navigation test + visual scores live under tag-namespaced subdirs.
    # Resolve the default click_tag via eval/summary.json, falling back to the
    # only subdirectory we find.
    summary = _read_eval(eval_dir, "summary.json") or {}
    click_tag = summary.get("vlm_click_tag") or _guess_default_subdir(
        eval_dir / "navigation_test"
    )

    if click_tag:
        nav_path = eval_dir / "navigation_test" / click_tag / "test_report.json"
        nav = read_json(nav_path) if nav_path.is_file() else None
        if nav:
            run.update({
                "nav_total": nav.get("total_cases", 0),
                "nav_passed": nav.get("passed", 0),
                "nav_pass_rate": nav.get("pass_rate", 0),
            })

        visual_path = eval_dir / "navigation_visual" / click_tag / "visual_report.json"
        visual = read_json(visual_path) if visual_path.is_file() else None
        if visual:
            judges = visual.get("visual_scores_by_judge") or {}
            judge_tag = summary.get("visual_judge_tag")
            if judge_tag not in judges:
                judge_tag = next(iter(judges), None) if len(judges) == 1 else judge_tag
            judge_entry = judges.get(judge_tag) if judge_tag else None
            if judge_entry:
                run.update({
                    "visual_avg_before": judge_entry.get("avg_before", 0) or 0,
                    "visual_avg_after": judge_entry.get("avg_after", 0) or 0,
                })

    # Project quality
    pq = _read_eval(eval_dir, "project_quality.json")
    if pq:
        scale = pq.get("scale", {})
        reuse = pq.get("component_reuse", {})
        dc = pq.get("design_consistency", {})
        run.update({
            "total_loc": scale.get("total_loc", 0),
            "file_count": scale.get("file_count", 0),
            "shared_components": reuse.get("shared_component_count", 0),
            "reuse_rate": reuse.get("component_reuse_rate", 0),
            "dead_component_rate": reuse.get("dead_component_rate", 0),
            "color_consistency": dc.get("color_token_consistency", 0),
        })

    # Trajectory
    traj = _read_eval(eval_dir, "trajectory.json")
    if traj:
        phases = traj.get("phase_analysis", {})
        behavior = traj.get("behavior_patterns", {})
        run.update({
            "phase_count": phases.get("phase_count", 0),
            "verify_fail_count": behavior.get("self_repair", {}).get("verify_fail_count", 0),
            "write_granularity": behavior.get("write_granularity", {}).get("avg_files_per_write_call", 0),
        })

    if eval_dir.is_dir() and any(eval_dir.iterdir()):
        run["status"] = "evaluated"

    return run


def _read_eval(eval_dir: Path, filename: str) -> dict | None:
    path = eval_dir / filename
    if path.is_file():
        return read_json(path)
    return None


def _guess_default_subdir(parent: Path) -> str | None:
    """When summary.json doesn't tell us the tag, pick the only child subdir
    if there's exactly one; otherwise return None (caller will skip).
    """
    if not parent.is_dir():
        return None
    subs = [d.name for d in parent.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if len(subs) == 1:
        return subs[0]
    return None


# ------------------------------------------------------------------
# Grouping and averaging
# ------------------------------------------------------------------

_NUMERIC_KEYS = [
    "input_tokens", "output_tokens", "total_tokens", "llm_calls",
    "llm_time_sec", "iteration_count", "estimated_cost_usd",
    "route_coverage", "page_count", "route_count",
    "nav_total", "nav_passed", "nav_pass_rate",
    "visual_avg_before", "visual_avg_after",
    "total_loc", "file_count", "shared_components", "reuse_rate",
    "dead_component_rate", "color_consistency",
    "phase_count", "verify_fail_count", "write_granularity",
]

_BOOL_KEYS = ["build_passed", "tsc_passed", "vite_passed"]


def _group_and_average(
    runs: list[dict], group_key: str
) -> dict[str, dict[str, Any]]:
    """Group runs by *group_key* and compute mean of numeric fields."""
    groups: dict[str, list[dict]] = {}
    for r in runs:
        key = r.get(group_key, "unknown")
        groups.setdefault(key, []).append(r)

    result: dict[str, dict[str, Any]] = {}
    for key, group_runs in groups.items():
        n = len(group_runs)
        agg: dict[str, Any] = {"count": n}

        for nk in _NUMERIC_KEYS:
            vals = [r.get(nk, 0) for r in group_runs if nk in r]
            if vals:
                mean = sum(vals) / len(vals)
                agg[f"{nk}_mean"] = round(mean, 2)
                if len(vals) > 1:
                    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
                    agg[f"{nk}_std"] = round(variance ** 0.5, 2)

        for bk in _BOOL_KEYS:
            vals = [r.get(bk) for r in group_runs if bk in r]
            if vals:
                agg[f"{bk}_rate"] = round(sum(1 for v in vals if v) / len(vals), 3)

        result[key] = agg
    return result


def _cross_table(
    runs: list[dict],
    key_a: str,
    key_b: str,
) -> list[dict[str, Any]]:
    """Build model × policy cross table."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        ka = r.get(key_a, "unknown")
        kb = r.get(key_b, "unknown")
        groups.setdefault((ka, kb), []).append(r)

    rows: list[dict[str, Any]] = []
    for (ka, kb), group_runs in sorted(groups.items()):
        n = len(group_runs)
        row: dict[str, Any] = {key_a: ka, key_b: kb, "count": n}
        for nk in _NUMERIC_KEYS:
            vals = [r.get(nk, 0) for r in group_runs if nk in r]
            if vals:
                row[nk] = round(sum(vals) / len(vals), 2)
        rows.append(row)
    return rows
