"""
Structured trace writer — organized by stage / agent / page.

Produces JSON trace files in a hierarchical directory layout:

    traces/
    ├── run_manifest.json
    ├── 01_map/
    │   ├── ir_extraction/
    │   │   └── page_home.trace.json
    │   └── ...
    ├── 04_generate/
    │   └── page_gen/
    │       └── page_home.trace.json   (contains all iterations)
    └── ...

Each trace captures full context: input prompts, LLM output,
tool calls with results, token counts, timing, and verification.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STAGE_ORDER: dict[str, int] = {
    "map": 1,
    "reduce": 2,
    "contract": 3,
    "generate": 4,
    "merge": 5,
    "build": 6,
    "verify": 7,
}

MAX_TOOL_RESULT_LEN = 2000


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class IterationRecord:
    iteration: int
    elapsed_sec: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    assistant_text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "iteration": self.iteration,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
            },
            "finish_reason": self.finish_reason,
        }
        if self.assistant_text:
            d["assistant_text"] = self.assistant_text
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.verification is not None:
            d["verification"] = self.verification
        return d


class AgentLoopTrace:
    """Accumulates iteration records for a ToolAgent run, then writes on finalize."""

    def __init__(
        self,
        writer: TraceWriter,
        trace_id: str,
        agent_name: str,
        stage: str,
        page_id: str,
        model: str,
        system_prompt: str,
        initial_user_content: str | list[dict[str, Any]],
    ):
        self._writer = writer
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.stage = stage
        self.page_id = page_id
        self.model = model
        self.system_prompt = system_prompt
        self._user_content_summary = self._summarize_user_content(initial_user_content)
        self.started_at = _iso_now()
        self._t0 = time.time()
        self.iterations: list[IterationRecord] = []

    @staticmethod
    def _summarize_user_content(content: str | list[dict[str, Any]]) -> str | list:
        if isinstance(content, str):
            return content
        summarized = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image_url":
                    summarized.append({"type": "image_url", "url": "[base64 image]"})
                else:
                    summarized.append(block)
            else:
                summarized.append(block)
        return summarized

    def add_iteration(self, rec: IterationRecord) -> None:
        self.iterations.append(rec)

    def finalize(self) -> str:
        total_elapsed = time.time() - self._t0
        total_in = sum(it.input_tokens for it in self.iterations)
        total_out = sum(it.output_tokens for it in self.iterations)

        data = {
            "trace_id": self.trace_id,
            "agent": self.agent_name,
            "stage": self.stage,
            "page_id": self.page_id or None,
            "model": self.model,
            "started_at": self.started_at,
            "total_elapsed_sec": round(total_elapsed, 2),
            "total_iterations": len(self.iterations),
            "tokens": {"input": total_in, "output": total_out},
            "input": {
                "system": self.system_prompt,
                "user": self._user_content_summary,
            },
            "iterations": [it.to_dict() for it in self.iterations],
        }

        return self._writer._write_trace_file(
            self.agent_name, self.stage, self.page_id, data
        )


class TraceWriter:
    """Thread-safe structured trace writer with hierarchical directory layout."""

    def __init__(self, trace_dir: Path):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._current_stage: str = ""
        self._traces: list[dict[str, Any]] = []
        self._gate_results: dict[str, dict[str, Any]] = {}
        self._run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._stage_timings: dict[str, dict[str, Any]] = {}

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._current_stage = stage

    @property
    def current_stage(self) -> str:
        return self._current_stage

    def record_gate(self, gate_name: str, passed: bool, errors: list[str]) -> None:
        with self._lock:
            self._gate_results[gate_name] = {
                "passed": passed,
                "errors": errors,
                "timestamp": _iso_now(),
            }

    def record_stage_timing(self, stage: str, elapsed_sec: float) -> None:
        with self._lock:
            self._stage_timings[stage] = {
                "elapsed_sec": round(elapsed_sec, 2),
                "timestamp": _iso_now(),
            }

    # ------------------------------------------------------------------
    # Single-call trace (BaseAgent)
    # ------------------------------------------------------------------

    def write_single_call(
        self,
        *,
        agent_name: str,
        stage: str,
        page_id: str,
        model: str,
        call_type: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_sec: float,
        system_prompt: str,
        user_prompt: str,
        raw_output: str,
        image_path: str = "",
        retry_count: int = 0,
        error: str | None = None,
    ) -> str:
        data: dict[str, Any] = {
            "trace_id": _short_id(),
            "agent": agent_name,
            "stage": stage,
            "page_id": page_id or None,
            "model": model,
            "call_type": call_type,
            "started_at": _iso_now(),
            "elapsed_sec": round(elapsed_sec, 2),
            "tokens": {"input": input_tokens, "output": output_tokens},
            "input": {
                "system": system_prompt,
                "user": user_prompt,
            },
            "output": {
                "raw_text": raw_output,
            },
            "retry_count": retry_count,
            "error": error,
        }
        if image_path:
            data["input"]["image_path"] = image_path

        return self._write_trace_file(agent_name, stage, page_id, data)

    # ------------------------------------------------------------------
    # Agent-loop trace (ToolAgent)
    # ------------------------------------------------------------------

    def begin_agent_loop(
        self,
        *,
        agent_name: str,
        stage: str,
        page_id: str,
        model: str,
        system_prompt: str,
        initial_user_content: str | list[dict[str, Any]],
    ) -> AgentLoopTrace:
        return AgentLoopTrace(
            writer=self,
            trace_id=_short_id(),
            agent_name=agent_name,
            stage=stage or self._current_stage,
            page_id=page_id,
            model=model,
            system_prompt=system_prompt,
            initial_user_content=initial_user_content,
        )

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def write_manifest(self, config_info: dict[str, Any], totals: dict[str, Any]) -> Path:
        manifest = {
            "run_id": self._run_id,
            "finished_at": _iso_now(),
            "config": config_info,
            "stages": [],
            "gates": self._gate_results,
            "totals": totals,
        }

        stages_in_order = sorted(
            self._stage_timings.items(),
            key=lambda kv: STAGE_ORDER.get(kv[0], 99),
        )
        for stage_name, timing in stages_in_order:
            stage_traces = [
                t["path"] for t in self._traces if t["stage"] == stage_name
            ]
            manifest["stages"].append({
                "name": stage_name,
                "elapsed_sec": timing["elapsed_sec"],
                "gate": self._gate_results.get(stage_name),
                "traces": stage_traces,
            })

        path = self.trace_dir / "run_manifest.json"
        path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Trace manifest written to %s", path)
        return path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_trace_file(
        self, agent_name: str, stage: str, page_id: str, data: dict[str, Any]
    ) -> str:
        idx = STAGE_ORDER.get(stage, 0)
        stage_dir = self.trace_dir / f"{idx:02d}_{stage}" / agent_name
        stage_dir.mkdir(parents=True, exist_ok=True)

        trace_id = data.get("trace_id", _short_id())
        if page_id:
            filename = f"{page_id}.trace.json"
        else:
            filename = f"{trace_id}.trace.json"

        with self._lock:
            path = stage_dir / filename
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            rel_path = str(path.relative_to(self.trace_dir))
            self._traces.append({
                "agent": agent_name,
                "stage": stage,
                "page_id": page_id,
                "path": rel_path,
            })

        logger.debug("Trace written: %s", rel_path)
        return str(path)
