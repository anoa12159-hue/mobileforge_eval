"""
Task Registry — tracks all generation tasks and their status.

Maintains a `tasks.json` index in the output root directory, recording:
- Task name, input source, timestamps, status, config snapshot
- Enables listing, comparing, and resuming across multiple tasks

Directory convention:
    output/
    ├── tasks.json              ← this registry
    ├── wxreader/               ← task "wxreader"
    │   ├── .designforge/
    │   └── src/ ...
    └── taobao/                 ← task "taobao"
        └── ...
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskRecord:
    """A single task's metadata."""

    def __init__(
        self,
        name: str,
        input_dir: str,
        output_dir: str,
        *,
        status: str = "running",
        created_at: str = "",
        updated_at: str = "",
        config: dict[str, Any] | None = None,
        image_count: int = 0,
        description: str = "",
        result: dict[str, Any] | None = None,
    ):
        self.name = name
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.status = status
        self.created_at = created_at or _iso_now()
        self.updated_at = updated_at or self.created_at
        self.config = config or {}
        self.image_count = image_count
        self.description = description
        self.result = result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "config": self.config,
            "image_count": self.image_count,
            "description": self.description or "",
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskRecord:
        return cls(
            name=d["name"],
            input_dir=d.get("input_dir", ""),
            output_dir=d.get("output_dir", ""),
            status=d.get("status", "unknown"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            config=d.get("config"),
            image_count=d.get("image_count", 0),
            description=d.get("description", ""),
            result=d.get("result"),
        )


class TaskRegistry:
    """Reads/writes the tasks.json index in the output root."""

    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._path = output_root / "tasks.json"
        self._tasks: dict[str, TaskRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in data.get("tasks", []):
                rec = TaskRecord.from_dict(entry)
                self._tasks[rec.name] = rec
        except Exception as e:
            logger.warning("Failed to load task registry: %s", e)

    def _save(self) -> None:
        data = {
            "updated_at": _iso_now(),
            "task_count": len(self._tasks),
            "tasks": [t.to_dict() for t in sorted(
                self._tasks.values(), key=lambda t: t.created_at, reverse=True
            )],
        }
        content = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp", prefix=".tasks_"
        )
        closed = False
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            closed = True
            os.replace(tmp, str(self._path))
        except BaseException:
            if not closed:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def register(self, record: TaskRecord) -> None:
        self._tasks[record.name] = record
        self._save()
        logger.info("Task registered: %s → %s", record.name, record.output_dir)

    def update_status(self, name: str, status: str, result: dict[str, Any] | None = None) -> None:
        rec = self._tasks.get(name)
        if rec is None:
            return
        rec.status = status
        rec.updated_at = _iso_now()
        if result is not None:
            rec.result = result
        self._save()

    def get(self, name: str) -> TaskRecord | None:
        return self._tasks.get(name)

    def list_tasks(self) -> list[TaskRecord]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    @property
    def path(self) -> Path:
        return self._path
