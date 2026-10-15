"""
Efficiency evaluator — reads .designforge/metrics.json and extracts standardised metrics.

Corresponds to benchmark §4.1 efficiency indicators.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from evaluator.utils import read_json

logger = logging.getLogger(__name__)


def evaluate_efficiency(run_dir: str) -> dict | None:
    """Read .designforge/metrics.json and return standardised efficiency metrics."""
    rd = Path(run_dir)
    metrics_path = rd / ".designforge" / "metrics.json"
    if not metrics_path.is_file():
        logger.warning("metrics.json not found at %s", metrics_path)
        return None

    data = read_json(metrics_path)
    if not data:
        return None

    totals = data.get("totals", {})
    calls = data.get("calls", [])
    per_agent = data.get("per_agent", [])

    iteration_count = len(calls) if calls else totals.get("total_calls", 0)

    return {
        "input_tokens": totals.get("total_input_tokens", 0),
        "output_tokens": totals.get("total_output_tokens", 0),
        "total_tokens": totals.get("total_tokens", 0),
        "llm_call_count": totals.get("total_calls", 0),
        "llm_time_sec": totals.get("total_llm_time_sec", 0),
        "pipeline_time_sec": totals.get("pipeline_time_sec", 0),
        "iteration_count": iteration_count,
        "estimated_cost_usd": totals.get("estimated_cost_usd", 0.0),
        "per_agent_summary": per_agent,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }
