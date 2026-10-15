"""CLI entry point — ``python -m annotator``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from designforge.config import ANNOTATOR_MODEL, ANNOTATOR_WORKERS, INPUT_DIR
from annotator.describe import describe_screenshot
from annotator.analyze_app import analyze_app
from annotator.assemble import assemble_metadata, write_metadata

logger = logging.getLogger("annotator")
console = Console()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _find_images(directory: Path) -> list[Path]:
    """Return sorted list of image files in *directory*."""
    images = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images, key=lambda p: p.name)


def _annotate_app(input_dir: Path, model: str, *, describe_only: bool = False, force: bool = False, no_testgen: bool = False) -> bool:
    """Annotate a single app directory. Returns True on success."""
    metadata_path = input_dir / "metadata.json"
    if metadata_path.exists() and not force:
        console.print(f"[yellow]跳过[/yellow] {input_dir.name} — metadata.json 已存在 (使用 --force 覆盖)")
        return True

    images = _find_images(input_dir)
    if not images:
        console.print(f"[red]跳过[/red] {input_dir.name} — 未找到图片文件")
        return False

    console.print(f"\n[bold cyan]标注[/bold cyan] {input_dir.name} ({len(images)} 张截图)")
    console.print(f"  模型: {model}")

    # Phase 1: per-screenshot descriptors
    console.print(f"\n[bold]Phase 1[/bold] 单图描述 ({len(images)} 张)")
    descriptors: list[dict] = []
    for i, img in enumerate(images, 1):
        console.print(f"  [{i}/{len(images)}] {img.name} ... ", end="")
        try:
            desc = describe_screenshot(str(img), model)
            descriptors.append(desc)
            page_name = desc.get("page_name", "?") 
            page_type = desc.get("page_type", "?")
            console.print(f"[green]✓[/green] {page_name} ({page_type})")
        except Exception as e:
            console.print(f"[red]✗[/red] {e}")
            logger.exception("单图描述失败: %s", img.name)
            descriptors.append({"screenshot": img.name, "error": str(e)})

    # Phase 2: global analysis
    app_analysis = None
    if not describe_only:
        console.print(f"\n[bold]Phase 2[/bold] 全局分析 ... ", end="")
        try:
            image_paths = [str(img) for img in images]
            valid_descriptors = [d for d in descriptors if "error" not in d]
            app_analysis = analyze_app(image_paths, valid_descriptors, model)
            app_name = app_analysis.get("app_name", "?")
            console.print(f"[green]✓[/green] {app_name}")
        except Exception as e:
            console.print(f"[red]✗[/red] {e}")
            logger.exception("全局分析失败")

    # Assemble & write
    metadata = assemble_metadata(
        [str(img) for img in images],
        descriptors,
        app_analysis,
        model,
    )
    out_path = write_metadata(str(input_dir), metadata)

    _print_summary(metadata, out_path)

    # Auto-generate test cases from the freshly written metadata
    if not no_testgen:
        try:
            from testgen.generator import generate_from_file, export_cases
            cases = generate_from_file(out_path)
            if cases:
                cases_path = input_dir / "test_cases.json"
                export_cases(cases, cases_path)
                console.print(
                    f"[bold green]Testgen[/bold green] 自动生成 {len(cases)} 个测试用例 → {cases_path}"
                )
        except Exception as e:
            console.print(f"[yellow]Testgen 自动生成跳过[/yellow]: {e}")
            logger.warning("testgen auto-generate failed: %s", e)

    return True


def _print_summary(metadata: dict, out_path: Path) -> None:
    """Print annotation summary table."""
    pages = metadata.get("pages", [])
    type_counts: dict[str, int] = {}
    for p in pages:
        pt = p.get("page_type", "unknown")
        type_counts[pt] = type_counts.get(pt, 0) + 1

    console.print()
    table = Table(title="标注摘要", show_header=True, header_style="bold")
    table.add_column("字段", style="cyan")
    table.add_column("值")

    table.add_row("app", metadata.get("app_name", "—"))
    table.add_row("category", metadata.get("category", "—"))

    tabs = metadata.get("tab_structure", [])
    table.add_row("tab_structure", ", ".join(tabs) if tabs else "—")

    for pt, count in sorted(type_counts.items()):
        table.add_row(f"pages ({pt})", str(count))
    table.add_row("总页面数", str(len(pages)))

    rels = metadata.get("page_relationships", [])
    table.add_row("page_relationships", str(len(rels)))
    table.add_row("输出", str(out_path))

    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="annotator",
        description="VLM-based dataset annotation for app screenshots",
    )
    parser.add_argument(
        "--input", "-i",
        default=str(INPUT_DIR),
        help=f"输入目录（单个 app 目录，或 --batch 时的父目录；默认: {INPUT_DIR}，来自 experiment.yaml）",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="批量模式：处理输入目录下所有子目录",
    )
    parser.add_argument(
        "--model", "-m",
        default=ANNOTATOR_MODEL,
        help=f"VLM 模型名称 (默认: {ANNOTATOR_MODEL}，来自 experiment.yaml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 metadata.json",
    )
    parser.add_argument(
        "--describe-only",
        action="store_true",
        help="仅运行单图描述（Phase 1），跳过全局分析",
    )
    parser.add_argument(
        "--no-testgen",
        action="store_true",
        help="标注完成后不自动生成 test_cases.json",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细日志",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    input_path = Path(args.input).resolve()
    if not input_path.is_dir():
        console.print(f"[red]错误[/red]: 目录不存在: {input_path}")
        sys.exit(1)

    console.print(f"[bold]Annotator[/bold] — VLM 数据集标注器")
    console.print(f"模型: {args.model} | 模式: {'批量' if args.batch else '单 app'}")

    if args.batch:
        app_dirs = sorted([
            d for d in input_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])
        if not app_dirs:
            console.print(f"[red]错误[/red]: {input_path} 下无子目录")
            sys.exit(1)

        console.print(f"发现 {len(app_dirs)} 个 app 目录")
        success, total = 0, len(app_dirs)
        for app_dir in app_dirs:
            if _annotate_app(app_dir, args.model, describe_only=args.describe_only, force=args.force, no_testgen=args.no_testgen):
                success += 1

        console.print(f"\n[bold]完成[/bold]: {success}/{total} 个 app 标注成功")
    else:
        if not _annotate_app(input_path, args.model, describe_only=args.describe_only, force=args.force, no_testgen=args.no_testgen):
            sys.exit(1)
        console.print("\n[bold green]标注完成[/bold green]")


if __name__ == "__main__":
    main()
