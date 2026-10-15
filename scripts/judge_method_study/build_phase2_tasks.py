"""Pick Phase-2 human-labeling tasks from the list-wise cache.

Reads every cache file under ``<cache-root>/<experiment>/<app>/`` and
ranks candidate scenarios by how much they "need" a human oracle, then
writes the chosen subset to ``phase2/tasks.json`` for the label server.

Selection strategies (can be combined with proportions):

  - ``shuffle_unstable``: smallest ``shuffle_stability`` (list-wise is unsure)
  - ``close_borda``:       smallest top-1/top-2 Borda gap (close call)
  - ``low_anchor``:        ``anchor_pass_rate`` < 1.0
  - ``stratified_random``: random sample, at most one per app

Default mix: 4 shuffle_unstable + 4 close_borda + 3 stratified_random = 11.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("build_phase2_tasks")


def load_cache_entries(cache_dir: Path) -> list[dict[str, Any]]:
    """Load every *.json cache file (skip _summary__*)."""
    entries: list[dict[str, Any]] = []
    for app_dir in sorted(cache_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        for f in sorted(app_dir.glob("*.json")):
            if f.name.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text())
                data["_cache_path"] = str(f)
                entries.append(data)
            except Exception as e:
                logger.warning("Unreadable cache %s: %s", f, e)
    return entries


def compute_borda_gap(entry: dict[str, Any]) -> float:
    """Gap between top-1 and top-2 Borda scores among present candidates.

    Smaller = closer call = more in need of human oracle.
    Returns a large number (inf) if there are <2 present candidates.
    """
    scores = entry.get("borda_scores") or {}
    vals = sorted(scores.values(), reverse=True)
    if len(vals) < 2:
        return float("inf")
    return vals[0] - vals[1]


def compute_task_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields the label server needs per task."""
    # The first trial records which impl path was shown for each candidate
    # label (presented_order). We reconstruct from the cache the label -> path
    # mapping using the trial's presented_order + the ref path for REF.
    trials = entry.get("trials") or []
    label_to_path: dict[str, str] = {}
    anchor_label: str | None = None
    for t in trials:
        if t is None:
            continue
        al = t.get("anchor_label")
        anchor_label = al or anchor_label
        # presented_order is list of labels in the order shown to VLM
        # but the actual (label -> path) mapping is implicit: anchor = ref, others = candidate impls.
        # We get it from the entry's reference_path + the original candidates (not persisted...),
        # so fall back: scan scenario's source paths via run discovery at label time.
        break

    return {
        "app": entry["app"],
        "test_id": entry["test_id"],
        "type": entry.get("type"),
        "reference_path": entry["reference_path"],
        "present_candidates": entry["present_candidates"],
        "missing_models": entry.get("missing_models", []),
        "listwise_final_ranking_present": entry.get("final_ranking_present") or entry.get("final_ranking", []),
        "borda_scores": entry.get("borda_scores", {}),
        "anchor_pass_rate": entry.get("anchor_pass_rate"),
        "shuffle_stability": entry.get("shuffle_stability"),
        "cache_path": entry["_cache_path"],
    }


def attach_impl_paths(
    task: dict[str, Any],
    experiment_dir: Path,
    vlm_click_tag: str,
) -> dict[str, Any]:
    """Resolve {label: impl_path} for each present candidate, scanning runs."""
    from scripts.judge_method_study.run_listwise import (  # noqa: E402
        discover_runs, resolve_impl_path,
    )
    runs = discover_runs(experiment_dir, task["app"])
    label_paths: dict[str, str] = {}
    for mt in task["present_candidates"]:
        run_dir = runs.get(mt)
        if run_dir is None:
            continue
        p = resolve_impl_path(run_dir, vlm_click_tag, task["test_id"])
        if p is not None:
            label_paths[mt] = str(p)
    task["candidate_paths"] = label_paths
    return task


