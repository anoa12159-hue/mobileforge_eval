"""Phase 3 aggregation: Borda-based model rankings across 29 apps × 6 models.

Consumes the list-wise cache (one entry per scenario × judge) and produces:

1. Global ranking (averaged across all scenarios, normalized)
2. Per-app ranking (29 tables)
3. Per-scenario-type ranking (3 tables: page_exists / parent_child_navigation / tab_navigation)
4. Cross-app stability: how often each model appears in top-1 / bottom-1
5. App-level outliers: apps where the ranking deviates from global

Usage:
    python -m scripts.judge_method_study.aggregate_phase3 \
        --cache-dir experiments/judge_method_study/listwise_cache/<experiment> \
        --judge-model gemini-2.5-pro \
        --out experiments/judge_method_study/phase3/AGGREGATE.md \
        --json-out experiments/judge_method_study/phase3/borda_summary.json
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

logger = logging.getLogger("aggregate_phase3")


def load_entries(cache_dir: Path, judge_tag: str) -> list[dict]:
    entries = []
    for app_dir in sorted(cache_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        for f in sorted(app_dir.glob(f"*__{judge_tag}.json")):
            if f.name.startswith("_"):
                continue
            entries.append(json.loads(f.read_text()))
    return entries


def normalize_borda_per_scenario(entries: list[dict]) -> list[dict]:
    """Each scenario's borda scores are normalized to [0, 1] within that scenario.

    Missing models (no screenshot) are appended at 0.
    """
    out = []
    for e in entries:
        scores = dict(e.get("borda_scores") or {})
        missing = e.get("missing_models", [])
        if not scores:
            continue
        lo = min(scores.values())
        hi = max(scores.values())
        rng = (hi - lo) if hi > lo else 1.0
        normed = {m: (s - lo) / rng for m, s in scores.items()}
        for m in missing:
            normed[m] = 0.0
        out.append({
            "app": e["app"],
            "test_id": e["test_id"],
            "type": e.get("type"),
            "scores": normed,
        })
    return out


def per_group_average(entries: list[dict], group_fn) -> dict[str, dict[str, float]]:
    """Return {group_key: {model: avg_normalized_borda}}."""
    bucket: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        g = group_fn(e)
        for m, s in e["scores"].items():
            bucket[g][m].append(s)
    return {
        g: {m: mean(vs) for m, vs in ms.items()}
        for g, ms in bucket.items()
    }


def rank_of(scores: dict[str, float]) -> list[str]:
    """High → low."""
    return [m for m, _ in sorted(scores.items(), key=lambda x: -x[1])]


def kendall_tau(a: list[str], b: list[str]) -> float:
    common = [m for m in a if m in b]
    n = len(common)
    if n < 2:
        return float("nan")
    ra = {m: -a.index(m) for m in common}
    rb = {m: -b.index(m) for m in common}
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            d1 = ra[common[i]] - ra[common[j]]
            d2 = rb[common[i]] - rb[common[j]]
            if d1 * d2 > 0:
                conc += 1
            elif d1 * d2 < 0:
                disc += 1
    total = n * (n - 1) // 2
    return (conc - disc) / total if total else float("nan")


def fmt_num(x: float, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    return f"{x:.{digits}f}"


def build_report(
    normed: list[dict],
    judge_tag: str,
) -> tuple[str, dict]:
    """Return (markdown_text, json_summary)."""
    all_models = sorted({m for e in normed for m in e["scores"]})
    apps = sorted({e["app"] for e in normed})
    types = sorted({e["type"] for e in normed})

    # Global ranking
    global_scores: dict[str, list[float]] = defaultdict(list)
    for e in normed:
        for m, s in e["scores"].items():
            global_scores[m].append(s)
    global_avg = {m: mean(vs) for m, vs in global_scores.items()}
    global_rank = rank_of(global_avg)

    # Per-app
    per_app = per_group_average(normed, lambda e: e["app"])
    per_app_rank = {a: rank_of(s) for a, s in per_app.items()}

    # Per-type
    per_type = per_group_average(normed, lambda e: e["type"])
    per_type_rank = {t: rank_of(s) for t, s in per_type.items()}

    # Per-(app, type)
    per_app_type = per_group_average(
        normed, lambda e: (e["app"], e["type"])
    )

    # Stability: top-1 and bottom-1 frequency across apps
    top1_count = defaultdict(int)
    bot1_count = defaultdict(int)
    for a in apps:
        r = per_app_rank[a]
        if r:
            top1_count[r[0]] += 1
            bot1_count[r[-1]] += 1

    # Per-app Kendall τ vs global
    per_app_tau = {a: kendall_tau(per_app_rank[a], global_rank) for a in apps}

    # Per-type τ vs global
    per_type_tau = {t: kendall_tau(per_type_rank[t], global_rank) for t in types}

    # ===== Markdown =====
    lines: list[str] = []
    lines.append(f"# Phase 3 · 全量 Borda 聚合报告")
    lines.append("")
    lines.append(f"> **Judge model**: `{judge_tag}`")
    lines.append(f"> **Scope**: {len(apps)} apps × {len(all_models)} models × {len(normed)} scenarios")
    lines.append(f"> **Scenario types**: " + ", ".join(f"`{t}`" for t in types))
    lines.append("")

    # ---- Global ranking ----
    lines.append("## 1. 全局模型排名（29 apps, 701 scenarios, 归一化 Borda）")
    lines.append("")
    lines.append("| Rank | Model | Avg normalized Borda |")
    lines.append("|---|---|---|")
    for i, m in enumerate(global_rank, 1):
        lines.append(f"| {i} | `{m}` | {fmt_num(global_avg[m], 4)} |")
    lines.append("")

    # ---- Stability ----
    lines.append("## 2. 跨 App 稳定性")
    lines.append("")
    lines.append(f"### 2.1 Top-1 / Bottom-1 频率（跨 {len(apps)} 个 app）")
    lines.append("")
    lines.append("| Model | Top-1 次数 | Bottom-1 次数 |")
    lines.append("|---|---|---|")
    for m in global_rank:
        lines.append(f"| `{m}` | {top1_count.get(m, 0)} | {bot1_count.get(m, 0)} |")
    lines.append("")

    # ---- Per-app vs global τ ----
    lines.append(f"### 2.2 每 App 的 Model ranking vs 全局 ranking 的 Kendall τ")
    lines.append("")
    lines.append("| τ 区间 | App 数 | Apps |")
    lines.append("|---|---|---|")
    tau_buckets = defaultdict(list)
    for a, t in per_app_tau.items():
        if t != t:  # NaN
            bucket = "n/a"
        elif t >= 0.8:
            bucket = "[0.8, 1.0]"
        elif t >= 0.6:
            bucket = "[0.6, 0.8)"
        elif t >= 0.4:
            bucket = "[0.4, 0.6)"
        elif t >= 0.0:
            bucket = "[0.0, 0.4)"
        else:
            bucket = "< 0"
        tau_buckets[bucket].append(a)
    for bucket in ["[0.8, 1.0]", "[0.6, 0.8)", "[0.4, 0.6)", "[0.0, 0.4)", "< 0", "n/a"]:
        apps_in = tau_buckets.get(bucket, [])
        if apps_in:
            lines.append(f"| {bucket} | {len(apps_in)} | {', '.join(f'`{a}`' for a in sorted(apps_in))} |")
    lines.append("")
    taus = [t for t in per_app_tau.values() if t == t]
    if taus:
        lines.append(f"**Per-app τ 均值 = {fmt_num(mean(taus))}**，标准差 = {fmt_num(stdev(taus)) if len(taus) > 1 else 'n/a'}")
        lines.append("")

    # ---- Per-type ranking ----
    lines.append("## 3. 按 Scenario 类型分解")
    lines.append("")
    lines.append("### 3.1 各类型独立排名")
    lines.append("")
    header = "| Rank | " + " | ".join(f"`{t}`" for t in types) + " |"
    sep = "|---" * (len(types) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for i in range(len(all_models)):
        row = f"| {i + 1} |"
        for t in types:
            r = per_type_rank.get(t, [])
            if i < len(r):
                m = r[i]
                score = per_type[t].get(m, 0.0)
                row += f" `{m}` ({fmt_num(score, 3)}) |"
            else:
                row += " — |"
        lines.append(row)
    lines.append("")
    lines.append("### 3.2 每类型 vs 全局 τ")
    lines.append("")
    lines.append("| Type | N scenarios | τ vs global |")
    lines.append("|---|---|---|")
    type_counts = defaultdict(int)
    for e in normed:
        type_counts[e["type"]] += 1
    for t in types:
        lines.append(f"| `{t}` | {type_counts[t]} | {fmt_num(per_type_tau[t])} |")
    lines.append("")

    # ---- Heatmap-style big table: app × model ----
    lines.append("## 4. App × Model Borda 分数矩阵（主表）")
    lines.append("")
    header = "| App |" + "|".join(f" `{m}` " for m in global_rank) + "| τ vs global |"
    sep = "|---" * (len(global_rank) + 2) + "|"
    lines.append(header)
    lines.append(sep)
    for a in apps:
        row = f"| `{a}` |"
        for m in global_rank:
            s = per_app[a].get(m)
            row += f" {fmt_num(s, 3) if s is not None else '—'} |"
        tau = per_app_tau[a]
        row += f" {fmt_num(tau)} |"
        lines.append(row)
    # global row
    row = "| **global** |"
    for m in global_rank:
        row += f" **{fmt_num(global_avg[m], 3)}** |"
    row += " 1.000 |"
    lines.append(row)
    lines.append("")

    # ---- Outliers ----
    lines.append("## 5. Outlier Apps（τ < 0.6）")
    lines.append("")
    outliers = [(a, per_app_tau[a]) for a in apps if per_app_tau[a] == per_app_tau[a] and per_app_tau[a] < 0.6]
    outliers.sort(key=lambda x: x[1])
    if outliers:
        lines.append("| App | τ | 本 app 排名 | 全局排名 |")
        lines.append("|---|---|---|---|")
        for a, tau in outliers:
            app_r = " > ".join(f"`{m}`" for m in per_app_rank[a])
            lines.append(f"| `{a}` | {fmt_num(tau)} | {app_r} | {' > '.join(f'`{m}`' for m in global_rank)} |")
        lines.append("")
        lines.append('**解读**：这些 app 的模型排名显著偏离全局，可能是：')
        lines.append('- app 的 UI 模式对某些模型特别「友好」或「敌对」')
        lines.append('- 该 app 的 scenario 类型分布异常（例如全是 tab_navigation）')
        lines.append('- 值得在 paper 里单独讨论')
    else:
        lines.append("（无 τ < 0.6 的 outlier，所有 app 的 ranking 都和全局高度一致）")
    lines.append("")

    # ---- JSON summary ----
    summary = {
        "judge_model": judge_tag,
        "n_apps": len(apps),
        "n_models": len(all_models),
        "n_scenarios": len(normed),
        "global_ranking": global_rank,
        "global_borda": {m: round(v, 4) for m, v in global_avg.items()},
        "top1_count_per_model": dict(top1_count),
        "bottom1_count_per_model": dict(bot1_count),
        "per_app_borda": {a: {m: round(v, 4) for m, v in s.items()} for a, s in per_app.items()},
        "per_app_ranking": per_app_rank,
        "per_app_tau_vs_global": {a: (round(t, 4) if t == t else None) for a, t in per_app_tau.items()},
        "per_type_borda": {t: {m: round(v, 4) for m, v in s.items()} for t, s in per_type.items()},
        "per_type_ranking": per_type_rank,
        "per_type_tau_vs_global": {t: round(v, 4) for t, v in per_type_tau.items()},
    }
    return "\n".join(lines), summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True, type=Path)
    ap.add_argument("--judge-model", required=True, type=str)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--json-out", default=None, type=Path)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    from evaluator.utils import sanitize_model_tag
    judge_tag = sanitize_model_tag(args.judge_model)

    entries = load_entries(args.cache_dir, judge_tag)
    logger.info("Loaded %d entries", len(entries))
    if not entries:
        logger.error("No cache found for judge=%s", judge_tag)
        return

    normed = normalize_borda_per_scenario(entries)
    md, summary = build_report(normed, judge_tag)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)
    logger.info("Report → %s", args.out)

    if args.json_out:
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        logger.info("JSON → %s", args.json_out)


if __name__ == "__main__":
    main()
