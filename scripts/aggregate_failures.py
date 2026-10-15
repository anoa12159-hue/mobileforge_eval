"""
Failure Aggregation for MobileForge
====================================

Pulls failure cases from all 174 runs and aggregates by:
- failure signal (nav_failure / visual_low_listwise / unrankable_by_human)
- model
- scenario type (page_exists / tab_navigation / parent_child_navigation)
- app

Two failure signals are derived:

1. **Navigation failure**: per-run `eval/summary.json` → `navigation_test.details[*]`
   where `passed=False` (the click model could not reach the expected page).

2. **Visual bottom-K (list-wise)**: per scenario, the model that received the
   *lowest* normalized Borda score in the list-wise judge cache. Reading the
   bottom-1 model per scenario gives a "who is the worst on this case" signal.

3. **Human unrankable** (only available for the 21 phase-2 tasks where the
   reviewer marked candidates as ✗). These are the strongest "really broken"
   signals because they came from human eyes.

Outputs:
  experiments/failures/REPORT.md           — markdown summary tables
  experiments/failures/failures.json       — full failure list (case x reason)
  experiments/failures/failure_screenshots/ — symlink farm to all failure screenshots

This script is read-only on user data; it only writes inside
`experiments/failures/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# -------------------- helpers --------------------

def parse_run_dir(name: str) -> tuple[str, str] | None:
    """Run dirs are named like `airbnb__claude-opus-4-6__agentic`."""
    m = re.match(r"^([a-z0-9-]+)__(.+?)__(agentic|monolithic|sequential|page_independent|plan_then_generate)$", name)
    if not m:
        return None
    return m.group(1), m.group(2)


def load_json(p: Path) -> Any | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# -------------------- nav failures --------------------

def collect_nav_failures(runs_dir: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        parsed = parse_run_dir(run_dir.name)
        if not parsed:
            continue
        app, model = parsed
        summary = load_json(run_dir / "eval" / "summary.json")
        if not summary:
            continue
        nav = (summary.get("results") or {}).get("navigation_test") or {}
        for det in nav.get("details", []):
            if det.get("passed"):
                continue
            failures.append({
                "app": app,
                "model": model,
                "test_id": det.get("test_id"),
                "type": det.get("type"),
                "screenshot_after": det.get("screenshot_after"),
                "screenshot_before": det.get("screenshot_before"),
                "expected_route": det.get("expected_route"),
                "final_url": det.get("final_url"),
                "error": det.get("error"),
                "failure_signal": "nav_failure",
            })
    return failures


# ---------------- visual bottom-1 from list-wise cache ----------------

def collect_visual_bottoms(cache_dir: Path, judge_model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """For each scenario cache file, find:
    - bottoms: the candidate(s) with the lowest Borda raw score among those
      actually scored (excludes models with missing screenshots).
    - missings: the candidates that had no screenshot at all for that scenario,
      tracked as a separate failure signal.
    """
    bottoms: list[dict[str, Any]] = []
    missings: list[dict[str, Any]] = []
    if not cache_dir.exists():
        return bottoms, missings
    suffix = f"__{judge_model}.json"
    for app_dir in sorted(cache_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        for fp in sorted(app_dir.glob(f"*{suffix}")):
            scenario_name = fp.name[: -len(suffix)]
            data = load_json(fp)
            if not data:
                continue
            scores = data.get("borda_scores") or {}
            if scores:
                min_score = min(scores.values())
                losers = [m for m, s in scores.items() if s == min_score]
                for loser in losers:
                    bottoms.append({
                        "app": app_dir.name,
                        "model": loser,
                        "test_id": scenario_name,
                        "type": _infer_type_from_test_id(scenario_name),
                        "borda_score": float(min_score),
                        "failure_signal": "visual_bottom_listwise",
                    })
            for missing in data.get("missing_models") or []:
                missings.append({
                    "app": app_dir.name,
                    "model": missing,
                    "test_id": scenario_name,
                    "type": _infer_type_from_test_id(scenario_name),
                    "failure_signal": "missing_screenshot",
                })
    return bottoms, missings


def _infer_type_from_test_id(tid: str) -> str:
    if tid.startswith("page_exists"):
        return "page_exists"
    if tid.startswith("tab_nav"):
        return "tab_navigation"
    if tid.startswith("parent_child"):
        return "parent_child_navigation"
    return "unknown"


# -------------- human unrankable (phase 2) --------------

def collect_human_unrankable(human_labels_path: Path, tasks_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    labels = load_json(human_labels_path)
    tasks = load_json(tasks_path)
    if not labels or not tasks:
        return out
    task_index = {t["task_id"]: t for t in tasks.get("tasks", [])}
    for tid, lab in (labels.get("labels") or {}).items():
        unrankable = lab.get("unrankable") or []
        if not unrankable:
            continue
        task = task_index.get(tid, {})
        app = task.get("app")
        scenario = task.get("scenario") or task.get("test_id")
        for cand in unrankable:
            out.append({
                "app": app,
                "model": cand,
                "test_id": scenario,
                "type": _infer_type_from_test_id(scenario or ""),
                "failure_signal": "human_unrankable",
            })
    return out


# -------------- aggregations --------------

def matrix(records: list[dict[str, Any]], row_key: str, col_key: str) -> dict[str, Counter]:
    m: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        m[r.get(row_key, "?")][r.get(col_key, "?")] += 1
    return m


def md_matrix(title: str, mat: dict[str, Counter], col_order: list[str], row_order: list[str] | None = None) -> str:
    if row_order is None:
        row_order = sorted(mat.keys(), key=lambda r: -sum(mat[r].values()))
    lines = [title, ""]
    header = "| Row \\ Col | " + " | ".join(col_order) + " | Total |"
    sep = "|---|" + "---|" * (len(col_order) + 1)
    lines.append(header)
    lines.append(sep)
    col_totals = Counter()
    for r in row_order:
        cells = []
        rsum = 0
        for c in col_order:
            v = mat[r][c]
            rsum += v
            col_totals[c] += v
            cells.append(str(v))
        lines.append(f"| `{r}` | " + " | ".join(cells) + f" | **{rsum}** |")
    grand = sum(col_totals.values())
    lines.append("| **Total** | " + " | ".join(f"**{col_totals[c]}**" for c in col_order) + f" | **{grand}** |")
    lines.append("")
    return "\n".join(lines)


# -------------- screenshot symlink farm --------------

def make_screenshot_links(failures: list[dict[str, Any]], out_dir: Path) -> int:
    """Create symlinks: experiments/failures/failure_screenshots/<app>/<model>/<test_id>__after.png"""
    out_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for r in failures:
        if r.get("failure_signal") != "nav_failure":
            continue
        sa = r.get("screenshot_after")
        if not sa or not Path(sa).exists():
            continue
        dst_dir = out_dir / (r.get("app") or "?") / (r.get("model") or "?")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{r.get('test_id')}__after.png"
        if dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(os.path.abspath(sa), dst)
            created += 1
        except OSError:
            continue
    return created


# -------------- markdown report --------------

TYPE_ORDER = ["page_exists", "tab_navigation", "parent_child_navigation"]


def render_report(
    nav_fails: list[dict[str, Any]],
    visual_bottoms: list[dict[str, Any]],
    missings: list[dict[str, Any]],
    human_unrankable: list[dict[str, Any]],
    n_runs: int,
    n_test_cases_total: int,
    judge_model: str,
    screenshots_created: int,
) -> str:
    models_seen = sorted({r["model"] for r in nav_fails + visual_bottoms + missings + human_unrankable})

    lines: list[str] = []
    lines.append("# Failure Aggregation on MobileForge")
    lines.append("")
    lines.append(f"> Source: `eval/summary.json` × {n_runs} runs, list-wise cache (`{judge_model}` judge), "
                 "and the 55 phase-2 human-labeled tasks.")
    lines.append("")
    lines.append("Three failure signals are aggregated:")
    lines.append("")
    lines.append("- **`nav_failure`** — `navigation_test.details[*].passed = false`. The click model could not "
                 "reach the expected page within the test budget.")
    lines.append("- **`visual_bottom_listwise`** — for every scenario in the list-wise cache, the model whose "
                 "Borda score is the lowest. A given scenario contributes one record per worst model "
                 "(ties counted once each).")
    lines.append("- **`human_unrankable`** — phase-2 reviewers explicitly marked these candidates as "
                 "\"too broken to rank\".")
    lines.append("")
    lines.append("Together they characterise *what* breaks and *for whom*. The taxonomy in §4 then maps these "
                 "rows into qualitative buckets.")
    lines.append("")
    lines.append(f"- Total `nav_failure` records: **{len(nav_fails)}** (out of {n_test_cases_total} executed test cases)")
    lines.append(f"- Total `visual_bottom_listwise` records: **{len(visual_bottoms)}**")
    lines.append(f"- Total `missing_screenshot` records: **{len(missings)}** (model never produced a screenshot for that scenario)")
    lines.append(f"- Total `human_unrankable` records: **{len(human_unrankable)}**")
    lines.append(f"- Failure screenshots linked: **{screenshots_created}** (under `experiments/failures/failure_screenshots/`)")
    lines.append("")

    # ---- nav fails by model and by type ----
    lines.append("## 1. Navigation failures by model × type")
    lines.append("")
    lines.append(md_matrix("**`nav_failure`** counts:", matrix(nav_fails, "model", "type"), TYPE_ORDER))
    lines.append("Reading: which scenario type is each model worst at navigating?")
    lines.append("")

    # ---- nav fails by app ----
    lines.append("## 2. Navigation failures by app (top 10)")
    lines.append("")
    by_app = Counter(r["app"] for r in nav_fails)
    lines.append("| App | # nav failures |")
    lines.append("|---|---:|")
    for app, c in by_app.most_common(10):
        lines.append(f"| `{app}` | {c} |")
    lines.append("")

    # ---- visual bottom by model ----
    lines.append("## 3. Visual bottom-1 frequency (list-wise)")
    lines.append("")
    lines.append("How often does each model land in last place on the list-wise judge?")
    lines.append("")
    lines.append(md_matrix("**`visual_bottom_listwise`** counts:", matrix(visual_bottoms, "model", "type"), TYPE_ORDER))

    # ---- human unrankable ----
    if human_unrankable:
        lines.append("## 4. Human-marked unrankable candidates (phase 2)")
        lines.append("")
        lines.append(md_matrix("**`human_unrankable`** counts:", matrix(human_unrankable, "model", "type"), TYPE_ORDER))

    # ---- missing screenshots ----
    if missings:
        lines.append("## 4b. Missing-screenshot frequency")
        lines.append("")
        lines.append("Cases where the model produced no screenshot at all for a scenario (run failed earlier or the page never rendered). "
                     "These are the strongest \"agent broke before evaluation could even run\" signal.")
        lines.append("")
        lines.append(md_matrix("**`missing_screenshot`** counts:", matrix(missings, "model", "type"), TYPE_ORDER))

    # ---- combined leaderboard ----
    lines.append("## 5. Combined failure leaderboard (per model)")
    lines.append("")
    nav_by_m = Counter(r["model"] for r in nav_fails)
    vis_by_m = Counter(r["model"] for r in visual_bottoms)
    miss_by_m = Counter(r["model"] for r in missings)
    hum_by_m = Counter(r["model"] for r in human_unrankable)
    lines.append("| Model | # nav failures | # visual bottom-1 | # missing screenshot | # human unrankable |")
    lines.append("|---|---:|---:|---:|---:|")
    for m in sorted(models_seen, key=lambda mm: -(nav_by_m[mm] + vis_by_m[mm] + miss_by_m[mm] + hum_by_m[mm])):
        lines.append(f"| `{m}` | {nav_by_m[m]} | {vis_by_m[m]} | {miss_by_m[m]} | {hum_by_m[m]} |")
    lines.append("")

    # ---- recommended taxonomy ----
    lines.append("## 6. Suggested next step: qualitative taxonomy")
    lines.append("")
    lines.append("Use the screenshot links below for a 30-minute manual classification pass. Suggested buckets:")
    lines.append("")
    lines.append("| Bucket | What you'll see in the after-screenshot |")
    lines.append("|---|---|")
    lines.append("| **A. Wrong page rendered** | nav_success=true (or the route looks right) but the page content does not match the reference at all (different layout, different content). |")
    lines.append("| **B. Navigation broken** | Click had no effect or landed on the same page; URL did not change. Most `nav_failure` records fall here. |")
    lines.append("| **C. Missing interactive elements** | Tab bar / button / link is absent so the test could not even attempt the navigation. |")
    lines.append("| **D. Layout severely off** | The right page is shown but layout/spacing is destroyed. Most `visual_bottom_listwise` records fall here. |")
    lines.append("| **E. Content placeholder** | Page is mostly skeleton boxes / lorem ipsum / icons missing — the GPT-5.1 \"骨架\" symptom. |")
    lines.append("| **F. Build OK but runtime crashed** | dist/ exists but the page is blank or shows a JS error overlay. |")
    lines.append("")
    lines.append("Path to start labelling: `experiments/failures/failure_screenshots/<app>/<model>/`.")
    lines.append("")
    return "\n".join(lines)


# -------------- main --------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("experiments/20260428_181902_mobileforge-compare/runs"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("experiments/judge_method_study/listwise_cache/20260428_181902_mobileforge-compare"),
    )
    parser.add_argument(
        "--judge-model",
        default="gemini-2.5-pro",
    )
    parser.add_argument(
        "--phase2-tasks",
        type=Path,
        default=Path("experiments/judge_method_study/phase2/tasks.json"),
    )
    parser.add_argument(
        "--phase2-labels",
        type=Path,
        default=Path("experiments/judge_method_study/phase2/human_labels.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/failures"),
    )
    args = parser.parse_args()

    nav_fails = collect_nav_failures(args.runs_dir)
    n_runs = len([d for d in args.runs_dir.iterdir() if d.is_dir() and parse_run_dir(d.name)])
    visual_bottoms, missings = collect_visual_bottoms(args.cache_dir, args.judge_model)
    human_unrankable = collect_human_unrankable(args.phase2_labels, args.phase2_tasks)

    # total executed test cases (for context)
    n_total = 0
    for d in args.runs_dir.iterdir():
        if not d.is_dir():
            continue
        s = load_json(d / "eval" / "summary.json")
        if not s:
            continue
        nav = (s.get("results") or {}).get("navigation_test") or {}
        n_total += int(nav.get("total_cases") or 0)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_created = make_screenshot_links(nav_fails, args.out_dir / "failure_screenshots")

    md = render_report(nav_fails, visual_bottoms, missings, human_unrankable, n_runs, n_total, args.judge_model, screenshots_created)
    (args.out_dir / "REPORT.md").write_text(md, encoding="utf-8")
    (args.out_dir / "failures.json").write_text(
        json.dumps({
            "nav_failures": nav_fails,
            "visual_bottoms": visual_bottoms,
            "missings": missings,
            "human_unrankable": human_unrankable,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.out_dir / 'REPORT.md'} ({len(nav_fails)} nav failures, "
          f"{len(visual_bottoms)} visual bottoms, {len(missings)} missing screenshots, "
          f"{len(human_unrankable)} human-unrankable). "
          f"Linked {screenshots_created} screenshots.")


if __name__ == "__main__":
    main()