def pick_tasks(
    entries: list[dict[str, Any]],
    n_unstable: int,
    n_close: int,
    n_random: int,
    n_low_anchor: int,
    seed: int = 0,
    all_entries: bool = False,
) -> list[dict[str, Any]]:
    """Select a diverse set of tasks for human labeling.

    If ``all_entries`` is True, every entry in ``entries`` is picked
    (labeled as ``_selected_by = "all"``) and the ``n_*`` quotas are ignored.
    Useful for exhaustive labeling on a pre-filtered pool.
    """
    rng = random.Random(seed)

    if all_entries:
        picked: list[dict[str, Any]] = []
        for e in entries:
            e["_selected_by"] = "all"
            picked.append(e)
        return picked

    picked_keys: set[tuple[str, str]] = set()
    picked = []

    def key(e):
        return (e["app"], e["test_id"])

    # low_anchor first (rarest and most suspicious)
    low_anchor = [e for e in entries if (e.get("anchor_pass_rate") or 1.0) < 1.0]
    low_anchor.sort(key=lambda e: (e.get("anchor_pass_rate") or 1.0))
    for e in low_anchor[:n_low_anchor]:
        if key(e) not in picked_keys:
            e["_selected_by"] = "low_anchor"
            picked.append(e)
            picked_keys.add(key(e))

    # shuffle_unstable
    by_stab = sorted(
        [e for e in entries if e.get("shuffle_stability") is not None],
        key=lambda e: (e.get("shuffle_stability") or 0.0),
    )
    for e in by_stab:
        if len(picked) - len(low_anchor[:n_low_anchor]) >= n_unstable:
            break
        if key(e) in picked_keys:
            continue
        e["_selected_by"] = "shuffle_unstable"
        picked.append(e)
        picked_keys.add(key(e))

    # close_borda
    by_gap = sorted(entries, key=compute_borda_gap)
    added = 0
    for e in by_gap:
        if added >= n_close:
            break
        if key(e) in picked_keys:
            continue
        e["_selected_by"] = "close_borda"
        picked.append(e)
        picked_keys.add(key(e))
        added += 1

    # stratified random (at most 1 per app not already picked)
    by_app: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_app.setdefault(e["app"], []).append(e)
    rng.shuffle(list(by_app.keys()))
    added = 0
    for app in sorted(by_app.keys(), key=lambda _: rng.random()):
        if added >= n_random:
            break
        for e in rng.sample(by_app[app], len(by_app[app])):
            if key(e) not in picked_keys:
                e["_selected_by"] = "stratified_random"
                picked.append(e)
                picked_keys.add(key(e))
                added += 1
                break

    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description="Pick Phase-2 human-labeling tasks")
    ap.add_argument("--cache-dir", required=True, type=Path,
                    help="e.g. experiments/judge_method_study/listwise_cache/20260428_181902_mobileforge-compare")
    ap.add_argument("--experiment", required=True, type=Path,
                    help="Original experiment dir (to resolve impl image paths)")
    ap.add_argument("--vlm-click-tag", default="gemini-2.5-flash", type=str)
    ap.add_argument("--out", required=True, type=Path,
                    help="Where to write tasks.json (e.g. experiments/judge_method_study/phase2/tasks.json)")
    ap.add_argument("--n-unstable", default=4, type=int)
    ap.add_argument("--n-close", default=4, type=int)
    ap.add_argument("--n-random", default=3, type=int)
    ap.add_argument("--n-low-anchor", default=2, type=int)
    ap.add_argument(
        "--include-types",
        default=None,
        type=str,
        help="Comma-separated list of `type` values to keep "
             "(e.g. 'page_exists,parent_child_navigation'). "
             "If omitted, all types are eligible.",
    )
    ap.add_argument(
        "--exclude-types",
        default=None,
        type=str,
        help="Comma-separated list of `type` values to drop. "
             "Applied after --include-types.",
    )
    ap.add_argument(
        "--exclude-apps",
        default=None,
        type=str,
        help="Comma-separated list of apps to drop "
             "(e.g. 'coinbase' if its cache is incomplete).",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Pick every entry in the (filtered) pool, ignoring n-* quotas. "
             "Useful for exhaustive labeling after --include-types narrows the pool.",
    )
    ap.add_argument("--seed", default=0, type=int)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    entries = load_cache_entries(args.cache_dir)
    logger.info("Loaded %d cache entries from %s", len(entries), args.cache_dir)
    if not entries:
        logger.error("No cache entries — did you run run_listwise yet?")
        return

    if args.include_types:
        keep = {t.strip() for t in args.include_types.split(",") if t.strip()}
        before = len(entries)
        entries = [e for e in entries if e.get("type") in keep]
        logger.info(
            "--include-types %s: kept %d / %d entries",
            sorted(keep), len(entries), before,
        )
    if args.exclude_types:
        drop = {t.strip() for t in args.exclude_types.split(",") if t.strip()}
        before = len(entries)
        entries = [e for e in entries if e.get("type") not in drop]
        logger.info(
            "--exclude-types %s: kept %d / %d entries",
            sorted(drop), len(entries), before,
        )
    if args.exclude_apps:
        drop_apps = {a.strip() for a in args.exclude_apps.split(",") if a.strip()}
        before = len(entries)
        entries = [e for e in entries if e.get("app") not in drop_apps]
        logger.info(
            "--exclude-apps %s: kept %d / %d entries",
            sorted(drop_apps), len(entries), before,
        )
    if not entries:
        logger.error("No entries remain after type filters — nothing to label")
        return

    picked = pick_tasks(
        entries,
        n_unstable=args.n_unstable,
        n_close=args.n_close,
        n_random=args.n_random,
        n_low_anchor=args.n_low_anchor,
        seed=args.seed,
        all_entries=args.all,
    )

    tasks: list[dict[str, Any]] = []
    for i, e in enumerate(picked, 1):
        t = compute_task_payload(e)
        t["task_id"] = f"task_{i:03d}"
        t["selected_by"] = e.get("_selected_by")
        t = attach_impl_paths(t, args.experiment.resolve(), args.vlm_click_tag)
        tasks.append(t)

    out_dir = args.out.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "cache_dir": str(args.cache_dir),
        "experiment": str(args.experiment),
        "n_tasks": len(tasks),
        "selection_summary": {
            s: sum(1 for t in tasks if t["selected_by"] == s)
            for s in ["all", "low_anchor", "shuffle_unstable", "close_borda", "stratified_random"]
        },
        "include_types": args.include_types,
        "exclude_types": args.exclude_types,
        "exclude_apps": args.exclude_apps,
        "tasks": tasks,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Wrote %d tasks → %s", len(tasks), args.out)
    for t in tasks:
        logger.info(
            "  [%s] %s/%s (by %s, stab=%s, anchor=%s)",
            t["task_id"], t["app"], t["test_id"],
            t["selected_by"], t.get("shuffle_stability"), t.get("anchor_pass_rate"),
        )


if __name__ == "__main__":
    main()
