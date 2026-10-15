"""
Evaluation pipeline — the standalone entry point for evaluating a single run.

Does NOT depend on runner.py or experiment.py; works with any run directory
that has src/ and .designforge/.

Evaluator modules:
  - efficiency, build, project_quality, trajectory  (always available)
  - navigation_test   (testgen run — requires test_cases.json, depends on build)
  - navigation_visual  (testgen eval — VLM scoring, depends on navigation_test)
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluator.utils import atomic_json_write, read_json, sanitize_model_tag

logger = logging.getLogger(__name__)


def _resolve_reviewed(directory: Path, base_name: str) -> Path:
    """Prefer .reviewed.json variant, fall back to original."""
    stem = base_name.removesuffix(".json")
    reviewed = directory / f"{stem}.reviewed.json"
    if reviewed.is_file():
        return reviewed
    return directory / base_name

ALL_EVALUATORS = [
    "efficiency",
    "build",
    "project_quality",
    "trajectory",
    "navigation_test",
    "navigation_visual",
]

_DEPENDENCY_MAP: dict[str, list[str]] = {
    "efficiency": [],
    "build": [],
    "project_quality": [],
    "trajectory": [],
    "navigation_test": ["build"],
    "navigation_visual": ["navigation_test"],
}


class EvaluationPreconditionError(Exception):
    """Raised when run directory is incomplete for evaluation."""


def evaluate_run(
    run_dir: str,
    input_dir: str | None = None,
    *,
    skip: list[str] | None = None,
    only: list[str] | None = None,
    force: bool = False,
    resume: bool = False,
    visual_judge_model: str | None = None,
    vlm_click_model: str | None = None,
    plot: bool = True,
    x_axis: str = "both",
) -> dict[str, Any]:
    """Evaluate a single run directory.

    Gate check: src/ and .designforge/ must exist.
    Runs each evaluator in dependency order, writes results to eval/*.json.

    Strategy flags (three-way, semantically exclusive; if both resume and force
    are given, ``force`` wins):

      - neither : module-level idempotency (existing result file -> reuse).
                  Missing modules are run from scratch. Legacy behaviour.
      - resume  : same as neither at module level, but for navigation_test /
                  navigation_visual the evaluator is invoked with a resume flag
                  so it can filter out already-completed cases / judge triples.
      - force   : for every targeted module (whether complete or partial),
                  wipe its artefacts under the current (click_tag, judge_tag)
                  namespace and rerun from scratch. Other judges' scores in
                  navigation_visual are preserved. Use when prior eval results
                  are known to be wrong.
    """
    from designforge.config import TESTGEN_FALLBACK_MODEL, TESTGEN_EVAL_MODEL

    if visual_judge_model is None:
        visual_judge_model = TESTGEN_EVAL_MODEL
    if vlm_click_model is None:
        vlm_click_model = TESTGEN_FALLBACK_MODEL

    rd = Path(run_dir)
    src_dir = rd / "src"
    forge_dir = rd / ".designforge"

    if not src_dir.is_dir() or not any(src_dir.iterdir()):
        raise EvaluationPreconditionError(
            f"src/ directory missing or empty in {run_dir} — cannot evaluate"
        )
    if not forge_dir.is_dir():
        raise EvaluationPreconditionError(
            f".designforge/ directory missing in {run_dir} — cannot evaluate"
        )

    eval_dir = rd / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    click_tag = sanitize_model_tag(vlm_click_model) if vlm_click_model else "default"
    judge_tag = sanitize_model_tag(visual_judge_model) if visual_judge_model else "default"

    # Determine which evaluators to run
    to_run = _resolve_evaluators(skip=skip, only=only)

    results: dict[str, Any] = {}
    completed: set[str] = set()
    skipped_reasons: dict[str, str] = {}

    for name in to_run:
        # Check dependencies
        deps = _DEPENDENCY_MAP.get(name, [])
        unmet = [d for d in deps if d not in completed]
        if unmet:
            reason = f"unmet dependencies: {', '.join(unmet)}"
            skipped_reasons[name] = reason
            logger.info("Skipping %s: %s", name, reason)
            continue

        result_file = _result_file(
            eval_dir, name, click_tag=click_tag, judge_tag=judge_tag,
        )
        module_complete = _is_module_complete(
            name, result_file, judge_tag=judge_tag,
        )

        # Branch 1: module already complete -> reuse (applies in all modes)
        if module_complete and not force:
            existing = read_json(result_file)
            if existing:
                results[name] = existing
                completed.add(name)
                logger.info("Reusing existing %s result", name)
                continue

        # Branch 2: not complete + force -> wipe partial dir first
        if force:
            _wipe_partial(name, eval_dir, click_tag=click_tag, judge_tag=judge_tag)

        # Branch 3: not complete + resume -> pass resume flag to nav evaluators
        # Branch 4: not complete + neither -> legacy behaviour (rerun whole module)

        # Run the evaluator
        try:
            result = _dispatch(
                name, rd, input_dir,
                results=results,
                visual_judge_model=visual_judge_model,
                vlm_click_model=vlm_click_model,
                click_tag=click_tag,
                judge_tag=judge_tag,
                result_file=result_file,
                resume=resume and not force,
            )
            if result is not None:
                results[name] = result
                atomic_json_write(result_file, result)
                completed.add(name)
                logger.info("✓ %s completed", name)
            else:
                skipped_reasons[name] = "evaluator returned None"
        except Exception as exc:
            logger.warning("✗ %s failed: %s", name, exc, exc_info=True)
            skipped_reasons[name] = str(exc)

    # Generate token composition plots for this run
    if plot and "trajectory" in results and results["trajectory"] is not None:
        try:
            from evaluator.evaluate.trajectory import plot_token_composition
            plot_paths = plot_token_composition(run_dir, results["trajectory"], x_axis=x_axis)
            for pp in plot_paths:
                logger.info("✓ token_composition plot → %s", pp)
        except Exception as exc:
            logger.debug("Token composition plot skipped: %s", exc)

    # Write merged summary
    summary: dict[str, Any] = {
        "run_dir": run_dir,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "vlm_click_model": vlm_click_model,
        "visual_judge_model": visual_judge_model,
        "vlm_click_tag": click_tag,
        "visual_judge_tag": judge_tag,
        "completed": sorted(completed),
        "skipped": skipped_reasons,
        "results": results,
    }
    atomic_json_write(eval_dir / "summary.json", summary)

    return results


def is_run_fully_evaluated(
    run_dir: Path,
    *,
    skip: list[str] | None = None,
    only: list[str] | None = None,
    visual_judge_model: str | None = None,
    vlm_click_model: str | None = None,
) -> bool:
    """Return True if every target evaluator for this run has a complete result
    under the given (click_tag, judge_tag) namespace.
    """
    from designforge.config import TESTGEN_FALLBACK_MODEL, TESTGEN_EVAL_MODEL

    eval_dir = run_dir / "eval"
    if not eval_dir.is_dir():
        return False

    vjm = visual_judge_model or TESTGEN_EVAL_MODEL
    vcm = vlm_click_model or TESTGEN_FALLBACK_MODEL
    click_tag = sanitize_model_tag(vcm) if vcm else "default"
    judge_tag = sanitize_model_tag(vjm) if vjm else "default"

    to_run = _resolve_evaluators(skip=skip, only=only)
    for name in to_run:
        rf = _result_file(eval_dir, name, click_tag=click_tag, judge_tag=judge_tag)
        if not _is_module_complete(name, rf, judge_tag=judge_tag):
            return False
    return True


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _resolve_evaluators(
    skip: list[str] | None = None,
    only: list[str] | None = None,
) -> list[str]:
    """Return the ordered list of evaluators to run."""
    if only:
        return [e for e in ALL_EVALUATORS if e in only]
    if skip:
        return [e for e in ALL_EVALUATORS if e not in skip]
    return list(ALL_EVALUATORS)


def _result_file(
    eval_dir: Path,
    name: str,
    *,
    click_tag: str = "default",
    judge_tag: str = "default",
) -> Path:
    """Compute the canonical result-file path for each evaluator.

    navigation_test / navigation_visual are namespaced by click/judge tag so
    different VLM models produce side-by-side artefacts that don't overwrite
    each other.
    """
    if name == "navigation_test":
        return eval_dir / "navigation_test" / click_tag / "test_report.json"
    if name == "navigation_visual":
        return eval_dir / "navigation_visual" / click_tag / "visual_report.json"

    file_names = {
        "efficiency": "efficiency.json",
        "build": "build_result.json",
        "project_quality": "project_quality.json",
        "trajectory": "trajectory.json",
    }
    return eval_dir / file_names.get(name, f"{name}.json")


def _is_module_complete(
    name: str,
    result_file: Path,
    *,
    judge_tag: str = "default",
) -> bool:
    """Check whether a module's result file represents a complete run.

    - navigation_visual: must contain an entry under the given judge_tag.
    - others: file-exists + JSON-loadable is enough (consistent with legacy).
    """
    if not result_file.is_file():
        return False
    data = read_json(result_file)
    if not data:
        return False
    if name == "navigation_visual":
        by_judge = data.get("visual_scores_by_judge") or {}
        return judge_tag in by_judge and bool(by_judge[judge_tag])
    return True


def _wipe_partial(
    name: str,
    eval_dir: Path,
    *,
    click_tag: str,
    judge_tag: str,
) -> None:
    """Remove the partial artefacts of an incomplete module (--force path)."""
    if name == "navigation_test":
        target = eval_dir / "navigation_test" / click_tag
        shots = eval_dir / "test_screenshots" / click_tag
        for p in (target, shots):
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
                logger.info("Wiped partial %s", p)
        return
    if name == "navigation_visual":
        # Only drop this specific judge's scores; keep other judges intact.
        path = eval_dir / "navigation_visual" / click_tag / "visual_report.json"
        data = read_json(path) if path.is_file() else None
        if data and "visual_scores_by_judge" in data:
            data["visual_scores_by_judge"].pop(judge_tag, None)
            atomic_json_write(path, data)
            logger.info("Wiped %s judge=%s", path, judge_tag)
        return
    # Other modules: drop the JSON so it gets regenerated.
    rf = _result_file(eval_dir, name, click_tag=click_tag, judge_tag=judge_tag)
    if rf.is_file():
        rf.unlink()
        logger.info("Wiped %s", rf)


def _dispatch(
    name: str,
    run_dir: Path,
    input_dir: str | None,
    results: dict[str, Any],
    visual_judge_model: str,
    vlm_click_model: str | None = None,
    *,
    click_tag: str = "default",
    judge_tag: str = "default",
    result_file: Path | None = None,
    resume: bool = False,
) -> Any:
    """Call the appropriate evaluator function."""
    if name == "efficiency":
        from evaluator.evaluate.efficiency import evaluate_efficiency
        return evaluate_efficiency(str(run_dir))

    if name == "build":
        from evaluator.evaluate.build import evaluate_build
        return evaluate_build(str(run_dir))

    if name == "project_quality":
        from evaluator.evaluate.project_quality import evaluate_project_quality
        return evaluate_project_quality(str(run_dir))

    if name == "trajectory":
        from evaluator.evaluate.trajectory import analyze_trajectory
        return analyze_trajectory(str(run_dir))

    if name == "navigation_test":
        return _dispatch_navigation_test(
            run_dir, input_dir, results, vlm_click_model,
            click_tag=click_tag, result_file=result_file, resume=resume,
        )

    if name == "navigation_visual":
        return _dispatch_navigation_visual(
            run_dir, input_dir, results, visual_judge_model,
            click_tag=click_tag, judge_tag=judge_tag,
            result_file=result_file, resume=resume,
        )

    logger.warning("Unknown evaluator: %s", name)
    return None


def _dispatch_navigation_test(
    run_dir: Path,
    input_dir: str | None,
    results: dict[str, Any],
    vlm_click_model: str | None = None,
    *,
    click_tag: str = "default",
    result_file: Path | None = None,
    resume: bool = False,
) -> dict[str, Any] | None:
    """Run testgen navigation tests (Phase 1)."""
    build_result = results.get("build", {})
    if not build_result.get("build_passed"):
        logger.info("Skipping navigation_test: build not passed")
        return None

    if not input_dir:
        logger.info("Skipping navigation_test: no input_dir")
        return None

    cases_path = _resolve_reviewed(Path(input_dir), "test_cases.json")
    if not cases_path.is_file():
        logger.info("Skipping navigation_test: test_cases.json not found at %s", cases_path)
        return None

    try:
        from testgen.runner import run_tests
    except ImportError:
        logger.warning("Skipping navigation_test: testgen package not importable")
        return None

    if result_file is None:
        result_file = run_dir / "eval" / "navigation_test" / click_tag / "test_report.json"
    screenshots_dir = run_dir / "eval" / "test_screenshots" / click_tag

    return run_tests(
        cases_path=cases_path,
        run_dir=run_dir,
        input_dir=input_dir,
        report_path=result_file,
        screenshots_dir=screenshots_dir,
        vlm_model=vlm_click_model,
        resume=resume,
    )


def _dispatch_navigation_visual(
    run_dir: Path,
    input_dir: str | None,
    results: dict[str, Any],
    visual_judge_model: str,
    *,
    click_tag: str = "default",
    judge_tag: str = "default",
    result_file: Path | None = None,
    resume: bool = False,
) -> dict[str, Any] | None:
    """Run testgen visual evaluation (Phase 2) on existing test report."""
    nav_result = results.get("navigation_test")
    if not nav_result:
        logger.info("Skipping navigation_visual: no navigation_test result")
        return None

    if not input_dir:
        logger.info("Skipping navigation_visual: no input_dir")
        return None

    cases_path = _resolve_reviewed(Path(input_dir), "test_cases.json")
    if not cases_path.is_file():
        logger.info("Skipping navigation_visual: test_cases.json not found")
        return None

    nav_report_path = (
        run_dir / "eval" / "navigation_test" / click_tag / "test_report.json"
    )
    if not nav_report_path.is_file():
        logger.info("Skipping navigation_visual: %s not found", nav_report_path)
        return None

    try:
        from testgen.runner import run_visual_evaluation
    except ImportError:
        logger.warning("Skipping navigation_visual: testgen package not importable")
        return None

    if result_file is None:
        result_file = (
            run_dir / "eval" / "navigation_visual" / click_tag / "visual_report.json"
        )

    return run_visual_evaluation(
        nav_report_path=nav_report_path,
        visual_report_path=result_file,
        cases_path=cases_path,
        input_dir=input_dir,
        vlm_model=visual_judge_model,
        judge_tag=judge_tag,
        resume=resume,
    )
