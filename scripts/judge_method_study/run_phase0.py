"""Phase-0 smoke test for the judge-method study.

Compares three judge methods (point-wise, pair-wise, list-wise-with-anchor)
on a small slice of an existing experiment — no human labels required. Only
self-consistency metrics are computed.

Usage:
    python -m scripts.judge_method_study.run_phase0 \
        --experiment experiments/20260428_181902_mobileforge-compare \
        --app airbnb \
        --test-ids page_exists__explore tab_nav__explore__to__trips parent_child__explore__to__listing_details \
        --judge-model gemini-2.5-pro \
        --vlm-click-tag gemini-2.5-flash \
        --output experiments/judge_method_study/phase0

Layout of outputs::
    <output>/
      config.json          # what was run
      pairwise_raw.jsonl   # one line per pair (with swap)
      listwise_raw.jsonl   # one line per (test_id, shuffle)
      metrics.json         # aggregate self-consistency metrics
      REPORT.md            # human-readable summary
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure repo root on sys.path when run as script (not -m)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testgen.visual_judge import judge_pair as judge_pair_pointwise  # noqa: E402
from testgen.visual_judge_pairwise import judge_pair_with_swap  # noqa: E402
from testgen.visual_judge_listwise import rank_candidates_k_shuffle, _kendall_tau  # noqa: E402

logger = logging.getLogger("phase0")


# ------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------


def discover_runs(experiment_dir: Path, app: str) -> dict[str, Path]:
    """Return {model_tag: run_dir} for the given app.

    A "model tag" is the middle component of the run folder name, e.g.
    ``airbnb__claude-opus-4-6__agentic`` → ``claude-opus-4-6``.
    """
    runs_dir = experiment_dir / "runs"
    found: dict[str, Path] = {}
    for sub in sorted(runs_dir.iterdir()):
        if not sub.is_dir():
            continue
        parts = sub.name.split("__")
        if len(parts) < 3 or parts[0] != app:
            continue
        model_tag = parts[1]
        found[model_tag] = sub
    return found


def resolve_impl_path(run_dir: Path, vlm_click_tag: str, test_id: str) -> Path | None:
    """Return the 'after' screenshot (falls back to 'before') for a test_id.

    For page_exists cases, only 'before' exists. For navigation cases we want
    the 'after' shot because that's where visual quality is really judged.
    """
    shots_dir = run_dir / "eval" / "test_screenshots" / vlm_click_tag
    after = shots_dir / f"{test_id}__after.png"
    before = shots_dir / f"{test_id}__before.png"
    if after.is_file():
        return after
    if before.is_file():
        return before
    return None


def resolve_reference_path(app_input_dir: Path, case: dict[str, Any]) -> Path | None:
    """Resolve the reference screenshot for a test case.

    Prefer ``to_page.reference_screenshot``; fall back to ``from_page.``.
    """
    for side in ("to_page", "from_page"):
        fname = (case.get(side) or {}).get("reference_screenshot")
        if fname:
            p = app_input_dir / fname
            if p.is_file():
                return p
    return None


def load_cases(app_input_dir: Path) -> dict[str, dict[str, Any]]:
    """Load test_cases.reviewed.json (fallback to test_cases.json) keyed by test_id."""
    for name in ("test_cases.reviewed.json", "test_cases.json"):
        p = app_input_dir / name
        if p.is_file():
            data = json.loads(p.read_text())
            return {c["test_id"]: c for c in data.get("cases", [])}
    raise FileNotFoundError(f"No test_cases*.json in {app_input_dir}")


# ------------------------------------------------------------------
# Scenario execution
# ------------------------------------------------------------------


def build_scenarios(
    experiment_dir: Path,
    app: str,
    test_ids: list[str],
    vlm_click_tag: str,
    input_root: Path,
) -> list[dict[str, Any]]:
    """For each test_id, assemble reference + candidates={model_tag: impl_path}."""
    app_input = input_root / app
    cases = load_cases(app_input)
    runs = discover_runs(experiment_dir, app)
    if not runs:
        raise RuntimeError(f"No runs found for app={app} under {experiment_dir}")

    scenarios: list[dict[str, Any]] = []
    for tid in test_ids:
        case = cases.get(tid)
        if not case:
            logger.warning("Skipping %s: test_id not found in test_cases.json", tid)
            continue
        ref = resolve_reference_path(app_input, case)
        if ref is None:
            logger.warning("Skipping %s: reference screenshot missing", tid)
            continue

        candidates: dict[str, str] = {}
        for model_tag, run_dir in runs.items():
            impl = resolve_impl_path(run_dir, vlm_click_tag, tid)
            if impl is None:
                logger.warning("Missing impl for %s/%s — skipping this candidate", model_tag, tid)
                continue
            candidates[model_tag] = str(impl)

        if len(candidates) < 2:
            logger.warning("Skipping %s: fewer than 2 candidates available", tid)
            continue

        scenarios.append(
            {
                "app": app,
                "test_id": tid,
                "type": case.get("type"),
                "reference_path": str(ref),
                "candidates": candidates,
            }
        )
    return scenarios


def run_pointwise(
    scenarios: list[dict[str, Any]], model: str, max_workers: int = 6
) -> dict[str, dict[str, dict[str, Any]]]:
    """{test_id: {model_tag: pointwise_scores}}"""
    out: dict[str, dict[str, dict[str, Any]]] = {s["test_id"]: {} for s in scenarios}

    tasks: list[tuple[str, str, str, str]] = []
    for s in scenarios:
        for model_tag, impl in s["candidates"].items():
            tasks.append((s["test_id"], model_tag, s["reference_path"], impl))

    def _one(task):
        tid, mt, ref, impl = task
        scores = judge_pair_pointwise(design_path=ref, impl_path=impl, model=model)
        return tid, mt, scores

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_one, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            tid, mt, scores = f.result()
            out[tid][mt] = scores or {}
            logger.info("[pointwise %d/%d] %s · %s done", i, len(tasks), tid, mt)
    logger.info("Pointwise total: %d calls in %.1fs", len(tasks), time.time() - t0)
    return out


def run_pairwise(
    scenarios: list[dict[str, Any]], model: str, max_workers: int = 6
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    tasks: list[tuple[dict[str, Any], str, str]] = []
    for s in scenarios:
        labels = sorted(s["candidates"].keys())
        for x, y in itertools.combinations(labels, 2):
            tasks.append((s, x, y))

    def _one(task):
        s, x, y = task
        res = judge_pair_with_swap(
            reference_path=s["reference_path"],
            impl_x_path=s["candidates"][x],
            impl_y_path=s["candidates"][y],
            model=model,
            x_label=x,
            y_label=y,
        )
        res["app"] = s["app"]
        res["test_id"] = s["test_id"]
        return res

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_one, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            rows.append(f.result())
            logger.info("[pairwise %d/%d] done", i, len(tasks))
    logger.info("Pairwise total: %d swap-pairs (%d calls) in %.1fs",
                len(tasks), len(tasks) * 2, time.time() - t0)
    return rows


def run_listwise(
    scenarios: list[dict[str, Any]], model: str, k: int, max_workers: int = 3
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _one(s):
        res = rank_candidates_k_shuffle(
            reference_path=s["reference_path"],
            candidates=s["candidates"],
            model=model,
            k=k,
        )
        res["app"] = s["app"]
        res["test_id"] = s["test_id"]
        return res

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_one, s) for s in scenarios]
        for i, f in enumerate(as_completed(futs), 1):
            rows.append(f.result())
            logger.info("[listwise %d/%d] %s done", i, len(scenarios), rows[-1]["test_id"])
    logger.info("Listwise total: %d test_ids × k=%d (%d calls) in %.1fs",
                len(scenarios), k, len(scenarios) * k, time.time() - t0)
    return rows


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------


def compute_pairwise_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Swap consistency, transitivity, and a pair-wise derived per-test_id ranking."""
    n_total = len(rows)
    n_consistent = sum(1 for r in rows if r.get("consistent"))
    swap_consistency = n_consistent / n_total if n_total else 0.0

    # Build per-test_id preference: for each unordered pair, final_winner
    by_tid: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_tid.setdefault(r["test_id"], []).append(r)

    transitivity_stats = {"triples_checked": 0, "triples_violated": 0}
    per_tid_rankings: dict[str, list[str]] = {}

    for tid, prows in by_tid.items():
        # Extract models
        models: set[str] = set()
        for r in prows:
            models.add(r["x_label"])
            models.add(r["y_label"])
        models_l = sorted(models)

        # Win counts for ranking
        wins: dict[str, float] = {m: 0.0 for m in models_l}
        for r in prows:
            w = r.get("final_winner")
            if w == "TIE" or w is None:
                wins[r["x_label"]] += 0.5
                wins[r["y_label"]] += 0.5
            elif w in wins:
                wins[w] += 1.0
        ranking = sorted(models_l, key=lambda m: -wins[m])
        per_tid_rankings[tid] = ranking

        # Transitivity: for each triple A>B, B>C, check A>C
        # Build pref map (a, b) -> winner
        pref: dict[tuple[str, str], str] = {}
        for r in prows:
            w = r.get("final_winner")
            if w == "TIE" or w is None:
                continue
            loser = r["y_label"] if w == r["x_label"] else r["x_label"]
            pref[(w, loser)] = w

        for a, b, c in itertools.permutations(models_l, 3):
            if (a, b) in pref and (b, c) in pref:
                transitivity_stats["triples_checked"] += 1
                if pref.get((a, c)) != a:
                    # violated — either equal/tie or (c,a) instead
                    transitivity_stats["triples_violated"] += 1

    transitivity_rate = (
        1 - transitivity_stats["triples_violated"] / transitivity_stats["triples_checked"]
        if transitivity_stats["triples_checked"] else None
    )

    return {
        "pairs_total": n_total,
        "swap_consistency_rate": round(swap_consistency, 4),
        "transitivity_triples_checked": transitivity_stats["triples_checked"],
        "transitivity_triples_violated": transitivity_stats["triples_violated"],
        "transitivity_rate": round(transitivity_rate, 4) if transitivity_rate is not None else None,
        "per_tid_rankings": per_tid_rankings,
    }


