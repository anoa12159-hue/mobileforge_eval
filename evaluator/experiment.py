"""
Experiment lifecycle management.

Creates timestamped experiment directories, saves config snapshots,
expands the matrix into RunSpec objects, and manages status.json.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluator.config import ExperimentConfig, MethodSpec
from evaluator.utils import atomic_json_write, read_json, sanitize_model_tag

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_EXPERIMENTS_ROOT = _PROJECT_ROOT / "experiments"


# ------------------------------------------------------------------
# RunSpec — one unit of work
# ------------------------------------------------------------------


@dataclass
class RunSpec:
    """Describes a single (app, model, method) run."""

    run_id: str
    app_path: str
    model: str
    method: str
    context_policy: dict[str, Any] | None = None
    suffix: str = ""
    run_number: int = 1


# ------------------------------------------------------------------
# Status constants
# ------------------------------------------------------------------

PENDING = "pending"
GENERATING = "generating"
GENERATED = "generated"
EVALUATING = "evaluating"
EVALUATED = "evaluated"
GEN_FAILED = "gen_failed"
EVAL_FAILED = "eval_failed"


# ------------------------------------------------------------------
# Experiment
# ------------------------------------------------------------------


class Experiment:
    """Manages a single experiment's directory, config snapshot, and status."""

    def __init__(self, cfg: ExperimentConfig, *, resume: bool = False):
        self.cfg = cfg
        self._resume = resume
        self.experiment_dir: Path | None = None
        self._status: dict[str, Any] = {}

        if resume:
            self._try_resume()
        if self.experiment_dir is None or not self.experiment_dir.is_dir():
            self._create_fresh()

    # ------------------------------------------------------------------
    # Directory setup
    # ------------------------------------------------------------------

    @property
    def _experiments_root(self) -> Path:
        if self.cfg.output_dir:
            p = Path(self.cfg.output_dir)
            if not p.is_absolute():
                p = _PROJECT_ROOT / p
            return p
        return _EXPERIMENTS_ROOT

    def _create_fresh(self) -> None:
        root = self._experiments_root
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_dir = root / f"{ts}_{self.cfg.name}"
        self.experiment_dir.mkdir(parents=True, exist_ok=True)

        self._save_config_snapshot()
        self._init_status()
        logger.info("Created experiment dir: %s", self.experiment_dir)

    def _try_resume(self) -> None:
        """Find the most recent experiment directory with a matching name."""
        root = self._experiments_root
        if not root.is_dir():
            return
        candidates = sorted(
            (d for d in root.iterdir()
             if d.is_dir() and d.name.endswith(f"_{self.cfg.name}")),
            reverse=True,
        )
        if not candidates:
            return
        self.experiment_dir = candidates[0]
        self._load_status()
        logger.info("Resuming experiment from %s", self.experiment_dir)

    def _save_config_snapshot(self) -> None:
        src = Path(self.cfg.config_path)
        if src.is_file():
            shutil.copy2(src, self.experiment_dir / "config_snapshot.yaml")

        engine_cfg = _PROJECT_ROOT / "experiment.yaml"
        if engine_cfg.is_file():
            shutil.copy2(engine_cfg, self.experiment_dir / "engine_config_snapshot.yaml")

    # ------------------------------------------------------------------
    # Matrix expansion
    # ------------------------------------------------------------------

    def expand_matrix(self) -> list[RunSpec]:
        """Expand apps × models × methods × runs_per_cell → list[RunSpec]."""
        specs: list[RunSpec] = []
        for app_path in self.cfg.apps:
            app_name = Path(app_path).name
            for model in self.cfg.models:
                model_tag = sanitize_model_tag(model)
                for method in self.cfg.methods:
                    for run_num in range(1, self.cfg.runs_per_cell + 1):
                        suffix = method.suffix or method.name
                        run_id = f"{app_name}__{model_tag}__{suffix}"
                        if self.cfg.runs_per_cell > 1:
                            run_id = f"{run_id}__run{run_num}"

                        specs.append(RunSpec(
                            run_id=run_id,
                            app_path=app_path,
                            model=model,
                            method=method.name,
                            context_policy=method.context_policy,
                            suffix=suffix,
                            run_number=run_num,
                        ))
        return specs

    def get_pending_runs(self) -> list[RunSpec]:
        """Return RunSpecs that still need work (pending, gen_failed on resume)."""
        all_specs = self.expand_matrix()
        runs_status = self._status.get("runs", {})
        result: list[RunSpec] = []
        for spec in all_specs:
            st = runs_status.get(spec.run_id, {}).get("status", PENDING)
            if self._resume:
                if st in (EVALUATED,):
                    continue
                if st in (GENERATED, EVAL_FAILED):
                    pass
            else:
                if st in (EVALUATED, GENERATED, EVALUATING):
                    continue
            result.append(spec)
        return result

    # ------------------------------------------------------------------
    # Run directory
    # ------------------------------------------------------------------

    def create_run_dir(self, spec: RunSpec, *, clean: bool = False) -> Path:
        run_dir = self.experiment_dir / "runs" / spec.run_id
        if clean and run_dir.exists():
            shutil.rmtree(run_dir)
            logger.info("Cleaned stale run directory: %s", run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @property
    def runs_dir(self) -> Path:
        return self.experiment_dir / "runs"

    @property
    def report_dir(self) -> Path:
        d = self.experiment_dir / "report"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------

    def _init_status(self) -> None:
        self._status = {
            "experiment_id": self.experiment_dir.name,
            "eval_strategy": self.cfg.evaluation.eval_strategy,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "total_runs": self.cfg.total_runs,
            "counts": {},
            "runs": {},
        }
        self._save_status()

    def _load_status(self) -> None:
        path = self.experiment_dir / "status.json"
        self._status = read_json(path) or {}

    def _save_status(self) -> None:
        self._recount()
        atomic_json_write(self.experiment_dir / "status.json", self._status)

    def _recount(self) -> None:
        counts: dict[str, int] = {}
        for info in self._status.get("runs", {}).values():
            st = info.get("status", PENDING)
            counts[st] = counts.get(st, 0) + 1
        self._status["counts"] = counts

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        runs = self._status.setdefault("runs", {})
        entry = runs.setdefault(run_id, {})
        entry["status"] = status
        if status in (GENERATING, EVALUATING) and "started_at" not in entry:
            entry["started_at"] = datetime.now().isoformat(timespec="seconds")
        if status in (EVALUATED, GEN_FAILED, EVAL_FAILED, GENERATED):
            entry["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if extra:
            entry.update(extra)
        self._save_status()

    def get_run_status(self, run_id: str) -> str:
        return (
            self._status.get("runs", {})
            .get(run_id, {})
            .get("status", PENDING)
        )

    def get_runs_with_status(self, *statuses: str) -> list[str]:
        return [
            rid for rid, info in self._status.get("runs", {}).items()
            if info.get("status") in statuses
        ]

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status)
