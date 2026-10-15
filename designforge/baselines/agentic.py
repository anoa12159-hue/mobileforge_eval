"""
Agentic Baseline — thin wrapper around designforge.agentic.core.

Kept here so that the baselines runner can dispatch to it via BASELINE_REGISTRY,
maintaining compatibility with the comparison framework.
"""

from designforge.agentic.core import AgenticPipeline


class AgenticBaseline(AgenticPipeline):
    """Alias for backward-compatible baseline registration."""

    name = "agentic"