def compute_listwise_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    anchor_rates = []
    stabilities = []
    per_tid_rankings: dict[str, list[str]] = {}
    per_tid_anchor: dict[str, float] = {}
    per_tid_stability: dict[str, float | None] = {}

    for r in rows:
        apr = r.get("anchor_pass_rate")
        if apr is not None:
            anchor_rates.append(apr)
            per_tid_anchor[r["test_id"]] = apr
        st = r.get("pairwise_stability")
        per_tid_stability[r["test_id"]] = st
        if st is not None:
            stabilities.append(st)
        per_tid_rankings[r["test_id"]] = r.get("final_ranking", [])

    return {
        "n_test_ids": len(rows),
        "avg_anchor_pass_rate": round(sum(anchor_rates) / len(anchor_rates), 4) if anchor_rates else None,
        "avg_shuffle_stability": round(sum(stabilities) / len(stabilities), 4) if stabilities else None,
        "per_tid_anchor_pass_rate": per_tid_anchor,
        "per_tid_shuffle_stability": per_tid_stability,
        "per_tid_rankings": per_tid_rankings,
    }


def compute_cross_method_agreement(
    pw_rankings: dict[str, list[str]], lw_rankings: dict[str, list[str]]
) -> dict[str, Any]:
    taus: dict[str, float | None] = {}
    values: list[float] = []
    for tid in pw_rankings:
        if tid in lw_rankings:
            tau = _kendall_tau(pw_rankings[tid], lw_rankings[tid])
            taus[tid] = tau
            if tau is not None:
                values.append(tau)
    return {
        "per_tid_tau": taus,
        "avg_tau_pairwise_vs_listwise": round(sum(values) / len(values), 4) if values else None,
    }


