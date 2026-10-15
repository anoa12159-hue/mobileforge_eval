"""Migrate phase0/listwise_raw.jsonl into the run_listwise cache layout.

One-off helper — run once, then delete.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluator.utils import sanitize_model_tag  # noqa: E402


def migrate(
    phase0_dir: Path,
    cache_root: Path,
    experiment_dir: Path,
    judge_model: str,
    app: str,
    input_root: Path,
    vlm_click_tag: str,
) -> None:
    from scripts.judge_method_study.run_listwise import (  # noqa: E402
        discover_runs, resolve_impl_path, resolve_reference_path,
        load_cases, list_all_model_tags,
    )

    raw_path = phase0_dir / "listwise_raw.jsonl"
    config_path = phase0_dir / "config.json"
    if not raw_path.is_file():
        raise SystemExit(f"Not found: {raw_path}")
    cfg = json.loads(config_path.read_text())
    k = cfg.get("k_shuffle", 3)

    all_model_tags = list_all_model_tags(experiment_dir)
    runs = discover_runs(experiment_dir, app)
    cases = load_cases(input_root / app)

    judge_tag = sanitize_model_tag(judge_model)
    cache_root.mkdir(parents=True, exist_ok=True)
    migrated = 0

    with raw_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            tid = r.get("test_id")
            if not tid:
                continue
            case = cases.get(tid) or {}
            ref = resolve_reference_path(input_root / app, case)
            ref_str = str(ref) if ref else ""

            # Reconstruct presence / missing from current disk state
            present: dict[str, str] = {}
            for mt in all_model_tags:
                run_dir = runs.get(mt)
                if run_dir is None:
                    continue
                impl = resolve_impl_path(run_dir, vlm_click_tag, tid)
                if impl is not None:
                    present[mt] = str(impl)
            missing = sorted([mt for mt in all_model_tags if mt not in present])

            entry = {
                "app": app,
                "test_id": tid,
                "type": case.get("type"),
                "judge_model": judge_model,
                "k_shuffle": k,
                "reference_path": ref_str,
                "present_candidates": sorted(present.keys()),
                "missing_models": missing,
                "anchor_pass_rate": r.get("anchor_pass_rate"),
                "shuffle_stability": r.get("pairwise_stability"),
                "borda_scores": r.get("borda_scores"),
                "final_ranking_present": list(r.get("final_ranking") or []),
                "final_ranking": list(r.get("final_ranking") or []) + missing,
                "trials": r.get("trials"),
                "judged_at": datetime.now().isoformat(timespec="seconds"),
                "migrated_from": str(raw_path),
            }

            out = cache_root / app / f"{tid}__{judge_tag}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(entry, indent=2, ensure_ascii=False, default=str))
            migrated += 1
            print(f"  ✓ {app}/{tid} → {out}")

    print(f"Migrated {migrated} entries")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase0-dir", default=Path("experiments/judge_method_study/phase0"), type=Path)
    ap.add_argument("--cache-root", default=None, type=Path)
    ap.add_argument("--experiment", required=True, type=Path)
    ap.add_argument("--judge-model", required=True, type=str)
    ap.add_argument("--app", required=True, type=str)
    ap.add_argument("--input-root", default=Path("input/mobileforge"), type=Path)
    ap.add_argument("--vlm-click-tag", default="gemini-2.5-flash", type=str)
    args = ap.parse_args()

    experiment_dir = args.experiment.resolve()
    if args.cache_root is not None:
        cache_root = args.cache_root
    else:
        study_root = _REPO_ROOT / "experiments" / "judge_method_study" / "listwise_cache"
        cache_root = study_root / experiment_dir.name
    migrate(
        phase0_dir=args.phase0_dir,
        cache_root=cache_root,
        experiment_dir=experiment_dir,
        judge_model=args.judge_model,
        app=args.app,
        input_root=args.input_root,
        vlm_click_tag=args.vlm_click_tag,
    )


if __name__ == "__main__":
    main()
