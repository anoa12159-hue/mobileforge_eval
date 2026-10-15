"""
Batch runner — iterates RunSpecs, invokes designforge pipeline, manages eval.

Handles three eval_strategy modes: per_run, batch, manual.
Each run is isolated — a single failure does not abort the batch.

Supports parallel generation via ProcessPoolExecutor when
``ExperimentConfig.max_parallel_runs > 1``.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from evaluator.config import ExperimentConfig
from evaluator.experiment import (
    EVALUATED,
    EVALUATING,
    EVAL_FAILED,
    GENERATED,
    GENERATING,
    GEN_FAILED,
    PENDING,
    Experiment,
    RunSpec,
)
from evaluator.utils import atomic_json_write, infer_input_dir

logger = logging.getLogger(__name__)
console = Console()


# ------------------------------------------------------------------
# Engine parameter injection
# ------------------------------------------------------------------


def _configure_engine(spec: RunSpec) -> None:
    """Override designforge.config module-level variables for this run."""
    try:
        from designforge import config as cfg

        cfg.AGENT_MODEL = spec.model
        cfg.VISION_MODEL = spec.model
        cfg.TEXT_MODEL = spec.model
    except ImportError:
        logger.warning("designforge not importable; engine config override skipped")


# ------------------------------------------------------------------
# Generation
# ------------------------------------------------------------------


def _run_generation(
    spec: RunSpec,
    run_dir: Path,
    verbose: bool = False,
    *,
    skip_registry: bool = False,
) -> bool:
    """Call designforge to generate code for a single run. Returns success.

    Parameters
    ----------
    skip_registry:
        When True, skip TaskRegistry read/write to avoid concurrent
        writes to the shared tasks.json in parallel mode.
    """
    from designforge.baselines import BASELINE_REGISTRY
    from designforge.agentic.runner import discover_description, discover_images
    from designforge.logging_config import setup_logging
    from designforge.metrics import init_metrics
    from designforge.tools.file_ops import set_sandbox

    input_dir = Path(spec.app_path)
    if not input_dir.is_absolute():
        input_dir = Path(__file__).parent.parent / input_dir

    forge_dir = run_dir / ".designforge"
    setup_logging(forge_dir / "logs", verbose=verbose, console=console)
    mc = init_metrics(trace_dir=forge_dir / "traces")
    set_sandbox(str(run_dir))

    images = discover_images(input_dir)
    if not images:
        console.print(f"[red]No images found in {input_dir}[/red]")
        return False

    desc = discover_description(input_dir)

    registry = None
    if not skip_registry:
        from designforge.task_registry import TaskRecord, TaskRegistry

        registry = TaskRegistry(run_dir.parent)
        record = TaskRecord(
            name=spec.run_id,
            input_dir=str(input_dir),
            output_dir=str(run_dir),
            status="running",
            config={
                "method": spec.method,
                "model": spec.model,
                "context_policy": spec.context_policy,
                "suffix": spec.suffix,
            },
            image_count=len(images),
            description=desc,
        )
        registry.register(record)

    baseline_cls = BASELINE_REGISTRY.get(spec.method)
    if baseline_cls is None:
        console.print(f"[red]Unknown method: {spec.method}[/red]")
        return False

    from designforge.llm_client import LLMCallError

    try:
        baseline = baseline_cls()
        success = baseline.run(
            image_paths=images,
            output_dir=str(run_dir),
            desc=desc,
            framework="react",
            css_strategy="tailwind",
        )
    except LLMCallError:
        raise
    except Exception as exc:
        console.print(f"[red]Generation error: {exc}[/red]")
        logger.exception("Generation failed for %s", spec.run_id)
        success = False

    try:
        if mc:
            mc.save_report(forge_dir / "metrics.json")
    except Exception as exc:
        logger.warning("Metrics save failed: %s", exc)

    if registry is not None:
        registry.update_status(spec.run_id, "success" if success else "failed")
    return success


# ------------------------------------------------------------------
# Parallel generation support
# ------------------------------------------------------------------


def _generation_worker(
    spec: RunSpec, run_dir_str: str, verbose: bool
) -> dict[str, Any]:
    """Top-level function executed in a child process for one generation run.

    Must be a module-level function (not a closure) so that
    ``ProcessPoolExecutor`` can pickle it.  Returns a result dict consumed
    by the main process.
    """
    run_dir = Path(run_dir_str)
    _configure_engine(spec)

    from designforge.llm_client import LLMCallError

    t0 = time.time()
    try:
        success = _run_generation(spec, run_dir, verbose=verbose, skip_registry=True)
        elapsed = time.time() - t0
        return {
            "run_id": spec.run_id,
            "model": spec.model,
            "success": success,
            "elapsed": round(elapsed, 1),
            "error": None,
            "llm_error": False,
        }
    except LLMCallError as exc:
        elapsed = time.time() - t0
        return {
            "run_id": spec.run_id,
            "model": spec.model,
            "success": False,
            "elapsed": round(elapsed, 1),
            "error": str(exc),
            "llm_error": True,
        }
    except Exception as exc:
        elapsed = time.time() - t0
        return {
            "run_id": spec.run_id,
            "model": spec.model,
            "success": False,
            "elapsed": round(elapsed, 1),
            "error": str(exc),
            "llm_error": False,
        }


def _run_generation_parallel(
    experiment: "Experiment",
    specs: list[RunSpec],
    max_workers: int,
    *,
    verbose: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Run the generation phase with up to *max_workers* parallel processes.

    Returns ``(results, llm_fail_details)`` dicts identical in shape to
    those produced by the serial path so that downstream evaluation and
    reporting work unchanged.
    """
    results: dict[str, str] = {}
    llm_fail_details: dict[str, str] = {}

    # Partition specs: already done vs need generation
    gen_tasks: list[tuple[RunSpec, str]] = []
    for spec in specs:
        status = experiment.get_run_status(spec.run_id)
        if status in (GENERATED, EVALUATED, EVALUATING):
            results[spec.run_id] = status
            continue
        if status == GENERATING:
            # Stale "generating" from a previous crashed/killed run — treat as failed
            experiment.update_run_status(spec.run_id, GEN_FAILED)
            status = GEN_FAILED
        if status not in (GEN_FAILED, PENDING, EVAL_FAILED):
            results[spec.run_id] = status
            continue
        needs_clean = status == GEN_FAILED
        run_dir = experiment.create_run_dir(spec, clean=needs_clean)
        experiment.update_run_status(spec.run_id, GENERATING)
        gen_tasks.append((spec, str(run_dir)))

    if not gen_tasks:
        console.print("[dim]No generation tasks to run.[/dim]")
        return results, llm_fail_details

    console.print(
        f"\n[bold]Parallel generation: {len(gen_tasks)} tasks, "
        f"{max_workers} workers[/bold]"
    )

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as executor:
        future_map = {
            executor.submit(_generation_worker, spec, rd, verbose): spec
            for spec, rd in gen_tasks
        }

        done_count = 0
        total = len(gen_tasks)
        for future in as_completed(future_map):
            spec = future_map[future]
            done_count += 1

            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "run_id": spec.run_id,
                    "model": spec.model,
                    "success": False,
                    "elapsed": 0,
                    "error": str(exc),
                    "llm_error": False,
                }

            rid = result["run_id"]
            elapsed = result["elapsed"]

            if result["error"]:
                experiment.update_run_status(
                    rid,
                    GEN_FAILED,
                    extra={
                        "generation_time_sec": elapsed,
                        "generation_success": False,
                        "llm_call_error": result["llm_error"],
                    },
                )
                results[rid] = "llm_failed" if result["llm_error"] else GEN_FAILED
                llm_fail_details[rid] = result["error"]
                console.print(
                    f"  [{done_count}/{total}] [red]{rid}: "
                    f"failed ({elapsed:.0f}s)[/red]"
                )
            elif result["success"]:
                experiment.update_run_status(
                    rid,
                    GENERATED,
                    extra={
                        "generation_time_sec": elapsed,
                        "generation_success": True,
                    },
                )
                results[rid] = GENERATED
                console.print(
                    f"  [{done_count}/{total}] [green]{rid}: "
                    f"done ({elapsed:.0f}s)[/green]"
                )
            else:
                experiment.update_run_status(
                    rid,
                    GEN_FAILED,
                    extra={
                        "generation_time_sec": elapsed,
                        "generation_success": False,
                    },
                )
                results[rid] = GEN_FAILED
                console.print(
                    f"  [{done_count}/{total}] [red]{rid}: "
                    f"gen_failed ({elapsed:.0f}s)[/red]"
                )

    return results, llm_fail_details