def compute_pointwise_ranking(
    pointwise: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, list[str]]:
    """Derive a ranking per test_id from existing point-wise scores using
    the weighted sum that :mod:`testgen.visual_judge` would compute.
    """
    from testgen.visual_judge import DIMENSION_WEIGHTS

    rankings: dict[str, list[str]] = {}
    for tid, per_model in pointwise.items():
        scored: list[tuple[str, float]] = []
        for mt, sc in per_model.items():
            if not sc:
                continue
            wsum = sum(sc.get(d, 3) * w for d, w in DIMENSION_WEIGHTS.items())
            scored.append((mt, wsum))
        rankings[tid] = [mt for mt, _ in sorted(scored, key=lambda p: -p[1])]
    return rankings


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------


def write_report(
    out_dir: Path,
    config: dict[str, Any],
    pw_metrics: dict[str, Any],
    lw_metrics: dict[str, Any],
    cross: dict[str, Any],
    pt_lw_cross: dict[str, Any] | None,
    pt_pw_cross: dict[str, Any] | None,
    scenarios: list[dict[str, Any]],
) -> None:
    lines: list[str] = []
    lines.append(f"# Judge-Method Study · Phase 0 Smoke Test\n")
    lines.append(f"- **Run at**: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- **Judge model**: `{config['judge_model']}`")
    lines.append(f"- **App**: `{config['app']}`")
    lines.append(f"- **Test ids** ({len(scenarios)}): " + ", ".join(f"`{s['test_id']}`" for s in scenarios))
    n_models = len(scenarios[0]["candidates"]) if scenarios else 0
    lines.append(f"- **Models per test_id**: {n_models}")
    lines.append("")

    lines.append("## Self-consistency metrics\n")
    lines.append("| Metric | Value | Pass threshold |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Pair-wise swap consistency | {fmt_pct(pw_metrics['swap_consistency_rate'])} | ≥ 90% |"
    )
    lines.append(
        f"| Pair-wise transitivity rate | {fmt_pct(pw_metrics['transitivity_rate'])} "
        f"({pw_metrics['transitivity_triples_checked']} triples) | ≥ 85% |"
    )
    lines.append(
        f"| List-wise anchor pass rate | {fmt_pct(lw_metrics['avg_anchor_pass_rate'])} | ≥ 95% |"
    )
    lines.append(
        f"| List-wise shuffle stability (Kendall τ) | {fmt_num(lw_metrics['avg_shuffle_stability'])} | ≥ 0.7 |"
    )
    lines.append(
        f"| Pair-wise vs List-wise rank agreement (τ) | {fmt_num(cross['avg_tau_pairwise_vs_listwise'])} | observational |"
    )
    if pt_lw_cross is not None:
        lines.append(
            f"| Point-wise vs List-wise rank agreement (τ) | {fmt_num(pt_lw_cross.get('avg_tau'))} | observational |"
        )
    if pt_pw_cross is not None:
        lines.append(
            f"| Point-wise vs Pair-wise rank agreement (τ) | {fmt_num(pt_pw_cross.get('avg_tau'))} | observational |"
        )
    lines.append("")

    lines.append("## Per-test_id details\n")
    for s in scenarios:
        tid = s["test_id"]
        lines.append(f"### `{tid}` ({s.get('type')})")
        pw_rank = pw_metrics["per_tid_rankings"].get(tid, [])
        lw_rank = lw_metrics["per_tid_rankings"].get(tid, [])
        lines.append(f"- Pair-wise ranking (win count): `{' > '.join(pw_rank)}`")
        lines.append(
            f"- List-wise ranking (Borda): `{' > '.join(lw_rank)}` "
            f"(anchor pass={fmt_pct(lw_metrics['per_tid_anchor_pass_rate'].get(tid))}, "
            f"shuffle τ={fmt_num(lw_metrics['per_tid_shuffle_stability'].get(tid))})"
        )
        tau = cross["per_tid_tau"].get(tid)
        lines.append(f"- Pair-wise vs List-wise τ: {fmt_num(tau)}")
        lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "REPORT.md").write_text("\n".join(lines))


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v*100:.1f}%"


