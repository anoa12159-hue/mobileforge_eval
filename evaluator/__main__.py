"""
CLI entry point for the experiment evaluator.

Subcommands:
  run     — Run an experiment (generate + evaluate)
  eval    — Standalone evaluation of existing run(s)
  report  — Generate report from an experiment directory
  status  — Show experiment status
  monitor — Launch web dashboard for real-time monitoring
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()
logger = logging.getLogger("evaluator")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evaluator",
        description="Experiment evaluator — orchestrate runs and evaluate output",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", help="Available subcommands")

    # ── run ──────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run an experiment from config")
    p_run.add_argument("--config", "-c", required=True, help="Path to exp_config.yaml")
    p_run.add_argument("--resume", action="store_true", help="Resume the latest matching experiment")
    p_run.add_argument("--skip", type=str, default=None, help="Comma-separated eval modules to skip")
    p_run.add_argument("--only", type=str, default=None, help="Comma-separated eval modules to run")
    p_run.add_argument("--force", action="store_true", help="Overwrite existing eval results")
    p_run.add_argument("--visual-judge-model", type=str, default=None, help="VLM judge model")
    p_run.add_argument("--no-plot", action="store_true", help="Skip generating token composition plots")
    p_run.add_argument("--x-axis", choices=["iteration", "phase", "both"], default="both",
                       help="Token composition plot x-axis: iteration, phase, or both (default: both)")

    # ── eval ─────────────────────────────────────────────────────────
    p_eval = sub.add_parser("eval", help="Evaluate existing run directories")
    p_eval.add_argument("--dir", "-d", default=None, help="Run directory or parent directory (exclusive with -c/--config)")
    p_eval.add_argument("--config", "-c", default=None,
                        help="Experiment config YAML; auto-locates latest <ts>_<name> dir and batch-evaluates its runs/")
    p_eval.add_argument("--input", "-i", type=str, default=None, help="Input directory (design images)")
    p_eval.add_argument("--batch", action="store_true", help="Evaluate all subdirs of --dir")
    p_eval.add_argument("--skip", type=str, default=None, help="Comma-separated eval modules to skip")
    p_eval.add_argument("--only", type=str, default=None, help="Comma-separated eval modules to run")
    p_eval.add_argument("--resume", action="store_true",
                        help="Case-level resume: keep partial artifacts, only run missing cases / judge pairs")
    p_eval.add_argument("--force", action="store_true",
                        help="Wipe every targeted module (including already-complete ones) and rerun from scratch. Use when prior eval results are wrong and need to be discarded.")
    p_eval.add_argument("--visual-judge-model", type=str, default=None, help="VLM judge model (default: from experiment.yaml testgen.eval_model)")
    p_eval.add_argument("--no-plot", action="store_true", help="Skip generating token composition plots")
    p_eval.add_argument("--x-axis", choices=["iteration", "phase", "both"], default="both",
                        help="Token composition plot x-axis: iteration, phase, or both (default: both)")

    # ── report ───────────────────────────────────────────────────────
    p_report = sub.add_parser("report", help="Generate report from experiment directory")
    p_report.add_argument("--experiment-dir", "-e", required=True, help="Experiment directory")

    # ── status ───────────────────────────────────────────────────────
    p_status = sub.add_parser("status", help="Show experiment status")
    p_status.add_argument("experiment_dir", help="Experiment directory path")

    # ── monitor ──────────────────────────────────────────────────────
    p_mon = sub.add_parser("monitor", help="Launch web dashboard for real-time monitoring")
    p_mon.add_argument("experiment_dir", help="Experiment directory path")
    p_mon.add_argument("--port", "-p", type=int, default=8080, help="HTTP port (default: 8080)")
    p_mon.add_argument("--host", type=str, default="127.0.0.1",
                       help="Bind address (default: 127.0.0.1, use 0.0.0.0 for remote access)")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "eval":
        _cmd_eval(args)
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "monitor":
        _cmd_monitor(args)
    else:
        parser.print_help()
        sys.exit(1)


# ======================================================================
# Subcommand handlers
# ======================================================================


def _cmd_run(args: argparse.Namespace) -> None:
    from evaluator.config import load_experiment_config
    from evaluator.runner import run_experiment

    cfg = load_experiment_config(args.config)
    console.print(f"[bold]Loaded config: {cfg.name}[/bold]")
    console.print(
        f"  {len(cfg.apps)} apps × {len(cfg.models)} models × "
        f"{len(cfg.methods)} methods = {cfg.total_runs} runs"
    )
    if len(cfg.apps) <= 20:
        for app in cfg.apps:
            console.print(f"    [dim]{app}[/dim]")
    else:
        for app in cfg.apps[:5]:
            console.print(f"    [dim]{app}[/dim]")
        console.print(f"    [dim]... and {len(cfg.apps) - 5} more[/dim]")

    run_experiment(
        cfg,
        resume=args.resume,
        skip=_split_list(args.skip),
        only=_split_list(args.only),
        force=args.force,
        visual_judge_model=args.visual_judge_model,
        verbose=args.verbose,
        plot=not args.no_plot,
        x_axis=getattr(args, "x_axis", "both"),
    )


def _cmd_eval(args: argparse.Namespace) -> None:
    skip = _split_list(args.skip)
    only = _split_list(args.only)
    plot = not args.no_plot
    x_axis = getattr(args, "x_axis", "both")
    resume = bool(getattr(args, "resume", False))
    force = bool(getattr(args, "force", False))

    if not args.dir and not args.config:
        console.print("[red]eval: must provide either --dir or --config[/red]")
        sys.exit(1)
    if args.dir and args.config:
        console.print("[red]eval: --dir and --config are mutually exclusive[/red]")
        sys.exit(1)

    if args.config:
        _eval_from_config(
            args.config, args.input, skip, only,
            resume=resume, force=force,
            visual_judge_model=args.visual_judge_model,
            plot=plot, x_axis=x_axis,
        )
        return

    target = Path(args.dir)
    if args.batch:
        _eval_batch(
            target, args.input, skip, only, force,
            args.visual_judge_model, plot=plot, x_axis=x_axis,
            resume=resume,
        )
    else:
        _eval_single(
            target, args.input, skip, only, force,
            args.visual_judge_model, plot=plot, x_axis=x_axis,
            resume=resume,
        )


def _eval_from_config(
    config_path: str,
    input_dir: str | None,
    skip: list[str] | None,
    only: list[str] | None,
    *,
    resume: bool,
    force: bool,
    visual_judge_model: str | None,
    plot: bool,
    x_axis: str,
) -> None:
    """Config-driven eval: locate <ts>_<cfg.name> and batch-evaluate runs/.

    This mode does NOT create a new experiment dir — if none exists, we tell
    the user to run ``python -m evaluator run -c <config>`` first.

    Applies ``evaluation.selection.models`` as a run-dir filter and
    ``evaluation.selection.skip_modules`` as the default --skip list when
    the CLI didn't supply --skip/--only.
    """
    from evaluator.config import load_experiment_config
    from evaluator.utils import sanitize_model_tag

    cfg = load_experiment_config(config_path)
    console.print(f"[bold]Loaded config: {cfg.name}[/bold]")

    experiment_dir = _find_latest_experiment_dir(cfg)
    if experiment_dir is None:
        console.print(
            f"[red]No experiment dir matching '*_{cfg.name}' under "
            f"{_experiments_root(cfg)}.[/red] "
            f"Run `python -m evaluator run -c {config_path}` first."
        )
        sys.exit(1)

    runs_root = experiment_dir / "runs"
    if not runs_root.is_dir():
        console.print(f"[red]Runs directory missing: {runs_root}[/red]")
        sys.exit(1)

    console.print(f"[bold]Experiment dir: {experiment_dir}[/bold]")

    selection = cfg.evaluation.selection
    model_tag_filter: set[str] | None = None
    if selection.models:
        model_tag_filter = {sanitize_model_tag(m) for m in selection.models}
        console.print(
            f"[dim]selection.models → only evaluating runs with model in "
            f"{sorted(model_tag_filter)}[/dim]"
        )

    # skip: CLI > config.selection.skip_modules > None
    effective_skip = skip
    if effective_skip is None and only is None and selection.skip_modules:
        effective_skip = list(selection.skip_modules)
        console.print(
            f"[dim]selection.skip_modules → skipping {effective_skip}[/dim]"
        )

    # Build precise app_name -> input_dir lookup from cfg.apps (already expanded
    # to concrete app dirs). This lets us find the right input dir for each run
    # even when apps live in nested roots like ./input/mobileforge/<app>/,
    # which the generic infer_input_dir() fallback can't guess.
    apps_lookup: dict[str, str] = {}
    for app_path in cfg.apps:
        p = Path(app_path)
        if not p.is_absolute():
            from evaluator.experiment import _PROJECT_ROOT
            p = _PROJECT_ROOT / p
        apps_lookup[p.name] = str(p)

    _eval_batch(
        runs_root, input_dir, effective_skip, only, force,
        visual_judge_model, plot=plot, x_axis=x_axis,
        resume=resume,
        model_tag_filter=model_tag_filter,
        apps_lookup=apps_lookup,
    )


def _experiments_root(cfg) -> Path:
    from evaluator.experiment import _EXPERIMENTS_ROOT, _PROJECT_ROOT

    if cfg.output_dir:
        p = Path(cfg.output_dir)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return p
    return _EXPERIMENTS_ROOT


def _find_latest_experiment_dir(cfg) -> Path | None:
    """Pure read-only: find the most recent <ts>_<cfg.name> dir, or None."""
    root = _experiments_root(cfg)
    if not root.is_dir():
        return None
    candidates = sorted(
        (d for d in root.iterdir()
         if d.is_dir() and d.name.endswith(f"_{cfg.name}")),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _eval_single(
    run_dir: Path,
    input_dir: str | None,
    skip: list[str] | None,
    only: list[str] | None,
    force: bool,
    visual_judge_model: str,
    *,
    plot: bool = True,
    x_axis: str = "both",
    resume: bool = False,
) -> None:
    from evaluator.evaluate.pipeline import evaluate_run, EvaluationPreconditionError

    console.print(f"\n[bold]Evaluating: {run_dir}[/bold]")

    try:
        results = evaluate_run(
            run_dir=str(run_dir),
            input_dir=input_dir,
            skip=skip,
            only=only,
            force=force,
            resume=resume,
            visual_judge_model=visual_judge_model,
            plot=plot,
            x_axis=x_axis,
        )
    except EvaluationPreconditionError as exc:
        console.print(f"[red]Cannot evaluate: {exc}[/red]")
        sys.exit(1)

    _print_eval_results(run_dir.name, results)


def _eval_batch(
    parent_dir: Path,
    input_dir: str | None,
    skip: list[str] | None,
    only: list[str] | None,
    force: bool,
    visual_judge_model: str,
    *,
    plot: bool = True,
    x_axis: str = "both",
    resume: bool = False,
    model_tag_filter: set[str] | None = None,
    apps_lookup: dict[str, str] | None = None,
) -> None:
    from evaluator.evaluate.pipeline import (
        evaluate_run,
        EvaluationPreconditionError,
        is_run_fully_evaluated,
    )
    from evaluator.utils import infer_input_dir

    if not parent_dir.is_dir():
        console.print(f"[red]Directory not found: {parent_dir}[/red]")
        sys.exit(1)

    run_dirs = sorted(
        d for d in parent_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "src").is_dir()
    )

    if model_tag_filter:
        def _run_model_tag(p: Path) -> str:
            parts = p.name.split("__")
            return parts[1] if len(parts) >= 2 else ""
        before = len(run_dirs)
        run_dirs = [d for d in run_dirs if _run_model_tag(d) in model_tag_filter]
        skipped = before - len(run_dirs)
        if skipped:
            console.print(
                f"[dim]Filtered by model: {before} → {len(run_dirs)} runs "
                f"({skipped} excluded)[/dim]"
            )

    if not run_dirs:
        console.print(f"[red]No evaluable run directories in {parent_dir}[/red]")
        sys.exit(1)

    console.print(f"[bold]Batch eval: {len(run_dirs)} runs in {parent_dir}[/bold]")

    results_summary: dict[str, str] = {}
    for rd in run_dirs:
        console.print(f"\n[blue]▸ {rd.name}[/blue]")

        run_input = None
        app_name = rd.name.split("__")[0]
        if input_dir:
            candidate = Path(input_dir) / app_name
            run_input = str(candidate) if candidate.is_dir() else input_dir
        elif apps_lookup and app_name in apps_lookup:
            run_input = apps_lookup[app_name]
        else:
            inferred = infer_input_dir(rd)
            run_input = str(inferred) if inferred else None

        # --resume shortcut: if the whole run is already complete, skip it.
        # --force deliberately does NOT short-circuit here: users reach for --force
        # to nuke bad results, so we must enter evaluate_run which will wipe and
        # re-run each requested module (including already-complete ones).
        if resume and not force and is_run_fully_evaluated(
            rd, skip=skip, only=only,
            visual_judge_model=visual_judge_model,
        ):
            results_summary[rd.name] = "⊘ skipped (already complete)"
            console.print("  [dim]Skipped: already complete[/dim]")
            continue

        try:
            results = evaluate_run(
                run_dir=str(rd),
                input_dir=run_input,
                skip=skip,
                only=only,
                force=force,
                resume=resume,
                visual_judge_model=visual_judge_model,
                plot=plot,
                x_axis=x_axis,
            )
            completed = [k for k in results if results[k] is not None]
            results_summary[rd.name] = f"✓ {len(completed)} modules"
        except EvaluationPreconditionError:
            results_summary[rd.name] = "⊘ skipped (incomplete)"
            console.print("  [dim]Skipped: incomplete run[/dim]")
        except Exception as exc:
            results_summary[rd.name] = f"✗ {exc}"
            console.print(f"  [red]Failed: {exc}[/red]")

    # Batch summary
    table = Table(title="Batch Evaluation Summary", show_lines=True)
    table.add_column("Run", style="cyan")
    table.add_column("Result", justify="center")
    for name, status in results_summary.items():
        style = ""
        if status.startswith("✓"):
            style = "green"
        elif status.startswith("✗"):
            style = "red"
        else:
            style = "dim"
        table.add_row(name, f"[{style}]{status}[/{style}]")
    console.print("\n")
    console.print(table)


def _cmd_report(args: argparse.Namespace) -> None:
    from evaluator.report.aggregator import aggregate_results
    from evaluator.report.export_tables import export_tables
    from evaluator.report.table import generate_comparison_table
    from evaluator.utils import atomic_json_write, read_json

    ed = Path(args.experiment_dir)
    if not ed.is_dir():
        console.print(f"[red]Directory not found: {ed}[/red]")
        sys.exit(1)

    console.print(f"[bold]Generating report for: {ed}[/bold]")

    aggregated = aggregate_results(str(ed))
    if not aggregated:
        console.print("[red]No results to aggregate[/red]")
        sys.exit(1)

    report_dir = ed / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    atomic_json_write(report_dir / "summary.json", aggregated)
    console.print(f"  [green]✓[/green] summary.json")

    tables = export_tables(str(ed), aggregated)
    for t in tables:
        console.print(f"  [green]✓[/green] {Path(t).name}")

    md = generate_comparison_table(aggregated)
    (report_dir / "comparison_table.md").write_text(md, encoding="utf-8")
    console.print(f"  [green]✓[/green] comparison_table.md")

    # Token composition comparison plot across all runs
    try:
        from evaluator.evaluate.trajectory import plot_token_composition_comparison

        runs_dir = ed / "runs"
        if not runs_dir.is_dir():
            runs_dir = ed

        pairs: list[tuple[str, dict]] = []
        for rd in sorted(runs_dir.iterdir()):
            traj_file = rd / "eval" / "trajectory.json"
            if traj_file.is_file():
                traj_data = read_json(traj_file)
                if traj_data and "token_attribution" in traj_data:
                    label = rd.name.replace("__", " / ")
                    pairs.append((label, traj_data))

        if pairs:
            out = plot_token_composition_comparison(
                pairs, report_dir / "token_composition_comparison.png",
            )
            if out:
                console.print(f"  [green]✓[/green] token_composition_comparison.png")
    except Exception as exc:
        console.print(f"  [yellow]⚠[/yellow] Token composition plot skipped: {exc}")

    console.print(f"\n[bold green]Report generated → {report_dir}[/bold green]")


def _cmd_monitor(args: argparse.Namespace) -> None:
    from evaluator.dashboard import start_dashboard

    ed = Path(args.experiment_dir)
    if not ed.is_dir():
        console.print(f"[red]Directory not found: {ed}[/red]")
        sys.exit(1)

    start_dashboard(ed, host=args.host, port=args.port)


def _cmd_status(args: argparse.Namespace) -> None:
    from evaluator.utils import read_json

    ed = Path(args.experiment_dir)
    status_path = ed / "status.json"

    if not status_path.is_file():
        console.print(f"[red]No status.json found in {ed}[/red]")
        sys.exit(1)

    status = read_json(status_path) or {}

    console.print(f"\n[bold]Experiment: {status.get('experiment_id', ed.name)}[/bold]")
    console.print(f"  Strategy: {status.get('eval_strategy', '?')}")
    console.print(f"  Started:  {status.get('started_at', '?')}")
    console.print(f"  Total:    {status.get('total_runs', '?')}")

    counts = status.get("counts", {})
    if counts:
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        console.print(f"  Status:   {' | '.join(parts)}")

    runs = status.get("runs", {})
    if runs:
        table = Table(title="Run Status", show_lines=True)
        table.add_column("Run ID", style="cyan", max_width=60)
        table.add_column("Status", justify="center")
        table.add_column("Gen Time", justify="right")
        table.add_column("Eval Time", justify="right")

        status_styles = {
            "evaluated": "green",
            "generated": "blue",
            "generating": "yellow",
            "evaluating": "yellow",
            "gen_failed": "red",
            "eval_failed": "red",
            "pending": "dim",
        }

        for run_id, info in sorted(runs.items()):
            st = info.get("status", "pending")
            style = status_styles.get(st, "")
            gen_time = f"{info.get('generation_time_sec', 0):.0f}s" if "generation_time_sec" in info else "—"
            eval_time = f"{info.get('evaluation_time_sec', 0):.0f}s" if "evaluation_time_sec" in info else "—"
            table.add_row(run_id, f"[{style}]{st}[/{style}]", gen_time, eval_time)

        console.print()
        console.print(table)


# ======================================================================
# Helpers
# ======================================================================


def _split_list(val: str | None) -> list[str] | None:
    if not val:
        return None
    return [s.strip() for s in val.split(",") if s.strip()]


def _print_eval_results(run_name: str, results: dict) -> None:
    """Print a summary of evaluation results for a single run."""
    table = Table(title=f"Evaluation: {run_name}", show_lines=True)
    table.add_column("Module", style="cyan")
    table.add_column("Key Metrics")

    for module, data in results.items():
        if data is None:
            continue
        summary = _summarize_module(module, data)
        table.add_row(module, summary)

    console.print()
    console.print(table)


def _summarize_module(module: str, data: dict) -> str:
    """One-line summary for each eval module."""
    if module == "efficiency":
        return (
            f"tokens={data.get('total_tokens', 0):,} | "
            f"calls={data.get('llm_call_count', 0)} | "
            f"time={data.get('llm_time_sec', 0):.0f}s"
        )
    if module == "build":
        status = "[green]PASS[/green]" if data.get("build_passed") else "[red]FAIL[/red]"
        return (
            f"build={status} | "
            f"routes={data.get('route_count', 0)} | "
            f"pages={data.get('page_count', 0)}"
        )
    if module == "project_quality":
        scale = data.get("scale", {})
        reuse = data.get("component_reuse", {})
        return (
            f"loc={scale.get('total_loc', 0)} | "
            f"files={scale.get('file_count', 0)} | "
            f"reuse={reuse.get('component_reuse_rate', 0):.1f}"
        )
    if module == "trajectory":
        phases = data.get("phase_analysis", {})
        behavior = data.get("behavior_patterns", {})
        return (
            f"phases={phases.get('phase_count', 0)} | "
            f"repairs={behavior.get('self_repair', {}).get('verify_fail_count', 0)}"
        )
    if module == "navigation_test":
        passed = data.get("passed", 0)
        total = data.get("total_cases", 0)
        rate = data.get("pass_rate", 0)
        return (
            f"pass={passed}/{total} ({rate:.1%})"
        )
    if module == "navigation_visual":
        vs = data.get("visual_summary")
        if vs:
            avg_b = vs.get("avg_before")
            avg_a = vs.get("avg_after")
            b_str = f"{avg_b:.2f}" if avg_b is not None else "n/a"
            a_str = f"{avg_a:.2f}" if avg_a is not None else "n/a"
            return f"before={b_str} | after={a_str}"
        return "no visual scores"
    return str(data)[:80]


if __name__ == "__main__":
    main()
