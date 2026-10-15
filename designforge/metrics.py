"""
Observability: per-call token tracking, timing, raw output archival, and summary report.

Architecture:
- Each LLM/VLM call produces a `CallRecord`
- `MetricsCollector` (singleton-ish) accumulates all records
- `TraceWriter` produces structured JSON traces organized by stage/agent
- Pipeline registers stage start/end for stage-level timing
- At the end, `print_summary()` renders a Rich table and writes a JSON report
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from designforge.trace_writer import TraceWriter

logger = logging.getLogger(__name__)


@dataclass
class CallRecord:
    """One LLM / VLM invocation."""

    agent_name: str
    call_type: str              # "llm" | "vlm" | "agent"
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    elapsed_sec: float
    timestamp: float
    page_id: str = ""
    raw_output_path: str = ""


@dataclass
class StageTimer:
    name: str
    start: float = 0.0
    end: float = 0.0
    elapsed_sec: float = 0.0


class MetricsCollector:
    """Thread-safe metrics accumulator with structured trace support."""

    def __init__(self, trace_dir: Path | None = None):
        self._lock = threading.Lock()
        self.calls: list[CallRecord] = []
        self.stages: list[StageTimer] = []
        self._active_stages: dict[str, StageTimer] = {}
        self.trace_dir = trace_dir
        self.trace_writer: TraceWriter | None = None
        if trace_dir:
            trace_dir.mkdir(parents=True, exist_ok=True)
            self.trace_writer = TraceWriter(trace_dir)

    # ------------------------------------------------------------------
    # Call recording
    # ------------------------------------------------------------------

    def record_call(
        self,
        agent_name: str,
        call_type: str,
        model: str,
        usage: Any,
        elapsed_sec: float,
        raw_output: str,
        *,
        page_id: str = "",
        system_prompt: str = "",
        user_prompt: str = "",
        image_path: str = "",
    ) -> CallRecord:
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)

        raw_path = ""
        if self.trace_writer and call_type in ("llm", "vlm"):
            stage = self.trace_writer.current_stage
            raw_path = self.trace_writer.write_single_call(
                agent_name=agent_name,
                stage=stage,
                page_id=page_id,
                model=model,
                call_type=call_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_sec=elapsed_sec,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                raw_output=raw_output,
                image_path=image_path,
            )

        rec = CallRecord(
            agent_name=agent_name,
            call_type=call_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            elapsed_sec=elapsed_sec,
            timestamp=time.time(),
            page_id=page_id,
            raw_output_path=raw_path,
        )

        with self._lock:
            self.calls.append(rec)

        logger.debug(
            "[metrics] %s.%s | in=%d out=%d total=%d | %.1fs | %s",
            agent_name, call_type,
            input_tokens, output_tokens, input_tokens + output_tokens,
            elapsed_sec, page_id or "-",
        )
        return rec

    # ------------------------------------------------------------------
    # Stage timing
    # ------------------------------------------------------------------

    def stage_start(self, name: str) -> None:
        timer = StageTimer(name=name, start=time.time())
        with self._lock:
            self._active_stages[name] = timer

    def stage_end(self, name: str) -> None:
        with self._lock:
            timer = self._active_stages.pop(name, None)
        if timer:
            timer.end = time.time()
            timer.elapsed_sec = timer.end - timer.start
            with self._lock:
                self.stages.append(timer)
            if self.trace_writer:
                self.trace_writer.record_stage_timing(name, timer.elapsed_sec)
            logger.info("[stage] %s completed in %.1fs", name, timer.elapsed_sec)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_totals(self) -> dict[str, Any]:
        with self._lock:
            calls = list(self.calls)
            stages = list(self.stages)
        total_input = sum(c.input_tokens for c in calls)
        total_output = sum(c.output_tokens for c in calls)
        total_time = sum(c.elapsed_sec for c in calls)
        total_cost = self._estimate_total_cost(calls)
        return {
            "total_calls": len(calls),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_llm_time_sec": round(total_time, 1),
            "pipeline_time_sec": round(
                sum(s.elapsed_sec for s in stages), 1
            ),
            "estimated_cost_usd": round(total_cost, 4),
        }

    def _estimate_total_cost(self, calls: list[CallRecord] | None = None) -> float:
        """Estimate total cost using model pricing from config."""
        from designforge.config import get_model_info
        if calls is None:
            with self._lock:
                calls = list(self.calls)
        cost = 0.0
        for c in calls:
            info = get_model_info(c.model)
            if info is None:
                continue
            cost += (c.input_tokens / 1_000_000) * info.input_price
            cost += (c.output_tokens / 1_000_000) * info.output_price
        return cost

    def get_per_agent_summary(self) -> list[dict[str, Any]]:
        from designforge.config import get_model_info
        with self._lock:
            calls = list(self.calls)
        agents: dict[str, dict[str, Any]] = {}
        for c in calls:
            if c.agent_name not in agents:
                agents[c.agent_name] = {
                    "agent": c.agent_name,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "total_sec": 0.0,
                    "cost_usd": 0.0,
                }
            a = agents[c.agent_name]
            a["calls"] += 1
            a["input_tokens"] += c.input_tokens
            a["output_tokens"] += c.output_tokens
            a["total_tokens"] += c.total_tokens
            a["total_sec"] += c.elapsed_sec
            info = get_model_info(c.model)
            if info:
                a["cost_usd"] += (c.input_tokens / 1_000_000) * info.input_price
                a["cost_usd"] += (c.output_tokens / 1_000_000) * info.output_price
        rows = sorted(agents.values(), key=lambda x: x["total_tokens"], reverse=True)
        for r in rows:
            r["total_sec"] = round(r["total_sec"], 1)
            r["cost_usd"] = round(r["cost_usd"], 4)
        return rows

    def print_summary(self, console: Console | None = None) -> None:
        console = console or Console()
        totals = self.get_totals()
        per_agent = self.get_per_agent_summary()

        # --- Per-agent table ---
        has_cost = totals["estimated_cost_usd"] > 0
        table = Table(title="Agent Token Usage & Timing", show_lines=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Calls", justify="right")
        table.add_column("Input Tokens", justify="right")
        table.add_column("Output Tokens", justify="right")
        table.add_column("Total Tokens", justify="right", style="bold")
        table.add_column("Time (s)", justify="right")
        if has_cost:
            table.add_column("Cost ($)", justify="right")

        for row in per_agent:
            cols = [
                row["agent"],
                str(row["calls"]),
                f"{row['input_tokens']:,}",
                f"{row['output_tokens']:,}",
                f"{row['total_tokens']:,}",
                f"{row['total_sec']:.1f}",
            ]
            if has_cost:
                cols.append(f"{row['cost_usd']:.4f}")
            table.add_row(*cols)

        total_cols = [
            "[bold]TOTAL[/bold]", str(totals["total_calls"]),
            f"{totals['total_input_tokens']:,}",
            f"{totals['total_output_tokens']:,}",
            f"[bold]{totals['total_tokens']:,}[/bold]",
            f"{totals['total_llm_time_sec']:.1f}",
        ]
        if has_cost:
            total_cols.append(f"[bold]{totals['estimated_cost_usd']:.4f}[/bold]")
        table.add_row(*total_cols, style="on grey15")
        console.print()
        console.print(table)

        # --- Stage timing table ---
        if self.stages:
            st = Table(title="Stage Timing", show_lines=True)
            st.add_column("Stage", style="cyan")
            st.add_column("Duration (s)", justify="right")
            for s in self.stages:
                st.add_row(s.name, f"{s.elapsed_sec:.1f}")
            st.add_row(
                "[bold]TOTAL[/bold]",
                f"[bold]{totals['pipeline_time_sec']:.1f}[/bold]",
                style="on grey15",
            )
            console.print(st)

    def save_report(self, path: Path) -> None:
        from designforge.config import get_all_models, VISION_MODEL, TEXT_MODEL, AGENT_MODEL
        with self._lock:
            calls = list(self.calls)
            stages = list(self.stages)
        report = {
            "experiment": {
                "vision_model": VISION_MODEL,
                "text_model": TEXT_MODEL,
                "agent_model": AGENT_MODEL,
            },
            "totals": self.get_totals(),
            "per_agent": self.get_per_agent_summary(),
            "stages": [
                {"name": s.name, "elapsed_sec": round(s.elapsed_sec, 1)}
                for s in stages
            ],
            "calls": [
                {
                    "agent": c.agent_name,
                    "type": c.call_type,
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "elapsed_sec": round(c.elapsed_sec, 2),
                    "page_id": c.page_id,
                    "raw_output_path": c.raw_output_path,
                }
                for c in calls
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Metrics report saved to %s", path)


# --- Module-level default instance (set by pipeline) ---
_default: MetricsCollector | None = None


def get_metrics() -> MetricsCollector | None:
    return _default


def init_metrics(trace_dir: Path) -> MetricsCollector:
    global _default
    _default = MetricsCollector(trace_dir=trace_dir)
    return _default