def fmt_num(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.3f}"


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-0 smoke test")
    ap.add_argument("--experiment", required=True, type=Path)
    ap.add_argument("--app", required=True, type=str)
    ap.add_argument("--test-ids", required=True, nargs="+", type=str)
    ap.add_argument("--judge-model", required=True, type=str)
    ap.add_argument("--vlm-click-tag", default="gemini-2.5-flash", type=str,
                    help="Which VLM click tag's screenshots to read from each run/eval/test_screenshots/<tag>/")
    ap.add_argument("--input-root", default=Path("input/mobileforge"), type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--k-shuffle", default=3, type=int)
    ap.add_argument("--skip-pointwise", action="store_true",
                    help="Skip re-running point-wise (useful when you already have visual_report.json)")
    ap.add_argument("--pairwise-workers", default=6, type=int)
    ap.add_argument("--listwise-workers", default=3, type=int)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = build_scenarios(
        experiment_dir=args.experiment,
        app=args.app,
        test_ids=args.test_ids,
        vlm_click_tag=args.vlm_click_tag,
        input_root=args.input_root,
    )
    if not scenarios:
        logger.error("No usable scenarios — aborting")
        return

    cfg = {
        "experiment": str(args.experiment),
        "app": args.app,
        "test_ids": args.test_ids,
        "judge_model": args.judge_model,
        "vlm_click_tag": args.vlm_click_tag,
        "k_shuffle": args.k_shuffle,
        "n_scenarios": len(scenarios),
        "n_candidates_per_scenario": {s["test_id"]: len(s["candidates"]) for s in scenarios},
    }
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    logger.info("Scenarios: %d", len(scenarios))

    # -------- Point-wise --------
    pointwise_rankings: dict[str, list[str]] = {}
    if not args.skip_pointwise:
        logger.info("═══ Point-wise ═══")
        pw_scores = run_pointwise(scenarios, args.judge_model,
                                  max_workers=args.pairwise_workers)
        (out_dir / "pointwise_raw.json").write_text(
            json.dumps(pw_scores, indent=2, ensure_ascii=False)
        )
        pointwise_rankings = compute_pointwise_ranking(pw_scores)

    # -------- Pair-wise --------
    logger.info("═══ Pair-wise (with swap) ═══")
    pw_rows = run_pairwise(scenarios, args.judge_model,
                           max_workers=args.pairwise_workers)
    with (out_dir / "pairwise_raw.jsonl").open("w") as f:
        for row in pw_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    pw_metrics = compute_pairwise_metrics(pw_rows)

    # -------- List-wise --------
    logger.info("═══ List-wise (with anchor, k=%d shuffle) ═══", args.k_shuffle)
    lw_rows = run_listwise(scenarios, args.judge_model, k=args.k_shuffle,
                           max_workers=args.listwise_workers)
    with (out_dir / "listwise_raw.jsonl").open("w") as f:
        for row in lw_rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    lw_metrics = compute_listwise_metrics(lw_rows)

    # -------- Cross-method --------
    cross_pw_lw = compute_cross_method_agreement(
        pw_metrics["per_tid_rankings"], lw_metrics["per_tid_rankings"]
    )

    pt_lw_cross: dict[str, Any] | None = None
    pt_pw_cross: dict[str, Any] | None = None
    if pointwise_rankings:
        c1 = compute_cross_method_agreement(pointwise_rankings, lw_metrics["per_tid_rankings"])
        pt_lw_cross = {
            "per_tid_tau": c1["per_tid_tau"],
            "avg_tau": c1["avg_tau_pairwise_vs_listwise"],
        }
        c2 = compute_cross_method_agreement(pointwise_rankings, pw_metrics["per_tid_rankings"])
        pt_pw_cross = {
            "per_tid_tau": c2["per_tid_tau"],
            "avg_tau": c2["avg_tau_pairwise_vs_listwise"],
        }

    metrics = {
        "config": cfg,
        "pairwise": pw_metrics,
        "listwise": lw_metrics,
        "cross_pairwise_vs_listwise": cross_pw_lw,
        "pointwise_rankings": pointwise_rankings,
        "pointwise_vs_listwise": pt_lw_cross,
        "pointwise_vs_pairwise": pt_pw_cross,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    write_report(
        out_dir, cfg, pw_metrics, lw_metrics, cross_pw_lw,
        pt_lw_cross, pt_pw_cross, scenarios,
    )
    logger.info("Done. See %s/REPORT.md", out_dir)


if __name__ == "__main__":
    main()
