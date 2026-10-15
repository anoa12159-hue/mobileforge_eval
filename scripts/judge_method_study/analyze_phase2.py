"""Compare human rankings to list-wise rankings and write a report.

Usage::

    python -m scripts.judge_method_study.analyze_phase2 \\
        --tasks experiments/judge_method_study/phase2/tasks.json \\
        --labels experiments/judge_method_study/phase2/human_labels.json \\
        --out experiments/judge_method_study/phase2/REPORT.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testgen.visual_judge_listwise import _kendall_tau  # noqa: E402


def top_k_overlap(a: list[str], b: list[str], k: int) -> float:
    """Fraction of labels that appear in BOTH top-k of rankings a and b."""
    top_a = set(a[:k])
    top_b = set(b[:k])
    if not top_a:
        return 0.0
    return len(top_a & top_b) / len(top_a)


def bottom_k_overlap(a: list[str], b: list[str], k: int) -> float:
    """Fraction of labels that appear in BOTH bottom-k of rankings a and b."""
    if k <= 0 or not a or not b:
        return 0.0
    bot_a = set(a[-k:])
    bot_b = set(b[-k:])
    if not bot_a:
        return 0.0
    return len(bot_a & bot_b) / len(bot_a)


def set_overlap(a: list[str], b: list[str]) -> float:
    """Intersection-over-union of two label sets (for unrankable comparison)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def fmt_num(v: float | None, digits: int = 3) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v*100:.{digits}f}%"


