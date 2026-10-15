"""
Todo tool — fixed-stage work plan for the agentic pipeline.

Stages are predefined by the system. The LLM only uses `update_tasks`
to signal transitions (in_progress / done). The orchestrator reads
the state to predict upcoming work and adjust context accordingly.

State is held in a module-level dict keyed by output_dir so multiple
concurrent runs don't interfere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


FIXED_STAGES: list[dict[str, str]] = [
    {"id": "analyze", "description": "Analyze screenshots and plan project structure"},
    {"id": "foundation", "description": "Write src/types.ts and shared utilities"},
    {"id": "components", "description": "Write shared components in src/components/"},
    {"id": "pages", "description": "Write all page components in src/pages/"},
    {"id": "assembly", "description": "Write src/router.tsx and src/App.tsx"},
    {"id": "verify", "description": "Type-check, fix errors, and finalize"},
]


@dataclass
class TodoItem:
    id: str
    description: str
    status: str = "pending"  # pending | in_progress | done


@dataclass
class TodoState:
    items: list[TodoItem] = field(default_factory=list)

    def to_display(self) -> str:
        if not self.items:
            return "(no tasks planned)"
        lines: list[str] = []
        for item in self.items:
            marker = {"pending": "□", "in_progress": "▶", "done": "✓"}[item.status]
            lines.append(f"  {marker} [{item.id}] {item.description}")
        return "\n".join(lines)


# keyed by output_dir
_states: dict[str, TodoState] = {}


def get_state(output_dir: str) -> TodoState:
    if output_dir not in _states:
        _states[output_dir] = TodoState()
    return _states[output_dir]


def clear_state(output_dir: str) -> None:
    _states.pop(output_dir, None)


def init_plan(output_dir: str) -> str:
    """Initialize the fixed-stage plan. Called by the orchestrator at run start.

    The first stage (analyze) is automatically set to in_progress.
    """
    state = get_state(output_dir)
    state.items = [
        TodoItem(id=s["id"], description=s["description"])
        for s in FIXED_STAGES
    ]
    state.items[0].status = "in_progress"
    logger.info("[todo] plan initialized with %d fixed stages", len(state.items))
    return state.to_display()


def update_tasks(output_dir: str, updates: list[dict[str, str]]) -> str:
    """Batch-update task statuses.

    Args:
        updates: list of {"id": "...", "status": "in_progress" | "done"}.
                 Multiple items allowed in a single call.

    Returns the updated plan with progress summary.
    """
    state = get_state(output_dir)
    _VALID = {"in_progress", "done"}

    not_found: list[str] = []
    for upd in updates:
        tid = upd["id"]
        new_status = upd["status"]
        if new_status not in _VALID:
            continue
        matched = False
        for item in state.items:
            if item.id == tid:
                item.status = new_status
                matched = True
                logger.info("[todo] %s → %s: [%s] %s", item.status, new_status, item.id, item.description)
                break
        if not matched:
            not_found.append(tid)

    done_count = sum(1 for it in state.items if it.status == "done")
    in_progress = [it for it in state.items if it.status == "in_progress"]
    pending = [it for it in state.items if it.status == "pending"]
    total = len(state.items)

    result = f"Progress: {done_count}/{total} done.\n{state.to_display()}"
    if not_found:
        result += f"\n\nWarning: ids not found: {', '.join(not_found)}"
    if not in_progress and not pending:
        result += "\n\nAll stages completed!"

    return result
