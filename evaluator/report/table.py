"""
Markdown table generator — produces human-readable comparison tables.

Generates the main results table in the style described in benchmark §4.5.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def generate_comparison_table(aggregated: dict[str, Any]) -> str:
    """Generate a markdown comparison table from aggregated results."""
    lines: list[str] = []
    lines.append(f"# Experiment Results: {aggregated.get('experiment_id', 'unknown')}")
    lines.append("")
    lines.append(f"Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Total runs: {aggregated.get('total_runs', 0)} "
                 f"| Completed: {aggregated.get('completed', 0)} "
                 f"| Failed: {aggregated.get('failed', 0)}")
    lines.append("")

    # Main comparison by policy
    by_policy = aggregated.get("by_policy", {})
    if by_policy:
        lines.append("## Results by Policy")
        lines.append("")
        lines.append(_build_policy_table(by_policy))
        lines.append("")

    # By model
    by_model = aggregated.get("by_model", {})
    if by_model:
        lines.append("## Results by Model")
        lines.append("")
        lines.append(_build_model_table(by_model))
        lines.append("")

    # By app
    by_app = aggregated.get("by_app", {})
    if by_app:
        lines.append("## Results by App")
        lines.append("")
        lines.append(_build_app_table(by_app))
        lines.append("")

    return "\n".join(lines)


def _build_policy_table(by_policy: dict[str, dict]) -> str:
    """Build the main policy comparison markdown table."""
    header = (
        "| Policy | Count | InTok↓ | OutTok | Time↓ | Build% | Rt.Cov | "
        "NavPass↑ | VisBefore↑ | VisAfter↑ | LoC | Reuse↑ |"
    )
    sep = "|--------|-------|--------|--------|-------|--------|--------|----------|------------|-----------|-----|--------|"
    rows: list[str] = [header, sep]

    for policy, agg in sorted(by_policy.items()):
        rows.append(
            f"| {policy} "
            f"| {agg.get('count', 0)} "
            f"| {_fmt_k(agg.get('input_tokens_mean', 0))} "
            f"| {_fmt_k(agg.get('output_tokens_mean', 0))} "
            f"| {_fmt_time(agg.get('llm_time_sec_mean', 0))} "
            f"| {_fmt_pct(agg.get('build_passed_rate', 0))} "
            f"| {_fmt_f(agg.get('route_coverage_mean', 0))} "
            f"| {_fmt_pct(agg.get('nav_pass_rate_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_before_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_after_mean', 0))} "
            f"| {int(agg.get('total_loc_mean', 0))} "
            f"| {_fmt_f(agg.get('reuse_rate_mean', 0))} |"
        )

    return "\n".join(rows)


def _build_model_table(by_model: dict[str, dict]) -> str:
    header = (
        "| Model | Count | InTok↓ | Time↓ | Build% | NavPass↑ | VisBefore↑ | VisAfter↑ | LoC |"
    )
    sep = "|-------|-------|--------|-------|--------|----------|------------|-----------|-----|"
    rows: list[str] = [header, sep]

    for model, agg in sorted(by_model.items()):
        rows.append(
            f"| {model} "
            f"| {agg.get('count', 0)} "
            f"| {_fmt_k(agg.get('input_tokens_mean', 0))} "
            f"| {_fmt_time(agg.get('llm_time_sec_mean', 0))} "
            f"| {_fmt_pct(agg.get('build_passed_rate', 0))} "
            f"| {_fmt_pct(agg.get('nav_pass_rate_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_before_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_after_mean', 0))} "
            f"| {int(agg.get('total_loc_mean', 0))} |"
        )

    return "\n".join(rows)


def _build_app_table(by_app: dict[str, dict]) -> str:
    header = (
        "| App | Count | InTok↓ | Build% | NavPass↑ | VisBefore↑ | VisAfter↑ | Pages |"
    )
    sep = "|-----|-------|--------|--------|----------|------------|-----------|-------|"
    rows: list[str] = [header, sep]

    for app, agg in sorted(by_app.items()):
        rows.append(
            f"| {app} "
            f"| {agg.get('count', 0)} "
            f"| {_fmt_k(agg.get('input_tokens_mean', 0))} "
            f"| {_fmt_pct(agg.get('build_passed_rate', 0))} "
            f"| {_fmt_pct(agg.get('nav_pass_rate_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_before_mean', 0))} "
            f"| {_fmt_f(agg.get('visual_avg_after_mean', 0))} "
            f"| {_fmt_f(agg.get('page_count_mean', 0))} |"
        )

    return "\n".join(rows)


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------


def _fmt_k(val: float) -> str:
    """Format large numbers in K notation."""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.0f}K"
    return str(int(val))


def _fmt_time(seconds: float) -> str:
    """Format seconds as min:sec or just seconds."""
    if seconds >= 60:
        return f"{seconds / 60:.1f}min"
    return f"{seconds:.0f}s"


def _fmt_pct(val: float) -> str:
    """Format a 0-1 ratio as percentage."""
    return f"{val * 100:.0f}%"


def _fmt_f(val: float) -> str:
    """Format a float to 2 decimal places."""
    return f"{val:.2f}" if val else "—"