def analyze(tasks_path: Path, labels_path: Path) -> dict[str, Any]:
    tasks_data = json.loads(tasks_path.read_text())
    tasks_by_id = {t["task_id"]: t for t in tasks_data["tasks"]}

    labels_data = json.loads(labels_path.read_text())
    labels = labels_data.get("labels", {})

    rows: list[dict[str, Any]] = []
    for tid, lbl in labels.items():
        task = tasks_by_id.get(tid)
        if task is None:
            continue
        human_rank = lbl.get("ranking")
        if human_rank is None:
            # skipped by user
            continue
        human_unrankable = lbl.get("unrankable") or []

        listwise_full = task["listwise_final_ranking_present"]
        # Compose "human_full" = ranked order ++ unrankable (treated as tied last)
        human_full = list(human_rank) + list(human_unrankable)

        # Kendall τ strictly on the ranked portion (ignore unrankable in both
        # rankings because they carry no order signal).
        listwise_ranked_only = [m for m in listwise_full if m not in set(human_unrankable)]
        tau_strict = _kendall_tau(human_rank, listwise_ranked_only)

        # Kendall τ on full orderings (unrankable concatenated at tail).
        tau_full = _kendall_tau(human_full, listwise_full)

        top1 = 1.0 if (human_rank and listwise_full and human_rank[0] == listwise_full[0]) else 0.0
        top2 = top_k_overlap(human_rank, listwise_full, 2) if len(human_rank) >= 2 else None
        top3 = top_k_overlap(human_rank, listwise_full, 3) if len(human_rank) >= 3 else None

        # Unrankable agreement: list-wise bottom-K models vs human's unrankable set
        k_na = len(human_unrankable)
        unrankable_iou = (
            set_overlap(human_unrankable, listwise_full[-k_na:])
            if k_na > 0 else None
        )

        rows.append({
            "task_id": tid,
            "app": task["app"],
            "test_id": task["test_id"],
            "selected_by": task.get("selected_by"),
            "shuffle_stability": task.get("shuffle_stability"),
            "anchor_pass_rate": task.get("anchor_pass_rate"),
            "human_ranking": human_rank,
            "human_unrankable": human_unrankable,
            "listwise_ranking": listwise_full,
            "kendall_tau_strict": tau_strict,   # on ranked-only
            "kendall_tau_full": tau_full,       # unrankable tied at tail
            "top1_match": top1,
            "top2_overlap": top2,
            "top3_overlap": top3,
            "unrankable_iou": unrankable_iou,
            "note": lbl.get("note"),
        })

    def _avg(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    summary = {
        "n_labeled": len(rows),
        "n_skipped": sum(1 for lbl in labels.values() if lbl.get("ranking") is None),
        "n_total_tasks": len(tasks_data["tasks"]),
        "avg_kendall_tau_strict": _avg([r["kendall_tau_strict"] for r in rows]),
        "avg_kendall_tau_full": _avg([r["kendall_tau_full"] for r in rows]),
        "top1_accuracy": _avg([r["top1_match"] for r in rows]),
        "avg_top2_overlap": _avg([r["top2_overlap"] for r in rows]),
        "avg_top3_overlap": _avg([r["top3_overlap"] for r in rows]),
        "avg_unrankable_iou": _avg([r["unrankable_iou"] for r in rows]),
        "n_tasks_with_unrankable": sum(1 for r in rows if r["human_unrankable"]),
        "n_unrankable_total": sum(len(r["human_unrankable"]) for r in rows),
    }

    # Break down by selection strategy
    by_strategy: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = r.get("selected_by") or "unknown"
        bucket = by_strategy.setdefault(
            s, {"n": 0, "tau_strict": [], "tau_full": [], "top1": []}
        )
        bucket["n"] += 1
        if r["kendall_tau_strict"] is not None:
            bucket["tau_strict"].append(r["kendall_tau_strict"])
        if r["kendall_tau_full"] is not None:
            bucket["tau_full"].append(r["kendall_tau_full"])
        bucket["top1"].append(r["top1_match"])
    for s, b in by_strategy.items():
        b["avg_tau_strict"] = round(sum(b["tau_strict"]) / len(b["tau_strict"]), 4) if b["tau_strict"] else None
        b["avg_tau_full"] = round(sum(b["tau_full"]) / len(b["tau_full"]), 4) if b["tau_full"] else None
        b["top1_accuracy"] = round(sum(b["top1"]) / len(b["top1"]), 4) if b["top1"] else None
        # Drop the intermediate lists for cleaner JSON
        del b["tau_strict"], b["tau_full"], b["top1"]

    return {
        "summary": summary,
        "by_strategy": by_strategy,
        "rows": rows,
    }


def write_report(out_path: Path, result: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Phase 2 · Human vs List-wise 对齐报告\n")

    s = result["summary"]
    lines.append("## 核心指标\n")
    lines.append("| 指标 | 值 | 合格阈值 |")
    lines.append("|---|---|---|")
    lines.append(f"| 已标注任务数 | {s['n_labeled']} / {s['n_total_tasks']} | — |")
    lines.append(f"| Skipped 任务数 | {s['n_skipped']} | — |")
    lines.append(f"| 含 unrankable 的任务数 | {s['n_tasks_with_unrankable']} | — |")
    lines.append(f"| 总 unrankable 候选数 | {s['n_unrankable_total']} | — |")
    lines.append(f"| **Kendall τ (strict, ranked 部分)** | {fmt_num(s['avg_kendall_tau_strict'])} | ≥ 0.7 |")
    lines.append(f"| Kendall τ (full, unrankable tied tail) | {fmt_num(s['avg_kendall_tau_full'])} | 参考 |")
    lines.append(f"| **Top-1 命中率** | {fmt_pct(s['top1_accuracy'])} | ≥ 70% |")
    lines.append(f"| Top-2 重合率 | {fmt_pct(s['avg_top2_overlap'])} | 参考 |")
    lines.append(f"| Top-3 重合率 | {fmt_pct(s['avg_top3_overlap'])} | 参考 |")
    lines.append(f"| Unrankable IoU (list-wise 末 K vs human 的 unrankable) | {fmt_num(s['avg_unrankable_iou'])} | 参考 |")
    lines.append("")
    lines.append('> **strict τ**：只在人工「可排序」部分 vs list-wise 对应模型子序列上算，最严格的一致性。')
    lines.append('> **full τ**：把 human 标为 unrankable 的模型当作「并列最末」拼到排名尾部，与 list-wise 全序比较。')
    lines.append("")

    lines.append("## 按挑选策略分解\n")
    lines.append("| Strategy | N | τ-strict | τ-full | top-1 |")
    lines.append("|---|---|---|---|---|")
    for strat, b in result["by_strategy"].items():
        lines.append(
            f"| {strat} | {b['n']} | {fmt_num(b['avg_tau_strict'])} | "
            f"{fmt_num(b['avg_tau_full'])} | {fmt_pct(b['top1_accuracy'])} |"
        )
    lines.append("")

    lines.append("## 逐任务明细\n")
    lines.append("| task | app / test_id | 策略 | τ-strict | top-1 | #NA | human (order, NA tail) | list-wise |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in result["rows"]:
        human_order = list(r["human_ranking"])
        na = r["human_unrankable"]
        if na:
            human_order = human_order + [f"[NA]{m}" for m in na]
        human_str = " > ".join(human_order[:5]) + ("..." if len(human_order) > 5 else "")
        lw_str = " > ".join(r["listwise_ranking"][:5]) + ("..." if len(r["listwise_ranking"]) > 5 else "")
        lines.append(
            f"| {r['task_id']} | `{r['app']}` / `{r['test_id']}` | {r['selected_by']} | "
            f"{fmt_num(r['kendall_tau_strict'])} | {'✓' if r['top1_match'] else '✗'} | "
            f"{len(na)} | {human_str} | {lw_str} |"
        )
    lines.append("")

    lines.append("## 结论参考\n")
    tau = s.get("avg_kendall_tau_strict")
    top1 = s.get("top1_accuracy")
    if tau is not None and top1 is not None:
        if tau >= 0.7 and top1 >= 0.7:
            lines.append(
                "✅ **List-wise 与人工排名高度一致**（τ ≥ 0.7, top-1 ≥ 70%），"
                "可作为 paper 主方法上全量。"
            )
        elif tau >= 0.5:
            lines.append(
                "⚠️ **List-wise 与人工中等一致**（0.5 ≤ τ < 0.7）。建议："
                "1) 增加人工样本量；2) 检查不一致 case 的原因（prompt / judge 模型）；"
                "3) 考虑多 judge ensemble。"
            )
        else:
            lines.append(
                "❌ **List-wise 与人工排名显著不一致**（τ < 0.5）。需要重新检视方法："
                "是否 prompt 有误、judge 模型能力不足、或候选差异极小需要更细粒度方法。"
            )
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--json-out", default=None, type=Path,
                    help="Also write the full JSON result here")
    args = ap.parse_args()

    result = analyze(args.tasks, args.labels)
    write_report(args.out, result)
    if args.json_out:
        args.json_out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Report → {args.out}")
    if args.json_out:
        print(f"JSON → {args.json_out}")


if __name__ == "__main__":
    main()
