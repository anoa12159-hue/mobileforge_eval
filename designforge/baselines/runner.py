"""
Unified runner for baseline experiments.

Handles CLI argument parsing, logging/metrics setup, task registry integration,
and dispatching to the appropriate baseline method. Produces the same output
structure as DesignForge (output/{task_name}/ with .designforge/ metadata) for
consistent evaluation.

Usage:
  python -m designforge.baselines --method monolithic --input ./input/wxreader
  python -m designforge.baselines --method all --input ./input/wxreader
  python -m designforge.baselines --method all --input ./input --batch
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from designforge import config
from designforge.baselines import BASELINE_REGISTRY
from designforge.logging_config import setup_logging
from designforge.agentic.runner import IMAGE_EXTS, discover_description, discover_images
from designforge.metrics import MetricsCollector, init_metrics
from designforge.node_env import NodeEnvironmentError, get_node_version_display, resolve_node_env
from designforge.scaffold import REQUIRED_NODE_MAJOR
from designforge.task_registry import TaskRecord, TaskRegistry
from designforge.tools.file_ops import set_sandbox

console = Console()

ALL_METHODS = list(BASELINE_REGISTRY.keys())


def _sanitize_model_tag(model_name: str) -> str:
    """Turn a model name into a filesystem-safe short tag."""
    tag = model_name.rsplit("/", 1)[-1]
    return tag.replace(":", "-").replace(" ", "-")


def _derive_task_name(input_dir: Path, method: str, suffix: str = "") -> str:
    """Task name = {input_dir_name}__{method}__{model}[__{suffix}]."""
    model_tag = _sanitize_model_tag(config.AGENT_MODEL)
    name = f"{input_dir.name}__{method}__{model_tag}"
    if suffix:
        name = f"{name}__{suffix}"
    return name


def _discover_batch_dirs(parent: Path) -> list[Path]:
    dirs = []
    for child in sorted(parent.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            if any(any(child.glob(f"*{ext}")) for ext in IMAGE_EXTS):
                dirs.append(child)
    return dirs


def run_baseline_task(
    *,
    method_name: str,
    task_name: str,
    input_dir: Path,
    output_dir: Path,
    desc: str,
    framework: str,
    css: str,
    verbose: bool,
    registry: TaskRegistry,
) -> bool:
    """Run a single baseline method on a single input. Returns True on success."""
    baseline_cls = BASELINE_REGISTRY.get(method_name)
    if baseline_cls is None:
        console.print(f"[red]Unknown method: {method_name}[/red]")
        return False

    console.print(f"\n[bold blue]Baseline: {method_name} | Task: {task_name}[/bold blue]")
    console.print(f"  Input : {input_dir}")
    console.print(f"  Output: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    forge_dir = output_dir / ".designforge"
    log_file = setup_logging(forge_dir / "logs", verbose=verbose, console=console)
    mc = init_metrics(trace_dir=forge_dir / "traces")

    console.print(f"[dim]Log file: {log_file}[/dim]")

    set_sandbox(str(output_dir))

    images = discover_images(input_dir)
    if not images:
        console.print(f"[red]No images found in {input_dir}[/red]")
        return False

    console.print(f"Found [bold]{len(images)}[/bold] design images")

    func_desc = desc or discover_description(input_dir)
    if func_desc:
        console.print(f"  [green]✓[/green] Description: {func_desc[:80]}...")
    else:
        console.print("  [yellow]ℹ[/yellow] No description")

    record = TaskRecord(
        name=task_name,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        status="running",
        config={
            "method": method_name,
            "framework": framework,
            "css_strategy": css,
            "model": config.AGENT_MODEL,
        },
        image_count=len(images),
        description=func_desc,
    )
    registry.register(record)

    baseline = baseline_cls()
    run_kwargs: dict[str, Any] = dict(
        image_paths=images,
        output_dir=str(output_dir),
        desc=func_desc,
        framework=framework,
        css_strategy=css,
    )
    sig = inspect.signature(baseline.run)
    if "input_dir" in sig.parameters:
        run_kwargs["input_dir"] = str(input_dir)
    from designforge.llm_client import LLMCallError
    try:
        success = baseline.run(**run_kwargs)
    except LLMCallError:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        success = False

    result_info = None
    try:
        result_info = mc.get_totals() if mc else None
        if mc:
            mc.print_summary(console)
            mc.save_report(forge_dir / "metrics.json")
            if mc.trace_writer:
                mc.trace_writer.write_manifest(
                    config_info={
                        "method": method_name,
                        "model": config.AGENT_MODEL,
                    },
                    totals=result_info or {},
                )
    except Exception as exc:
        console.print(f"[yellow]Warning: metrics save failed: {exc}[/yellow]")

    try:
        registry.update_status(
            task_name,
            "success" if success else "failed",
            result=result_info,
        )
    except Exception as exc:
        console.print(f"[yellow]Warning: registry update failed: {exc}[/yellow]")

    if success:
        console.print(f"\n[bold green]✓ {method_name}/{task_name} succeeded → {output_dir}[/bold green]")
    else:
        console.print(f"\n[bold red]✗ {method_name}/{task_name} failed[/bold red]")

    return success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run baseline experiments for comparison with DesignForge"
    )
    parser.add_argument(
        "--method", "-m",
        type=str,
        required=True,
        choices=ALL_METHODS + ["all"],
        help="Baseline method to run (or 'all' for all methods)",
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=str(config.INPUT_DIR),
        help="Directory containing design images",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output root directory (default: ./output)",
    )
    parser.add_argument(
        "--task", "-t",
        type=str,
        default=None,
        help="Override task name (default: {input_dir}__{method})",
    )
    parser.add_argument(
        "--desc", "-d",
        type=str,
        default="",
        help="Functional description of the app",
    )
    parser.add_argument(
        "--framework",
        type=str,
        default=config.TARGET_FRAMEWORK,
        choices=["react", "vue", "nextjs"],
    )
    parser.add_argument(
        "--css",
        type=str,
        default=config.CSS_STRATEGY,
        choices=["tailwind", "css-modules", "styled-components"],
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: each subdirectory of --input as a separate task",
    )
    parser.add_argument(
        "--suffix", "-s",
        type=str,
        default="",
        help="Custom suffix appended to the task name (e.g. v2, experiment1)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt that would be sent to the model, then exit (no scaffold, no LLM call)",
    )
    args = parser.parse_args()

    if args.dry_run:
        input_dir = Path(args.input)
        images = discover_images(input_dir)
        func_desc = args.desc or discover_description(input_dir)
        baseline_cls = BASELINE_REGISTRY.get(args.method if args.method != "all" else "agentic")
        if baseline_cls is None:
            console.print(f"[red]Unknown method for dry-run: {args.method}[/red]")
            sys.exit(1)
        baseline = baseline_cls()
        run_kwargs: dict[str, Any] = dict(
            image_paths=images,
            output_dir=args.output or "/tmp/dry-run",
            desc=func_desc,
            framework=args.framework,
            css_strategy=args.css,
            dry_run=True,
        )
        sig = inspect.signature(baseline.run)
        if "input_dir" in sig.parameters:
            run_kwargs["input_dir"] = str(input_dir)
        if "dry_run" not in sig.parameters:
            console.print(f"[yellow]{args.method} does not support --dry-run[/yellow]")
            sys.exit(1)
        baseline.run(**run_kwargs)
        return

    # --- Node.js preflight ---
    try:
        node_env = resolve_node_env()
        console.print(
            f"[dim]Node.js: {get_node_version_display(node_env)} "
            f"(required >= {REQUIRED_NODE_MAJOR})[/dim]"
        )
    except NodeEnvironmentError as exc:
        console.print(f"[red bold]{exc}[/red bold]")
        sys.exit(1)

    output_root = Path(args.output) if args.output else config.OUTPUT_DIR
    registry = TaskRegistry(output_root)

    methods = ALL_METHODS if args.method == "all" else [args.method]
    input_dir = Path(args.input)

    if not input_dir.is_dir():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        sys.exit(1)

    if args.batch:
        task_dirs = _discover_batch_dirs(input_dir)
        if not task_dirs:
            console.print(f"[red]No image subdirectories in {input_dir}[/red]")
            sys.exit(1)
        console.print(
            f"[bold]Batch: {len(task_dirs)} inputs × {len(methods)} methods "
            f"= {len(task_dirs) * len(methods)} runs[/bold]"
        )
    else:
        task_dirs = [input_dir]

    from designforge.llm_client import LLMCallError

    _MAX_CONSECUTIVE_LLM_FAIL = 2
    results: dict[str, str] = {}
    llm_fail_details: dict[str, str] = {}
    consecutive_llm_failures = 0

    for method in methods:
        consecutive_llm_failures = 0
        for tdir in task_dirs:
            run_key = f"{method}/{tdir.name}"

            if consecutive_llm_failures >= _MAX_CONSECUTIVE_LLM_FAIL:
                results[run_key] = "skipped"
                console.print(
                    f"\n[yellow]SKIP {run_key}: 模型 {config.AGENT_MODEL} "
                    f"连续 {consecutive_llm_failures} 次 LLM 调用失败，跳过剩余任务[/yellow]"
                )
                continue

            try:
                task_name = args.task or _derive_task_name(tdir, method, args.suffix)
                out_dir = output_root / task_name
                ok = run_baseline_task(
                    method_name=method,
                    task_name=task_name,
                    input_dir=tdir,
                    output_dir=out_dir,
                    desc=args.desc,
                    framework=args.framework,
                    css=args.css,
                    verbose=args.verbose,
                    registry=registry,
                )
                if ok:
                    results[run_key] = "success"
                    consecutive_llm_failures = 0
                else:
                    results[run_key] = "failed"
            except LLMCallError as exc:
                results[run_key] = "llm_failed"
                llm_fail_details[run_key] = str(exc)
                consecutive_llm_failures += 1
                console.print(f"\n[red bold]LLM 调用失败: {run_key}[/red bold]")
                console.print(f"  [red]{exc}[/red]")
            except Exception as exc:
                results[run_key] = "failed"
                console.print(
                    f"\n[bold red]✗ {run_key} crashed: {exc}[/bold red]"
                )

    # --- Summary ---
    _print_batch_summary(results, llm_fail_details)

    failed = [n for n, s in results.items() if s not in ("success", "skipped")]
    if failed:
        console.print(f"\n[bold red]{len(failed)}/{len(results)} runs failed[/bold red]")
        sys.exit(1)
    else:
        console.print(f"\n[bold green]All {len(results)} runs succeeded[/bold green]")


_STATUS_DISPLAY = {
    "success": "[green]✓ success[/green]",
    "failed": "[red]✗ failed[/red]",
    "llm_failed": "[red]✗ LLM 失败[/red]",
    "skipped": "[yellow]⊘ 已跳过[/yellow]",
}


def _print_batch_summary(
    results: dict[str, str], llm_fail_details: dict[str, str]
) -> None:
    if len(results) <= 1:
        return

    table = Table(title="Baseline Results", show_lines=True)
    table.add_column("Method / Input", style="cyan")
    table.add_column("Result", justify="center")
    for name, status in results.items():
        table.add_row(name, _STATUS_DISPLAY.get(status, status))
    console.print("\n")
    console.print(table)

    if llm_fail_details:
        console.print("\n[bold red]LLM 失败详情:[/bold red]")
        for name, detail in llm_fail_details.items():
            first_line = detail.split("\n")[0]
            console.print(f"  [red]{name}: {first_line}[/red]")


if __name__ == "__main__":
    main()
