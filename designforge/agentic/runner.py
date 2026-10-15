"""
CLI runner for the agentic pipeline.

Usage:
  python -m designforge.agentic --input ./input/wxreader
  python -m designforge.agentic --input ./input --batch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from designforge import config
from designforge.agentic.core import AgenticPipeline
from designforge.logging_config import setup_logging
from designforge.metrics import MetricsCollector, init_metrics
from designforge.node_env import NodeEnvironmentError, get_node_version_display, resolve_node_env
from designforge.scaffold import REQUIRED_NODE_MAJOR
from designforge.task_registry import TaskRecord, TaskRegistry
from designforge.tools.file_ops import set_sandbox

console = Console()

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DESC_FILENAMES = ["desc.txt", "desc.md", "description.txt", "description.md", "README.md", "README.txt"]


def discover_images(input_dir: Path) -> list[str]:
    return sorted(
        str(p) for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def discover_description(input_dir: Path) -> str:
    for name in DESC_FILENAMES:
        candidate = input_dir / name
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return ""


def discover_metadata(input_dir: Path) -> dict | None:
    """Load metadata from input directory, preferring reviewed version."""
    import json
    for name in ("metadata.reviewed.json", "metadata.json"):
        meta_path = input_dir / name
        if meta_path.is_file():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def _sanitize_model_tag(model_name: str) -> str:
    tag = model_name.rsplit("/", 1)[-1]
    return tag.replace(":", "-").replace(" ", "-")


def _derive_task_name(input_dir: Path, suffix: str = "") -> str:
    model_tag = _sanitize_model_tag(config.AGENT_MODEL)
    name = f"{input_dir.name}__agentic__{model_tag}"
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


def run_task(
    *,
    task_name: str,
    input_dir: Path,
    output_dir: Path,
    desc: str,
    framework: str,
    css: str,
    verbose: bool,
    registry: TaskRegistry,
) -> bool:
    """Run the agentic pipeline on a single input. Returns True on success."""
    console.print(f"\n[bold blue]Task: {task_name}[/bold blue]")
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
            "method": "agentic",
            "framework": framework,
            "css_strategy": css,
            "model": config.AGENT_MODEL,
        },
        image_count=len(images),
        description=func_desc,
    )
    registry.register(record)

    from designforge.llm_client import LLMCallError

    pipeline = AgenticPipeline()
    try:
        success = pipeline.run(
            image_paths=images,
            output_dir=str(output_dir),
            desc=func_desc,
            framework=framework,
            css_strategy=css,
            input_dir=str(input_dir),
        )
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
                        "method": "agentic",
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
        console.print(f"\n[bold green]✓ {task_name} succeeded → {output_dir}[/bold green]")
    else:
        console.print(f"\n[bold red]✗ {task_name} failed[/bold red]")

    return success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgenticForge: autonomous agent for design-to-code generation"
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
        help="Override task name",
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
        pipeline = AgenticPipeline()
        pipeline.run(
            image_paths=images,
            output_dir=args.output or "/tmp/dry-run",
            desc=func_desc,
            framework=args.framework,
            css_strategy=args.css,
            input_dir=str(input_dir),
            dry_run=True,
        )
        return

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

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        sys.exit(1)

    if args.batch:
        task_dirs = _discover_batch_dirs(input_dir)
        if not task_dirs:
            console.print(f"[red]No image subdirectories in {input_dir}[/red]")
            sys.exit(1)
        console.print(f"[bold]Batch: {len(task_dirs)} inputs[/bold]")
    else:
        task_dirs = [input_dir]

    from designforge.llm_client import LLMCallError

    _MAX_CONSECUTIVE_LLM_FAIL = 2
    results: dict[str, str] = {}
    llm_fail_details: dict[str, str] = {}
    consecutive_llm_failures = 0

    for tdir in task_dirs:
        task_name = args.task or _derive_task_name(tdir, args.suffix)
        out_dir = output_root / task_name

        if consecutive_llm_failures >= _MAX_CONSECUTIVE_LLM_FAIL:
            results[task_name] = "skipped"
            console.print(
                f"\n[yellow]SKIP {task_name}: 模型 {config.AGENT_MODEL} "
                f"连续 {consecutive_llm_failures} 次 LLM 调用失败，跳过剩余任务[/yellow]"
            )
            continue

        try:
            ok = run_task(
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
                results[task_name] = "success"
                consecutive_llm_failures = 0
            else:
                results[task_name] = "failed"
        except LLMCallError as exc:
            results[task_name] = "llm_failed"
            llm_fail_details[task_name] = str(exc)
            consecutive_llm_failures += 1
            console.print(f"\n[red bold]LLM 调用失败: {task_name}[/red bold]")
            console.print(f"  [red]{exc}[/red]")
        except Exception as exc:
            results[task_name] = "failed"
            console.print(f"\n[bold red]✗ {task_name} crashed: {exc}[/bold red]")

    _print_batch_summary(results, llm_fail_details)

    failed = [n for n, s in results.items() if s not in ("success", "skipped")]
    if failed:
        console.print(f"\n[bold red]{len(failed)}/{len(results)} runs failed[/bold red]")
        sys.exit(1)
    else:
        console.print(f"\n[bold green]All {len(results)} runs succeeded[/bold green]")


def _print_batch_summary(
    results: dict[str, str], llm_fail_details: dict[str, str]
) -> None:
    if len(results) <= 1:
        return

    _STATUS_DISPLAY = {
        "success": "[green]✓ success[/green]",
        "failed": "[red]✗ failed[/red]",
        "llm_failed": "[red]✗ LLM 失败[/red]",
        "skipped": "[yellow]⊘ 已跳过[/yellow]",
    }
    table = Table(title="Results", show_lines=True)
    table.add_column("Task", style="cyan")
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
