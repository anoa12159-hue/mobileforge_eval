"""Full-scale list-wise judge runner with per-(app, test_id) caching.

Replaces the Phase-0 smoke test for actual evaluation use. Key differences
from ``run_phase0.py``:

1. Only list-wise method (with reference anchor + k-shuffle + Borda).
2. Results are cached per (app, test_id) so reruns skip already-judged
   combinations.
3. Candidates without screenshots (e.g. failed runs) are **not** sent to the
   VLM but are appended to the tail of the final ranking, so models with
   missing artefacts are treated as "least fidelity" rather than dropped.

Default behaviour: scan every (app, test_id) under the given experiment and
fill in all missing cache entries. Use ``--apps`` / ``--test-ids`` to narrow.

Usage::

    source env.sh
    python -m scripts.judge_method_study.run_listwise \\
        --experiment experiments/20260428_181902_mobileforge-compare \\
        --judge-model gemini-2.5-pro \\
        --vlm-click-tag gemini-2.5-flash \\
        --k-shuffle 3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testgen.visual_judge_listwise import rank_candidates_k_shuffle, _kendall_tau  # noqa: E402

logger = logging.getLogger("run_listwise")


# ------------------------------------------------------------------
# Scenario discovery
# ------------------------------------------------------------------


def discover_runs(experiment_dir: Path, app: str) -> dict[str, Path]:
    """Return {model_tag: run_dir} for the given app."""
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


def list_apps(experiment_dir: Path) -> list[str]:
    runs_dir = experiment_dir / "runs"
    apps: set[str] = set()
    for sub in runs_dir.iterdir():
        if sub.is_dir():
            parts = sub.name.split("__")
            if len(parts) >= 3:
                apps.add(parts[0])
    return sorted(apps)


def resolve_impl_path(run_dir: Path, vlm_click_tag: str, test_id: str) -> Path | None:
    """Prefer __after then __before screenshot for this test_id."""
    shots_dir = run_dir / "eval" / "test_screenshots" / vlm_click_tag
    after = shots_dir / f"{test_id}__after.png"
    before = shots_dir / f"{test_id}__before.png"
    if after.is_file():
        return after
    if before.is_file():
        return before
    return None


def resolve_reference_path(app_input_dir: Path, case: dict[str, Any]) -> Path | None:
    for side in ("to_page", "from_page"):
        fname = (case.get(side) or {}).get("reference_screenshot")
        if fname:
            p = app_input_dir / fname
            if p.is_file():
                return p
    return None


def load_cases(app_input_dir: Path) -> dict[str, dict[str, Any]]:
    for name in ("test_cases.reviewed.json", "test_cases.json"):
        p = app_input_dir / name
        if p.is_file():
            data = json.loads(p.read_text())
            return {c["test_id"]: c for c in data.get("cases", [])}
    raise FileNotFoundError(f"No test_cases*.json in {app_input_dir}")


def build_scenarios(
    experiment_dir: Path,
    app: str,
    test_ids: list[str] | None,
    vlm_click_tag: str,
    input_root: Path,
    all_model_tags: list[str],
) -> list[dict[str, Any]]:
    """Assemble scenarios. For each (app, test_id):

    - ``present_candidates``: {model_tag: impl_path} — fed to the VLM
    - ``missing_models``: [model_tag, ...] — appended to ranking tail

    ``all_model_tags`` is the full list of model tags present in the
    experiment (union across apps). Missing models are determined against
    this list, not against what happens to exist for this particular app.
    """
    app_input = input_root / app
    try:
        cases = load_cases(app_input)
    except FileNotFoundError as e:
        logger.warning("%s: %s — skipping app", app, e)
        return []

    runs = discover_runs(experiment_dir, app)
    if not runs:
        logger.warning("No runs for app=%s — skipping", app)
        return []

    iter_test_ids = test_ids if test_ids else list(cases.keys())

    scenarios: list[dict[str, Any]] = []
    for tid in iter_test_ids:
        case = cases.get(tid)
        if not case:
            logger.warning("%s/%s: test_id not in test_cases.json", app, tid)
            continue
        ref = resolve_reference_path(app_input, case)
        if ref is None:
            logger.warning("%s/%s: reference screenshot missing", app, tid)
            continue

        present: dict[str, str] = {}
        for mt in all_model_tags:
            run_dir = runs.get(mt)
            if run_dir is None:
                continue
            impl = resolve_impl_path(run_dir, vlm_click_tag, tid)
            if impl is None:
                continue
            present[mt] = str(impl)

        missing = [mt for mt in all_model_tags if mt not in present]

        if len(present) < 2:
            logger.warning(
                "%s/%s: fewer than 2 present candidates (%s) — skipping",
                app, tid, list(present.keys()),
            )
            continue

        scenarios.append({
            "app": app,
            "test_id": tid,
            "type": case.get("type"),
            "reference_path": str(ref),
            "present_candidates": present,
            "missing_models": missing,
        })
    return scenarios


def list_all_model_tags(experiment_dir: Path) -> list[str]:
    """Union of model tags across all apps."""
    tags: set[str] = set()
    for sub in (experiment_dir / "runs").iterdir():
        if sub.is_dir():
            parts = sub.name.split("__")
            if len(parts) >= 3:
                tags.add(parts[1])
    return sorted(tags)


# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------


def cache_path(cache_root: Path, app: str, test_id: str, judge_model: str) -> Path:
    """Canonical cache file for one (app, test_id, judge_model)."""
    from evaluator.utils import sanitize_model_tag
    judge_tag = sanitize_model_tag(judge_model)
    return cache_root / app / f"{test_id}__{judge_tag}.json"


def load_cache_entry(
    path: Path,
    *,
    expected_present: set[str],
    expected_missing: set[str],
    expected_k: int,
) -> dict[str, Any] | None:
    """Return cached result only if the inputs match exactly."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        logger.warning("Cache %s unreadable (%s) — ignoring", path, e)
        return None

    cached_present = set(data.get("present_candidates", []))
    cached_missing = set(data.get("missing_models", []))
    cached_k = data.get("k_shuffle")

    if cached_present != expected_present:
        logger.info(
            "Cache miss (%s): present set changed %s → %s",
            path.name, cached_present, expected_present,
        )
        return None
    if cached_missing != expected_missing:
        logger.info(
            "Cache miss (%s): missing set changed %s → %s",
            path.name, cached_missing, expected_missing,
        )
        return None
    if cached_k is not None and cached_k < expected_k:
        logger.info(
            "Cache miss (%s): k_shuffle %d < requested %d",
            path.name, cached_k, expected_k,
        )
        return None

    return data