# ------------------------------------------------------------------
# Evaluation
# ------------------------------------------------------------------


def _run_evaluation(
    spec: RunSpec,
    run_dir: Path,
    cfg: ExperimentConfig,
    *,
    skip: list[str] | None = None,
    only: list[str] | None = None,
    force: bool = False,
    resume: bool = False,
    visual_judge_model: str | None = None,
    plot: bool = True,
    x_axis: str = "both",
) -> dict[str, Any]:
    """Evaluate a single completed run."""
    from evaluator.evaluate.pipeline import evaluate_run

    input_dir = infer_input_dir(run_dir, spec.app_path)
    input_dir_str = str(input_dir) if input_dir else None

    return evaluate_run(
        run_dir=str(run_dir),
        input_dir=input_dir_str,
        skip=skip,
        only=only,
        force=force,
        resume=resume,
        visual_judge_model=visual_judge_model or cfg.evaluation.visual_judge_model,
        plot=plot,
        x_axis=x_axis,
    )


# ------------------------------------------------------------------
# Batch orchestration
# ------------------------------------------------------------------


def run_experiment(
    cfg: ExperimentConfig,
    *,
    resume: bool = False,
    skip: list[str] | None = None,
    only: list[str] | None = None,
    force: bool = False,
    visual_judge_model: str | None = None,
    verbose: bool = False,
    plot: bool = True,
    x_axis: str = "both",
) -> Experiment:
    # Internal: when resuming a batch, propagate resume flag to inner eval
    # so testgen's case-level resume kicks in. force still means "wipe+rerun"
    # per pipeline semantics.
    """Execute a full experiment: generate + evaluate according to eval_strategy."""
    experiment = Experiment(cfg, resume=resume)
    specs = experiment.get_pending_runs() if resume else experiment.expand_matrix()

    console.print(
        f"\n[bold]Experiment: {cfg.name}[/bold]  "
        f"({len(specs)} runs, strategy={cfg.evaluation.eval_strategy})"
    )
    if cfg.max_parallel_runs > 1:
        console.print(
            f"  [bold cyan]max_parallel_runs={cfg.max_parallel_runs}[/bold cyan]"
        )

    results: dict[str, str] = {}
    llm_fail_details: dict[str, str] = {}

    # ── Phase 1: Generation ─────────────────────────────────────────
    if cfg.max_parallel_runs > 1:
        # ---- Parallel generation path ----
        results, llm_fail_details = _run_generation_parallel(
            experiment, specs, cfg.max_parallel_runs, verbose=verbose,
        )

        # Serial per-run evaluation after parallel generation
        if cfg.evaluation.eval_strategy == "per_run":
            spec_map = {s.run_id: s for s in specs}
            for rid, status in list(results.items()):
                if status != GENERATED:
                    continue
                spec = spec_map.get(rid)
                if spec is None:
                    continue
                run_dir = experiment.runs_dir / rid
                experiment.update_run_status(rid, EVALUATING)
                try:
                    t0 = time.time()
                    _run_evaluation(
                        spec, run_dir, cfg,
                        skip=skip, only=only, force=force, resume=resume,
                        visual_judge_model=visual_judge_model,
                        plot=plot, x_axis=x_axis,
                    )
                    elapsed = time.time() - t0
                    experiment.update_run_status(
                        rid, EVALUATED,
                        extra={"evaluation_time_sec": round(elapsed, 1)},
                    )
                    console.print(f"  [green]✓ {rid} evaluated in {elapsed:.0f}s[/green]")
                    results[rid] = EVALUATED
                except Exception as exc:
                    experiment.update_run_status(rid, EVAL_FAILED)
                    console.print(f"  [red]✗ {rid} eval failed: {exc}[/red]")
                    logger.exception("Eval failed for %s", rid)
                    results[rid] = EVAL_FAILED
    else:
        # ---- Original serial path (unchanged) ----
        from designforge.llm_client import LLMCallError

        _MAX_CONSECUTIVE_LLM_FAIL = 2
        model_consecutive_llm_failures: dict[str, int] = {}
        skipped_models: set[str] = set()

        for i, spec in enumerate(specs, 1):
            current_status = experiment.get_run_status(spec.run_id)

            console.print(
                f"\n[bold blue]▸ [{i}/{len(specs)}] {spec.run_id}[/bold blue]"
            )

            if spec.model in skipped_models:
                results[spec.run_id] = "skipped"
                console.print(
                    f"  [yellow]SKIP: 模型 {spec.model} 已因连续 LLM 失败被跳过[/yellow]"
                )
                continue

            if current_status in (GENERATED, EVALUATED, EVALUATING):
                console.print("  [dim]Already generated, skipping generation[/dim]")
            elif current_status not in (GEN_FAILED, PENDING, EVAL_FAILED):
                pass
            else:
                _configure_engine(spec)
                needs_clean = current_status == GEN_FAILED
                run_dir = experiment.create_run_dir(spec, clean=needs_clean)
                experiment.update_run_status(spec.run_id, GENERATING)

                try:
                    t0 = time.time()
                    success = _run_generation(spec, run_dir, verbose=verbose)
                    elapsed = time.time() - t0
                except LLMCallError as exc:
                    elapsed = time.time() - t0
                    experiment.update_run_status(
                        spec.run_id, GEN_FAILED,
                        extra={"generation_time_sec": round(elapsed, 1),
                               "generation_success": False,
                               "llm_call_error": True},
                    )
                    results[spec.run_id] = "llm_failed"
                    llm_fail_details[spec.run_id] = str(exc)
                    console.print(f"  [red bold]LLM 调用失败[/red bold]")
                    console.print(f"  [red]{exc}[/red]")

                    count = model_consecutive_llm_failures.get(spec.model, 0) + 1
                    model_consecutive_llm_failures[spec.model] = count
                    if count >= _MAX_CONSECUTIVE_LLM_FAIL:
                        skipped_models.add(spec.model)
                        console.print(
                            f"  [yellow bold]模型 {spec.model} 连续 {count} 次 LLM 失败，"
                            f"后续该模型的任务将被跳过[/yellow bold]"
                        )
                    continue

                if success:
                    experiment.update_run_status(
                        spec.run_id, GENERATED,
                        extra={"generation_time_sec": round(elapsed, 1),
                               "generation_success": True},
                    )
                    console.print(
                        f"  [green]✓ Generated in {elapsed:.0f}s[/green]"
                    )
                    model_consecutive_llm_failures[spec.model] = 0
                else:
                    experiment.update_run_status(
                        spec.run_id, GEN_FAILED,
                        extra={"generation_time_sec": round(elapsed, 1),
                               "generation_success": False},
                    )
                    console.print(f"  [red]✗ Generation failed[/red]")
                    results[spec.run_id] = GEN_FAILED
                    continue

            # Per-run evaluation (serial path)
            if cfg.evaluation.eval_strategy == "per_run":
                current_status = experiment.get_run_status(spec.run_id)
                if current_status in (GENERATED, EVAL_FAILED):
                    run_dir = experiment.runs_dir / spec.run_id
                    experiment.update_run_status(spec.run_id, EVALUATING)
                    try:
                        t0 = time.time()
                        _run_evaluation(
                            spec, run_dir, cfg,
                            skip=skip, only=only, force=force, resume=resume,
                            visual_judge_model=visual_judge_model,
                            plot=plot, x_axis=x_axis,
                        )
                        elapsed = time.time() - t0
                        experiment.update_run_status(
                            spec.run_id, EVALUATED,
                            extra={"evaluation_time_sec": round(elapsed, 1)},
                        )
                        console.print(f"  [green]✓ Evaluated in {elapsed:.0f}s[/green]")
                        results[spec.run_id] = EVALUATED
                    except Exception as exc:
                        experiment.update_run_status(spec.run_id, EVAL_FAILED)
                        console.print(f"  [red]✗ Evaluation failed: {exc}[/red]")
                        logger.exception("Eval failed for %s", spec.run_id)
                        results[spec.run_id] = EVAL_FAILED
                else:
                    results[spec.run_id] = current_status
            else:
                results[spec.run_id] = experiment.get_run_status(spec.run_id)

    # ── Phase 2: Batch evaluation (if strategy == "batch") ──────────
    if cfg.evaluation.eval_strategy == "batch":
        generated_ids = experiment.get_runs_with_status(GENERATED, EVAL_FAILED)
        if generated_ids:
            console.print(
                f"\n[bold]Batch evaluation: {len(generated_ids)} runs[/bold]"
            )
            spec_map = {s.run_id: s for s in experiment.expand_matrix()}
            for rid in generated_ids:
                spec = spec_map.get(rid)
                if spec is None:
                    continue
                run_dir = experiment.runs_dir / rid
                experiment.update_run_status(rid, EVALUATING)
                try:
                    t0 = time.time()
                    _run_evaluation(
                        spec, run_dir, cfg,
                        skip=skip, only=only, force=force, resume=resume,
                        visual_judge_model=visual_judge_model,
                        plot=plot, x_axis=x_axis,
                    )
                    elapsed = time.time() - t0
                    experiment.update_run_status(
                        rid, EVALUATED,
                        extra={"evaluation_time_sec": round(elapsed, 1)},
                    )
                    console.print(f"  [green]✓ {rid} evaluated in {elapsed:.0f}s[/green]")
                    results[rid] = EVALUATED
                except Exception as exc:
                    experiment.update_run_status(rid, EVAL_FAILED)
                    console.print(f"  [red]✗ {rid} eval failed: {exc}[/red]")
                    results[rid] = EVAL_FAILED

    # Phase 3: Report
    _generate_report(experiment)

    # Summary table
    _print_summary(results, llm_fail_details)

    return experiment


