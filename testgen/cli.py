"""命令行入口：generate / run / eval 三个子命令"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="testgen",
        description="基于 metadata.json 的自动化测试用例生成与执行",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="输出详细日志",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- generate ---
    gen_parser = subparsers.add_parser(
        "generate", aliases=["gen"],
        help="从 metadata.json 生成测试用例",
    )
    gen_parser.add_argument("metadata", type=Path, help="metadata.json 文件路径")
    gen_parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="输出 test_cases.json 的路径（默认输出到 stdout）",
    )

    # --- run ---
    run_parser = subparsers.add_parser(
        "run",
        help="Phase 1: 导航测试（Playwright 自动化）",
    )
    run_parser.add_argument("--cases", type=Path, required=True, help="test_cases.json 路径")
    run_parser.add_argument("--run-dir", type=Path, required=True, help="生成项目的目录")
    run_parser.add_argument("--input-dir", type=Path, required=True, help="输入目录（含设计图）")
    run_parser.add_argument("--report", type=Path, default=None, help="报告输出路径")
    run_parser.add_argument(
        "--vlm-model", type=str, default=None,
        help="覆盖 VLM 兜底模型（默认读取 experiment.yaml testgen.fallback_model）",
    )
    run_parser.add_argument(
        "--no-vlm-fallback", action="store_true",
        help="禁用 VLM 点击兜底，仅使用 DOM/文本定位",
    )

    # --- eval ---
    eval_parser = subparsers.add_parser(
        "eval",
        help="Phase 2: 视觉评估（基于已有的 test_report.json 截图）",
    )
    eval_parser.add_argument(
        "--nav-report", "--report", dest="nav_report", type=Path, required=True,
        help="run 阶段生成的 test_report.json（只读）",
    )
    eval_parser.add_argument(
        "--visual-report", type=Path, default=None,
        help="视觉评分写入路径；默认与 nav-report 同目录下的 visual_report.json",
    )
    eval_parser.add_argument("--cases", type=Path, required=True, help="test_cases.json 路径")
    eval_parser.add_argument("--input-dir", type=Path, required=True, help="输入目录（含设计图）")
    eval_parser.add_argument(
        "--judge-tag", type=str, default=None,
        help="写入 visual_scores_by_judge[<tag>] 的 key；默认取 vlm-model sanitized 后的名字",
    )
    eval_parser.add_argument(
        "--resume", action="store_true",
        help="跳过已有评分的 (test_id, field, judge) 三元组",
    )
    eval_parser.add_argument(
        "--vlm-model", type=str, default=None,
        help="覆盖 VLM 评分模型（默认读取 experiment.yaml testgen.eval_model）",
    )
    eval_parser.add_argument(
        "--workers", type=int, default=None,
        help="VLM 评分并发数（默认读取 testgen.eval_workers，兜底 8）",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command in ("generate", "gen"):
        _cmd_generate(args)
    elif args.command == "run":
        _cmd_run(args)
    elif args.command == "eval":
        _cmd_eval(args)
    else:
        parser.print_help()
        sys.exit(1)


# -----------------------------------------------------------------------

def _get_testgen_config() -> dict:
    """Load testgen section from experiment.yaml."""
    try:
        from designforge.config import _cfg
        return _cfg.get("testgen", {}) or {}
    except Exception:
        return {}


def _resolve_fallback_model(args: argparse.Namespace) -> str | None:
    """Determine the VLM model for click fallback.

    Priority: --no-vlm-fallback > --vlm-model > testgen.fallback_model
    """
    if getattr(args, "no_vlm_fallback", False):
        return None
    if args.vlm_model:
        return args.vlm_model
    model = _get_testgen_config().get("fallback_model")
    if model:
        logger.info("VLM 兜底模型 (experiment.yaml): %s", model)
        return model
    return None


def _resolve_eval_model(args: argparse.Namespace) -> str:
    """Determine the VLM model for visual evaluation.

    Priority: --vlm-model > testgen.eval_model
    """
    if args.vlm_model:
        return args.vlm_model
    model = _get_testgen_config().get("eval_model")
    if model:
        logger.info("VLM 评估模型 (experiment.yaml): %s", model)
        return model
    print("错误: 未指定 VLM 评分模型，请传 --vlm-model 或在 experiment.yaml 中设置 testgen.eval_model",
          file=sys.stderr)
    sys.exit(1)


def _resolve_metadata_path(raw: Path) -> Path:
    """Prefer metadata.reviewed.json when the user points to a directory or metadata.json."""
    if raw.is_dir():
        reviewed = raw / "metadata.reviewed.json"
        if reviewed.is_file():
            return reviewed
        return raw / "metadata.json"
    if raw.name == "metadata.json":
        reviewed = raw.with_name("metadata.reviewed.json")
        if reviewed.is_file():
            return reviewed
    return raw


def _cmd_generate(args: argparse.Namespace) -> None:
    from .generator import generate_from_file, export_cases

    meta_path = _resolve_metadata_path(args.metadata)
    if not meta_path.exists():
        print(f"错误: 文件不存在 {meta_path}", file=sys.stderr)
        sys.exit(1)
    if meta_path != args.metadata:
        logger.info("使用审核版: %s", meta_path)

    cases = generate_from_file(meta_path)

    if args.output:
        export_cases(cases, args.output)
        print(f"已生成 {len(cases)} 个测试用例 → {args.output}")
    else:
        data = {
            "total_cases": len(cases),
            "cases": [c.to_dict() for c in cases],
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _cmd_run(args: argparse.Namespace) -> None:
    from .runner import run_tests

    for path, label in [(args.cases, "文件"), (args.run_dir, "目录"), (args.input_dir, "目录")]:
        if not path.exists():
            print(f"错误: {label}不存在 {path}", file=sys.stderr)
            sys.exit(1)

    vlm_model = _resolve_fallback_model(args)

    report = run_tests(
        cases_path=args.cases,
        run_dir=args.run_dir,
        input_dir=args.input_dir,
        report_path=args.report,
        vlm_model=vlm_model,
    )

    _print_nav_summary(report)


def _cmd_eval(args: argparse.Namespace) -> None:
    from .runner import run_visual_evaluation

    for path, label in [(args.nav_report, "报告"), (args.cases, "用例"), (args.input_dir, "目录")]:
        if not path.exists():
            print(f"错误: {label}不存在 {path}", file=sys.stderr)
            sys.exit(1)

    vlm_model = _resolve_eval_model(args)

    visual_report_path = args.visual_report
    if visual_report_path is None:
        visual_report_path = args.nav_report.parent / "visual_report.json"

    report = run_visual_evaluation(
        nav_report_path=args.nav_report,
        visual_report_path=visual_report_path,
        cases_path=args.cases,
        input_dir=args.input_dir,
        vlm_model=vlm_model,
        judge_tag=args.judge_tag,
        resume=args.resume,
        max_workers=args.workers,
    )

    _print_visual_summary(report)


# -----------------------------------------------------------------------
# Output formatting
# -----------------------------------------------------------------------

def _print_nav_summary(report: dict) -> None:
    total = report.get("total_cases", 0)
    passed = report.get("passed", 0)
    rate = report.get("pass_rate", 0.0)
    print(f"\n{'='*50}")
    print(f"导航测试完成: {passed}/{total} 通过 (通过率 {rate:.1%})")
    print(f"{'='*50}")

    for t, stats in report.get("by_type", {}).items():
        print(f"  {t:30s}  {stats['passed']}/{stats['total']}  ({stats['rate']:.1%})")

    failed = [d for d in report.get("details", []) if not d.get("passed")]
    if failed:
        print(f"\n失败用例 ({len(failed)}):")
        for d in failed:
            print(f"  ✗ {d['test_id']}: {d.get('error', 'navigation failed')}")


def _print_visual_summary(report: dict) -> None:
    scores_by_judge = report.get("visual_scores_by_judge") or {}
    if not scores_by_judge:
        print("无视觉评分数据")
        return

    for judge_tag, judge_entry in scores_by_judge.items():
        print(f"\n{'='*50}")
        print(f"视觉评估完成 (judge={judge_tag}, model={judge_entry.get('model')})")
        print(f"{'='*50}")
        if judge_entry.get("avg_before") is not None:
            print(
                f"  跳转前页面平均: {judge_entry['avg_before']:.2f}/5.00  "
                f"({judge_entry.get('scored_pairs_before', 0)} pairs)"
            )
        if judge_entry.get("avg_after") is not None:
            print(
                f"  跳转后页面平均: {judge_entry['avg_after']:.2f}/5.00  "
                f"({judge_entry.get('scored_pairs_after', 0)} pairs)"
            )

        details = judge_entry.get("details") or {}
        scored = [
            (tid, d) for tid, d in details.items()
            if d.get("visual_similarity_after") is not None
        ]
        if scored:
            print(f"\n逐用例视觉分:")
            for tid, d in scored:
                before = d.get("visual_similarity_before")
                after = d.get("visual_similarity_after")
                b_str = f"{before:.2f}" if before is not None else " n/a"
                a_str = f"{after:.2f}" if after is not None else " n/a"
                print(f"  {tid:55s}  before={b_str}  after={a_str}")


if __name__ == "__main__":
    main()
