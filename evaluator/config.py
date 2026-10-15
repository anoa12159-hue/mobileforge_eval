"""
Experiment configuration loader.

Reads exp_config.yaml and produces an ExperimentConfig dataclass
that describes a full experiment batch: matrix, evaluation strategy, limits, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class MethodSpec:
    """A single method entry in the experiment matrix."""

    name: str
    context_policy: dict[str, Any] | None = None
    suffix: str = ""


@dataclass
class EvaluationSelection:
    """Post-generation filters applied by `eval -c`.

    Kept separate from `matrix.*` (which is generation-side) so that filtering
    for evaluation doesn't overload the generation spec.
    """

    # Only evaluate run dirs whose run_id model segment is in this list.
    # Empty list = no filtering.
    models: list[str] = field(default_factory=list)

    # Default list of evaluator modules to skip in `eval`.
    # Overridden by CLI --skip when provided.
    skip_modules: list[str] = field(default_factory=list)


@dataclass
class EvaluationConfig:
    """Evaluation strategy and toggles."""

    eval_strategy: str = "per_run"
    build_check: bool = True
    structure_check: bool = True
    screenshot: bool = True
    visual_judge: bool = True
    visual_judge_model: str = "gemini-2.5-pro"
    selection: EvaluationSelection = field(default_factory=EvaluationSelection)


@dataclass
class ExperimentConfig:
    """Complete experiment specification loaded from exp_config.yaml."""

    name: str = "unnamed"
    description: str = ""

    apps: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    methods: list[MethodSpec] = field(default_factory=list)

    runs_per_cell: int = 1

    max_parallel_runs: int = 1
    timeout_per_run_sec: int = 3600

    output_dir: str = ""

    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)

    config_path: str = ""

    @property
    def total_runs(self) -> int:
        return len(self.apps) * len(self.models) * len(self.methods) * self.runs_per_cell


_APP_MARKER_FILES = ("metadata.json", "metadata.reviewed.json")


def _is_app_dir(d: Path) -> bool:
    """An app sample directory contains a metadata.json (produced by the annotator)."""
    return any((d / name).is_file() for name in _APP_MARKER_FILES)


def _expand_apps(raw_apps: list[str], config_dir: Path) -> list[str]:
    """Expand app paths: if a path is a parent directory (no metadata.json), expand its subdirs.

    Supports:
      - "./input/mobileforge"          -> all subdirs that contain metadata.json
      - "./input/mobileforge/airbnb"   -> kept as-is (has metadata.json)
    """
    expanded: list[str] = []
    for app_path in raw_apps:
        p = Path(app_path)
        if not p.is_absolute():
            p = config_dir / p
        if not p.is_dir():
            expanded.append(app_path)
            continue
        if _is_app_dir(p):
            expanded.append(app_path)
        else:
            subdirs = sorted(
                d for d in p.iterdir()
                if d.is_dir() and not d.name.startswith(".") and _is_app_dir(d)
            )
            if not subdirs:
                logger.warning("No app directories (with metadata.json) found in %s", p)
            for sd in subdirs:
                expanded.append(str(Path(app_path) / sd.name))
    if expanded:
        logger.info("Expanded apps: %d entries", len(expanded))
    return expanded


def load_experiment_config(config_path: str | Path) -> ExperimentConfig:
    """Load and validate an experiment config from a YAML file."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Experiment config not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    exp_raw = raw.get("experiment", {})
    matrix_raw = raw.get("matrix", {})
    limits_raw = raw.get("limits", {})
    eval_raw = raw.get("evaluation", {})

    methods: list[MethodSpec] = []
    for m in matrix_raw.get("methods", []):
        if isinstance(m, str):
            methods.append(MethodSpec(name=m))
        elif isinstance(m, dict):
            methods.append(MethodSpec(
                name=m.get("name", "agentic"),
                context_policy=m.get("context_policy"),
                suffix=m.get("suffix", ""),
            ))

    selection_raw = eval_raw.get("selection", {}) or {}
    selection = EvaluationSelection(
        models=list(selection_raw.get("models", []) or []),
        skip_modules=list(selection_raw.get("skip_modules", []) or []),
    )

    evaluation = EvaluationConfig(
        eval_strategy=eval_raw.get("eval_strategy", "per_run"),
        build_check=eval_raw.get("build_check", True),
        structure_check=eval_raw.get("structure_check", True),
        screenshot=eval_raw.get("screenshot", True),
        visual_judge=eval_raw.get("visual_judge", True),
        visual_judge_model=eval_raw.get("visual_judge_model", "gemini-2.5-pro"),
        selection=selection,
    )

    apps = _expand_apps(matrix_raw.get("apps", []), config_dir=path.parent)

    cfg = ExperimentConfig(
        name=exp_raw.get("name", "unnamed"),
        description=exp_raw.get("description", ""),
        apps=apps,
        models=matrix_raw.get("models", []),
        methods=methods,
        runs_per_cell=raw.get("runs_per_cell", 1),
        max_parallel_runs=limits_raw.get("max_parallel_runs", 1),
        timeout_per_run_sec=limits_raw.get("timeout_per_run_sec", 3600),
        output_dir=exp_raw.get("output_dir", ""),
        evaluation=evaluation,
        config_path=str(path.resolve()),
    )

    _validate(cfg)
    return cfg


def _validate(cfg: ExperimentConfig) -> None:
    """Raise on clearly invalid configuration."""
    errors: list[str] = []
    if not cfg.apps:
        errors.append("matrix.apps is empty")
    if not cfg.models:
        errors.append("matrix.models is empty")
    if not cfg.methods:
        errors.append("matrix.methods is empty")
    if cfg.evaluation.eval_strategy not in ("per_run", "batch", "manual"):
        errors.append(
            f"evaluation.eval_strategy must be per_run|batch|manual, "
            f"got '{cfg.evaluation.eval_strategy}'"
        )
    if errors:
        raise ValueError(
            "Invalid experiment config:\n" + "\n".join(f"  - {e}" for e in errors)
        )
