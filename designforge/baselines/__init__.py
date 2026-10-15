"""
Baseline methods for experimental comparison with DesignForge.

Five baselines representing different architectural paradigms for multi-page
design-to-code generation, all producing the same output format (React +
TypeScript + Tailwind project):

  - monolithic:          Single VLM call generates the entire project
  - page_independent:    Per-page VLM calls + deterministic assembly
  - sequential:          Pages generated one-by-one with accumulated context
  - plan_then_generate:  Global planning phase → parallel per-page generation
  - agentic:             Single autonomous agent with full tool access + build loop

Usage:
  python -m designforge.baselines --method monolithic --input ./input/wxreader
  python -m designforge.baselines --method agentic --input ./input/wxreader
  python -m designforge.baselines --method all --input ./input/wxreader
"""

from designforge.baselines.agentic import AgenticBaseline
from designforge.baselines.monolithic import MonolithicBaseline
from designforge.baselines.page_independent import PageIndependentBaseline
from designforge.baselines.plan_then_generate import PlanThenGenerateBaseline
from designforge.baselines.sequential import SequentialBaseline

BASELINE_REGISTRY: dict[str, type] = {
    "monolithic": MonolithicBaseline,
    "page_independent": PageIndependentBaseline,
    "sequential": SequentialBaseline,
    "plan_then_generate": PlanThenGenerateBaseline,
    "agentic": AgenticBaseline,
}

__all__ = [
    "BASELINE_REGISTRY",
    "AgenticBaseline",
    "MonolithicBaseline",
    "PageIndependentBaseline",
    "SequentialBaseline",
    "PlanThenGenerateBaseline",
]