def save_cache_entry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False, default=str))
    tmp.replace(path)


# ------------------------------------------------------------------
# Core
# ------------------------------------------------------------------


def judge_one_scenario(
    s: dict[str, Any],
    judge_model: str,
    k: int,
) -> dict[str, Any]:
    """Run k-shuffle list-wise + append missing models to tail."""
    res = rank_candidates_k_shuffle(
        reference_path=s["reference_path"],
        candidates=s["present_candidates"],
        model=judge_model,
        k=k,
    )

    # Append missing models (stable: sorted) to tail of final_ranking
    missing = sorted(s["missing_models"])
    final_ranking = list(res.get("final_ranking") or [])
    final_ranking_with_missing = final_ranking + missing

    entry = {
        "app": s["app"],
        "test_id": s["test_id"],
        "type": s.get("type"),
        "judge_model": judge_model,
        "k_shuffle": k,
        "reference_path": s["reference_path"],
        "present_candidates": sorted(s["present_candidates"].keys()),
        "missing_models": missing,
        "anchor_pass_rate": res.get("anchor_pass_rate"),
        "shuffle_stability": res.get("pairwise_stability"),
        "borda_scores": res.get("borda_scores"),
        "final_ranking_present": final_ranking,
        "final_ranking": final_ranking_with_missing,
        "trials": res.get("trials"),
        "judged_at": datetime.now().isoformat(timespec="seconds"),
    }
    return entry