def _generate_report(experiment: Experiment) -> None:
    """Generate aggregated report for the experiment."""
    try:
        from evaluator.report.aggregator import aggregate_results
        from evaluator.report.export_tables import export_tables
        from evaluator.report.table import generate_comparison_table

        aggregated = aggregate_results(str(experiment.experiment_dir))
        if aggregated:
            export_tables(str(experiment.experiment_dir), aggregated)
            md = generate_comparison_table(aggregated)
            report_dir = experiment.report_dir
            (report_dir / "comparison_table.md").write_text(md, encoding="utf-8")
            atomic_json_write(report_dir / "summary.json", aggregated)
            console.print("[green]✓ Report generated[/green]")
    except Exception as exc:
        console.print(f"[yellow]Warning: report generation failed: {exc}[/yellow]")
        logger.exception("Report generation failed")


def _print_summary(
    results: dict[str, str],
    llm_fail_details: dict[str, str] | None = None,
) -> None:
    """Print a Rich summary table."""
    if len(results) <= 1:
        return
    table = Table(title="Experiment Summary", show_lines=True)
    table.add_column("Run", style="cyan", max_width=60)
    table.add_column("Status", justify="center")

    status_styles = {
        EVALUATED: "[green]✓ evaluated[/green]",
        GENERATED: "[blue]● generated[/blue]",
        GEN_FAILED: "[red]✗ gen_failed[/red]",
        EVAL_FAILED: "[yellow]✗ eval_failed[/yellow]",
        "llm_failed": "[red]✗ LLM 失败[/red]",
        "skipped": "[yellow]⊘ 已跳过[/yellow]",
    }
    for run_id, status in results.items():
        table.add_row(run_id, status_styles.get(status, status))

    console.print("\n")
    console.print(table)

    counts: dict[str, int] = {}
    for st in results.values():
        counts[st] = counts.get(st, 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    console.print(f"[bold]Total: {len(results)} runs — {', '.join(parts)}[/bold]")

    if llm_fail_details:
        console.print("\n[bold red]LLM 失败详情:[/bold red]")
        for name, detail in llm_fail_details.items():
            first_line = detail.split("\n")[0]
            console.print(f"  [red]{name}: {first_line}[/red]")