def process_scenarios(
    scenarios: list[dict[str, Any]],
    judge_model: str,
    k: int,
    cache_root: Path,
    max_workers: int,
    force: bool,
) -> dict[str, Any]:
    """Run (or load cached) list-wise for every scenario."""
    from evaluator.utils import sanitize_model_tag  # noqa: F401 (sanity import)

    # First pass: cache hit / miss
    todo: list[tuple[dict[str, Any], Path]] = []
    cached_entries: list[dict[str, Any]] = []
    for s in scenarios:
        cp = cache_path(cache_root, s["app"], s["test_id"], judge_model)
        if not force:
            entry = load_cache_entry(
                cp,
                expected_present=set(s["present_candidates"].keys()),
                expected_missing=set(s["missing_models"]),
                expected_k=k,
            )
            if entry is not None:
                cached_entries.append(entry)
                continue
        todo.append((s, cp))

    logger.info(
        "Scenarios total=%d cached=%d todo=%d",
        len(scenarios), len(cached_entries), len(todo),
    )

    new_entries: list[dict[str, Any]] = []

    def _one(pair):
        s, cp = pair
        try:
            entry = judge_one_scenario(s, judge_model, k)
        except Exception as exc:
            logger.warning("Judge failed for %s/%s: %s", s["app"], s["test_id"], exc)
            return None, s, cp
        save_cache_entry(cp, entry)
        return entry, s, cp

    if todo:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_one, pair) for pair in todo]
            for i, f in enumerate(as_completed(futs), 1):
                entry, s, cp = f.result()
                if entry is not None:
                    new_entries.append(entry)
                logger.info(
                    "[%d/%d] %s/%s done (anchor=%.0f%%, τ=%.3f)",
                    i, len(todo), s["app"], s["test_id"],
                    (entry or {}).get("anchor_pass_rate", 0) * 100,
                    (entry or {}).get("shuffle_stability") or 0.0,
                )
        logger.info("Fresh calls: %d scenarios × k=%d in %.1fs",
                    len(todo), k, time.time() - t0)

    return {
        "cached": cached_entries,
        "new": new_entries,
    }


# ------------------------------------------------------------------
# Aggregation
# ------------------------------------------------------------------


def aggregate_model_ranking(
    entries: list[dict[str, Any]],
    all_model_tags: list[str],
) -> dict[str, Any]:
    """Aggregate per-scenario rankings into a global model ranking via
    average Borda score normalized to [0, 1].

    Missing models always get rank=last, which corresponds to 0 points in
    this scenario's Borda contribution.
    """
    if not entries:
        return {"per_app": {}, "global": {}}

    n_models = len(all_model_tags)

    per_app_totals: dict[str, dict[str, float]] = {}
    per_app_counts: dict[str, int] = {}
    global_totals: dict[str, float] = {mt: 0.0 for mt in all_model_tags}
    global_counts = 0

    for e in entries:
        app = e["app"]
        ranking = e["final_ranking"]
        # Pad to n_models with any remaining tags at the tail (stable)
        for mt in all_model_tags:
            if mt not in ranking:
                ranking = ranking + [mt]

        # Borda: best position = n-1 points, worst = 0
        app_d = per_app_totals.setdefault(app, {mt: 0.0 for mt in all_model_tags})
        for pos, mt in enumerate(ranking):
            pts = (n_models - 1 - pos) / (n_models - 1) if n_models > 1 else 0
            app_d[mt] += pts
            global_totals[mt] += pts

        per_app_counts[app] = per_app_counts.get(app, 0) + 1
        global_counts += 1

    per_app_avg: dict[str, dict[str, float]] = {}
    for app, d in per_app_totals.items():
        c = per_app_counts[app]
        per_app_avg[app] = {mt: round(pts / c, 4) for mt, pts in d.items()}

    global_avg = {mt: round(pts / global_counts, 4) for mt, pts in global_totals.items()}

    return {
        "per_app": per_app_avg,
        "global": global_avg,
        "n_scenarios_total": global_counts,
        "n_scenarios_per_app": per_app_counts,
    }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Full-scale list-wise judge runner")
    ap.add_argument("--experiment", required=True, type=Path)
    ap.add_argument("--judge-model", required=True, type=str)
    ap.add_argument("--vlm-click-tag", default="gemini-2.5-flash", type=str)
    ap.add_argument("--input-root", default=Path("input/mobileforge"), type=Path)
    ap.add_argument("--cache-root", default=None, type=Path,
                    help="Default: experiments/judge_method_study/listwise_cache/<experiment_name>/")
    ap.add_argument("--summary-out", default=None, type=Path,
                    help="Summary JSON. Default: <cache-root>/_summary__<judge>.json")
    ap.add_argument("--apps", nargs="*", default=None,
                    help="Filter to these apps (default: all)")
    ap.add_argument("--test-ids", nargs="*", default=None,
                    help="Filter to these test_ids (applied to every app)")
    ap.add_argument("--k-shuffle", default=3, type=int)
    ap.add_argument("--max-workers", default=4, type=int)
    ap.add_argument("--force", action="store_true",
                    help="Ignore cache and rerun every scenario")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only report scenario counts, don't call the VLM")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    from evaluator.utils import sanitize_model_tag

    experiment_dir: Path = args.experiment.resolve()
    if args.cache_root is not None:
        cache_root = args.cache_root
    else:
        # Default: experiments/judge_method_study/listwise_cache/<experiment_name>/
        study_root = _REPO_ROOT / "experiments" / "judge_method_study" / "listwise_cache"
        cache_root = study_root / experiment_dir.name
    cache_root.mkdir(parents=True, exist_ok=True)

    apps = args.apps or list_apps(experiment_dir)
    all_model_tags = list_all_model_tags(experiment_dir)
    logger.info("Apps: %d | Model tags: %s | Judge: %s | k=%d",
                len(apps), all_model_tags, args.judge_model, args.k_shuffle)
    logger.info("Cache root: %s", cache_root)

    all_scenarios: list[dict[str, Any]] = []
    for app in apps:
        scns = build_scenarios(
            experiment_dir=experiment_dir,
            app=app,
            test_ids=args.test_ids,
            vlm_click_tag=args.vlm_click_tag,
            input_root=args.input_root,
            all_model_tags=all_model_tags,
        )
        all_scenarios.extend(scns)

    logger.info("Total scenarios to consider: %d", len(all_scenarios))
    missing_stats = sum(len(s["missing_models"]) for s in all_scenarios)
    if missing_stats:
        logger.info("Total 'missing model' slots (appended to tail): %d", missing_stats)

    if args.dry_run:
        # Show cache-miss count without calling the VLM
        miss = 0
        for s in all_scenarios:
            cp = cache_path(cache_root, s["app"], s["test_id"], args.judge_model)
            entry = load_cache_entry(
                cp,
                expected_present=set(s["present_candidates"].keys()),
                expected_missing=set(s["missing_models"]),
                expected_k=args.k_shuffle,
            )
            if entry is None:
                miss += 1
        logger.info("Dry run: cache miss=%d / total=%d", miss, len(all_scenarios))
        return

    outcome = process_scenarios(
        scenarios=all_scenarios,
        judge_model=args.judge_model,
        k=args.k_shuffle,
        cache_root=cache_root,
        max_workers=args.max_workers,
        force=args.force,
    )
    all_entries = outcome["cached"] + outcome["new"]

    agg = aggregate_model_ranking(all_entries, all_model_tags)

    # Quality summary
    anchor_rates = [e["anchor_pass_rate"] for e in all_entries if e.get("anchor_pass_rate") is not None]
    stabilities = [e["shuffle_stability"] for e in all_entries if e.get("shuffle_stability") is not None]

    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "experiment": str(experiment_dir),
        "judge_model": args.judge_model,
        "vlm_click_tag": args.vlm_click_tag,
        "k_shuffle": args.k_shuffle,
        "apps": apps,
        "all_model_tags": all_model_tags,
        "n_scenarios": len(all_entries),
        "n_cached": len(outcome["cached"]),
        "n_fresh": len(outcome["new"]),
        "quality": {
            "avg_anchor_pass_rate": round(sum(anchor_rates) / len(anchor_rates), 4) if anchor_rates else None,
            "avg_shuffle_stability": round(sum(stabilities) / len(stabilities), 4) if stabilities else None,
            "n_anchor_failures": sum(1 for r in anchor_rates if r < 1.0),
        },
        "ranking": agg,
    }

    summary_path = args.summary_out or (
        cache_root / f"_summary__{sanitize_model_tag(args.judge_model)}.json"
    )
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    logger.info("Summary → %s", summary_path)

    # Human-readable global ranking
    global_sorted = sorted(agg["global"].items(), key=lambda p: -p[1])
    logger.info("──── Global model ranking (avg Borda, higher=better) ────")
    for mt, score in global_sorted:
        logger.info("  %-24s %.4f", mt, score)


if __name__ == "__main__":
    main()
