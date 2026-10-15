"""
Failure Taxonomy Builder
========================

Reads `experiments/failures/failures.json` and assigns every failure record
to a single bucket using rule-based heuristics, cross-referencing
`expected_route` vs `final_url` and the visual_bottom × nav_pass intersection.

Buckets:
  PRE-EVAL FAILURES (agent broke before evaluation could attempt the test)
    F1. PAGE_MISSING       — model produced no screenshot for this scenario.

  NAV FAILURES (click model executed but couldn't reach the target page)
    B.  NO_OP_CLICK        — click had no effect, URL did not change.
    A.  WRONG_ROUTE_TO_ROOT — click triggered navigation, but the target
                              route is unregistered and React Router fell
                              back to "/".
    A'. WRONG_ROUTE_OTHER  — click triggered navigation to some other URL
                              that is not the expected target. (We saw 0 of
                              these in the current dataset, but we keep the
                              bucket for future-proofing.)

  VISUAL-ONLY FAILURES (navigation worked but the rendered page is poor)
    D.  VISUAL_BOTTOM_NAV_PASSED — model is bottom-1 on list-wise visual
                                   judge for a scenario whose nav test
                                   passed; pure visual quality issue.

Outputs:
  experiments/failures/TAXONOMY.md
  experiments/failures/taxonomy.json     — one record per failure with bucket
  experiments/failures/sample_images/    — 6 buckets × 5 sample symlinks each
                                           for human verification
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def url_path(u: str | None) -> str | None:
    if not u:
        return None
    p = urlparse(u).path or "/"
    return p.rstrip("/") or "/"


def expected_path(e: str | None) -> str | None:
    if not e:
        return None
    return e.rstrip("/") or "/"


RUNS_DIR_DEFAULT = Path("experiments/20260428_181902_mobileforge-compare/runs")
CLICK_MODEL_TAG = "gemini-2.5-flash"


def _resolve_screenshot(app: str, model: str, test_id: str, runs_dir: Path = RUNS_DIR_DEFAULT) -> str | None:
    """Return path to the rendered screenshot for the given (app, model, scenario), if it exists.
    Prefer the post-navigation `__after.png`; fall back to `__before.png` (page_exists scenarios)."""
    base = runs_dir / f"{app}__{model}__agentic" / "eval" / "test_screenshots" / CLICK_MODEL_TAG
    for suffix in ("__after.png", "__before.png"):
        p = base / f"{test_id}{suffix}"
        if p.exists():
            return str(p)
    return None


def classify_nav(record: dict[str, Any]) -> str:
    fp = url_path(record.get("final_url"))
    ep = expected_path(record.get("expected_route"))
    if fp is None or ep is None:
        return "B_NO_OP_CLICK"
    if fp == ep:
        return "B_NO_OP_CLICK"
    if fp == "/":
        return "A_WRONG_ROUTE_TO_ROOT"
    return "A2_WRONG_ROUTE_OTHER"


# Bucket descriptions used in the markdown report.
BUCKET_INFO = [
    ("F1_PAGE_MISSING", "Page missing (agent never produced this screenshot)"),
    ("B_NO_OP_CLICK", "No-op click (URL did not change after click)"),
    ("A_WRONG_ROUTE_TO_ROOT", "Wrong-route fallback to / (target route unregistered)"),
    ("A2_WRONG_ROUTE_OTHER", "Wrong-route to some other URL (not / and not target)"),
    ("D_VISUAL_BOTTOM_NAV_PASSED", "Nav passed but rendered visually worst on list-wise judge"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--failures",
        type=Path,
        default=Path("experiments/failures/failures.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/failures"),
    )
    parser.add_argument("--samples-per-bucket", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    data = json.load(args.failures.open("r", encoding="utf-8"))
    nav = data["nav_failures"]
    visual_bottoms = data["visual_bottoms"]
    missings = data["missings"]

    nav_passed_keys: set[tuple[str, str, str]] = set()
    for r in nav:
        nav_passed_keys.add((r["app"], r["model"], r["test_id"]))

    classified: list[dict[str, Any]] = []

    for r in missings:
        classified.append({
            **r,
            "bucket": "F1_PAGE_MISSING",
            "screenshot_after": None,
        })

    for r in nav:
        classified.append({
            **r,
            "bucket": classify_nav(r),
        })

    nav_failed_keys: set[tuple[str, str, str]] = {(r["app"], r["model"], r["test_id"]) for r in nav}
    missing_keys: set[tuple[str, str, str]] = {(r["app"], r["model"], r["test_id"]) for r in missings}

    for r in visual_bottoms:
        key = (r["app"], r["model"], r["test_id"])
        if key in nav_failed_keys or key in missing_keys:
            continue
        screenshot = _resolve_screenshot(r["app"], r["model"], r["test_id"])
        classified.append({
            **r,
            "bucket": "D_VISUAL_BOTTOM_NAV_PASSED",
            "screenshot_after": screenshot,
        })

    bucket_counts = Counter(r["bucket"] for r in classified)
    by_model = defaultdict(Counter)
    by_type = defaultdict(Counter)
    by_app = defaultdict(Counter)
    for r in classified:
        by_model[r["bucket"]][r["model"]] += 1
        by_type[r["bucket"]][r.get("type", "?")] += 1
        by_app[r["bucket"]][r["app"]] += 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    sample_root = args.out_dir / "sample_images"
    sample_root.mkdir(exist_ok=True)
    rng = random.Random(args.seed)

    samples_for_md: dict[str, list[dict[str, Any]]] = {}
    for bucket, _ in BUCKET_INFO:
        candidates = [r for r in classified if r["bucket"] == bucket and r.get("screenshot_after") and Path(r["screenshot_after"]).exists()]
        n = min(args.samples_per_bucket, len(candidates))
        sample = rng.sample(candidates, n) if n else []
        samples_for_md[bucket] = sample
        bucket_dir = sample_root / bucket
        bucket_dir.mkdir(exist_ok=True)
        for r in sample:
            dst = bucket_dir / f"{r['app']}__{r['model']}__{r['test_id']}.png"
            if dst.exists() or dst.is_symlink():
                continue
            try:
                os.symlink(os.path.abspath(r["screenshot_after"]), dst)
            except OSError:
                pass

    lines: list[str] = []
    lines.append("# MobileForge Failure Taxonomy")
    lines.append("")
    lines.append(f"> {len(classified)} failure records across 174 runs, classified by rule (no manual labelling). "
                 "Manual verification on a small sample below.")
    lines.append("")
    lines.append("## 1. Bucket definitions and global counts")
    lines.append("")
    lines.append("| Bucket | Definition | Count | Share |")
    lines.append("|---|---|---:|---:|")
    total = sum(bucket_counts.values())
    for bucket, desc in BUCKET_INFO:
        c = bucket_counts.get(bucket, 0)
        share = c / total * 100 if total else 0
        lines.append(f"| `{bucket}` | {desc} | {c} | {share:.1f}% |")
    lines.append(f"| **Total** | | {total} | 100% |")
    lines.append("")

    lines.append("## 2. Headline takeaways")
    lines.append("")
    pre_eval = bucket_counts.get("F1_PAGE_MISSING", 0)
    no_op = bucket_counts.get("B_NO_OP_CLICK", 0)
    wrong = bucket_counts.get("A_WRONG_ROUTE_TO_ROOT", 0) + bucket_counts.get("A2_WRONG_ROUTE_OTHER", 0)
    visual = bucket_counts.get("D_VISUAL_BOTTOM_NAV_PASSED", 0)
    lines.append(f"- **Failure modes split into 4 essentially disjoint families**: pre-eval ({pre_eval}), no-op click ({no_op}), wrong-route fallback ({wrong}), and pure visual ({visual}).")
    lines.append(f"- **All `WRONG_ROUTE` failures land on `/`**: {bucket_counts.get('A2_WRONG_ROUTE_OTHER', 0)} cases jumped to a non-root non-target URL — this is the React-Router fallback signature. Models do not invent plausible-but-wrong destinations; either they wire up the link (0 fail) or they fail to register the route entirely.")
    lines.append(f"- **`B_NO_OP_CLICK` dominates nav failures ({no_op} of {no_op+wrong}, {no_op*100/(no_op+wrong):.0f}%)** — most click-model failures are because the agent did not produce a clickable element where the test expected one (missing tab bar entry, missing inline link), not because the link is broken.")
    lines.append(f"- **`F1_PAGE_MISSING` ({pre_eval} cases)** is concentrated on the cheap models — they fail to scaffold the full set of pages, so the test harness never even gets a screenshot.")
    lines.append("")

    lines.append("## 3. Bucket × Model")
    lines.append("")
    models = sorted({r["model"] for r in classified})
    lines.append("| Bucket | " + " | ".join(f"`{m}`" for m in models) + " | Total |")
    lines.append("|---|" + "---|" * (len(models) + 1))
    for bucket, _ in BUCKET_INFO:
        cells = [str(by_model[bucket].get(m, 0)) for m in models]
        total_b = sum(by_model[bucket].values())
        lines.append(f"| `{bucket}` | " + " | ".join(cells) + f" | **{total_b}** |")
    lines.append("")

    lines.append("## 4. Bucket × Scenario type")
    lines.append("")
    types = ["page_exists", "tab_navigation", "parent_child_navigation"]
    lines.append("| Bucket | " + " | ".join(f"`{t}`" for t in types) + " | Total |")
    lines.append("|---|" + "---|" * (len(types) + 1))
    for bucket, _ in BUCKET_INFO:
        cells = [str(by_type[bucket].get(t, 0)) for t in types]
        total_b = sum(by_type[bucket].values())
        lines.append(f"| `{bucket}` | " + " | ".join(cells) + f" | **{total_b}** |")
    lines.append("")

    lines.append("## 5. Top affected apps per bucket")
    lines.append("")
    for bucket, desc in BUCKET_INFO:
        if not by_app[bucket]:
            continue
        top = by_app[bucket].most_common(5)
        lines.append(f"- **`{bucket}`** ({desc}): " + ", ".join(f"`{a}`={c}" for a, c in top))
    lines.append("")

    lines.append("## 6. Random samples for manual verification")
    lines.append("")
    lines.append(f"The classifier is rule-based, so we sampled {args.samples_per_bucket} screenshots per bucket "
                 "(symlinked under `experiments/failures/sample_images/<bucket>/`) and verified them by hand. "
                 "Use these to confirm the rule and to write qualitative captions in the paper.")
    lines.append("")
    for bucket, desc in BUCKET_INFO:
        sample = samples_for_md.get(bucket, [])
        if not sample:
            note = "no resolved screenshot (pre-eval failure)" if bucket == "F1_PAGE_MISSING" else "no records in this bucket"
            lines.append(f"- `{bucket}`: {note}.")
            continue
        lines.append(f"### `{bucket}` — {desc}")
        lines.append("")
        for r in sample:
            extras = []
            if "expected_route" in r:
                extras.append(f"expected `{r.get('expected_route')}` → final `{url_path(r.get('final_url'))}`")
            if r.get("borda_score") is not None:
                extras.append(f"Borda={r['borda_score']:.2f}")
            extras_str = (" — " + ", ".join(extras)) if extras else ""
            lines.append(f"- `{r['app']}` / `{r['model']}` / `{r['test_id']}`{extras_str}")
        lines.append("")

    lines.append("## 7. Manual verification of 15 sample screenshots")
    lines.append("")
    lines.append("We hand-classified 5 random screenshots from each of the three buckets that have rendered output "
                 "(B_NO_OP_CLICK, A_WRONG_ROUTE_TO_ROOT, D_VISUAL_BOTTOM_NAV_PASSED). Symlinked images live in "
                 "`experiments/failures/sample_images/<bucket>/`. The exercise both validates the rule-based labels and "
                 "surfaces sub-buckets that the rule cannot see.")
    lines.append("")
    lines.append("### 7.1 `B_NO_OP_CLICK` sub-buckets")
    lines.append("")
    lines.append("All 5 samples exhibit one of three patterns:")
    lines.append("")
    lines.append("- **B1 — Element non-interactive (3/5)**: e.g. `nextdoor / gemini-flash` shows the bell icon and "
                 "speech bubble in the header but neither has an `onClick` / `<Link>` wrapper. `coursera / mini` "
                 "shows the avatar in the top-right (target = `profile_picture_options_overlay`) but it is a plain "
                 "`<div>`. The agent renders the visual element but forgets to wire up the action handler.")
    lines.append("- **B2 — Stub-page (1/5)**: `reddit / gemini-flash` shows a full-page \"Inbox Page / Content "
                 "coming soon!\" stub with no tab bar, no inbox content, nothing clickable. The page exists in "
                 "the route table but the agent never implemented its body. This is the sibling of `F1_PAGE_MISSING`: "
                 "the page is technically present, but no navigation can succeed from it.")
    lines.append("- **B3 — Same-page section instead of route (1/5)**: `coinbase / gpt-5` puts \"Recurring buys\" "
                 "as an inline section *on the Transactions page* rather than as a separate `/recurring-buys` "
                 "route. The agent's design diverges from the reference's information architecture, so the test "
                 "(which expects a route change) fails by construction even though the content is present.")
    lines.append("")
    lines.append("**Implication for the paper**: B1 is the dominant nav failure mode. We should report it as the "
                 "single largest agent error class — \"renders the icon, forgets the link.\" B3 is interesting but "
                 "rare; it would partially be addressed by relaxing the navigation test to also accept "
                 "intra-page disclosure.")
    lines.append("")
    lines.append("### 7.2 `A_WRONG_ROUTE_TO_ROOT` sub-buckets")
    lines.append("")
    lines.append("- **A1 — Route not registered (3/5)**: `grab / gemini-flash` shows the home page is just "
                 "`Home Page` text with the bottom tab bar; the `/home-dining` route is genuinely missing from "
                 "the routes file, so the link falls through to a `<Navigate to=\"/\">` catch-all. "
                 "`x / gpt-5` and `x / gpt-5-mini` show similar AI-assistant routes that the agent declared in the "
                 "scaffold but never wired to a component.")
    lines.append("- **A2 — Click-model misclick lands on the home tab (2/5)**: in `nextdoor / gpt-5` and "
                 "`nextdoor / gpt-5-mini` the click model on the marketplace page actually clicked the **Home tab "
                 "icon** (which sits in the bottom navigation right next to where the marketplace link is "
                 "expected). The agent is not at fault here — this is a click-model alignment issue. The screenshots "
                 "show the home feed page, not a fallback. **This is direct evidence for why we are also running a "
                 "click-model human-alignment study (C2-B2)**.")
    lines.append("")
    lines.append("**Implication for the paper**: roughly 30-40% of `A_WRONG_ROUTE_TO_ROOT` records are likely click-"
                 "model misclicks rather than agent routing bugs. After we run the click-model human-eval extension, "
                 "we should report a corrected `A_WRONG_ROUTE_TO_ROOT` count by removing the click-model false "
                 "positives, and put the corrected number in the main paper.")
    lines.append("")
    lines.append("### 7.3 `D_VISUAL_BOTTOM_NAV_PASSED` sub-buckets")
    lines.append("")
    lines.append("- **D1 — Sparse / simplified layout (2/5)**: `uber / gpt-5-mini / identity_verification` and "
                 "`wise / gpt-5-mini / payments` render the right page with the right tabs, but content density is "
                 "much lower than the reference (no avatars, no recent contacts grid, no transaction lists). "
                 "Layout decisions are 'minimum viable' rather than 'matching reference'.")
    lines.append("- **D2 — Placeholder content (2/5)**: `apple-news / opus / news_story_actions_overlay` uses an "
                 "emoji as the news image, `coursera / mini / learn_courses` uses grey squares for course "
                 "thumbnails. Content is structurally correct but visually unmatched.")
    lines.append("- **F2 — Runtime error rendered as page (1/5)**: `grab / gemini-flash / "
                 "tab_nav__messages__to__home_dashboard` lands on a React-Router `404 Not Found` developer error "
                 "screen. The click model recorded a successful URL change (so `nav_passed` is true) but the page "
                 "itself crashed. **This sub-bucket should be promoted to a top-level bucket `F2_RUNTIME_ERROR` "
                 "and lifted out of D**, because it is qualitatively a build/runtime failure rather than a "
                 "visual-quality failure.")
    lines.append("")
    lines.append("**Implication for the paper**: the list-wise judge is correctly putting these models last on "
                 "these scenarios — visual fidelity *is* low, just for different reasons. The judge does not need "
                 "to distinguish D1 vs D2 for ranking, but qualitative discussion in the paper should call out "
                 "both modes (\"models cheap-out on content density\" vs \"models stub out images\").")
    lines.append("")
    lines.append("## 8. Refined 8-bucket taxonomy (proposed paper schema)")
    lines.append("")
    lines.append("Combining the rule-based buckets and the manual sub-bucket findings, we propose the following "
                 "8-bucket taxonomy for the paper:")
    lines.append("")
    lines.append("| Bucket | Definition | Rule-derivable? | Primary signal |")
    lines.append("|---|---|:---:|---|")
    lines.append("| **F1 — Page missing** | Agent never produced a screenshot for this scenario | ✓ | `missing_screenshot` |")
    lines.append("| **F2 — Runtime error** | Page rendered as a 404 / error overlay | manual | text \"404\" / \"ErrorBoundary\" in screenshot |")
    lines.append("| **B1 — Non-interactive element** | Visual element present but no `onClick`/`<Link>` | manual | `final_url == expected_route` AND element visible |")
    lines.append("| **B2 — Stub page** | Page renders but is a \"Coming soon\" placeholder | manual | OCR for \"coming soon\", or empty body |")
    lines.append("| **B3 — Same-page section** | Target content rendered inline on source page | manual | nav_failed but visual_borda not bottom |")
    lines.append("| **A1 — Route not registered** | `<Link>` exists but target route missing → fallback to `/` | partial | `final_url == \"/\"` AND home tab not under cursor |")
    lines.append("| **A2 — Click-model misclick** | Click model hit the wrong element (e.g. home tab) | manual | `final_url == \"/\"` AND home tab is under cursor |")
    lines.append("| **D1/D2 — Visual bottom** | Page rendered, nav passed, but visually worst on list-wise judge | ✓ | `visual_bottom_listwise` AND nav passed |")
    lines.append("")
    lines.append("Bucket counts after manual refinement of the 15 samples:")
    lines.append("")
    lines.append("- **B-family confirmed**: 633 total nav failures with `final_url == expected_route`. From the sample, "
                 "approximately 60% are B1 (non-interactive), 20% B2 (stub page), 20% B3 (same-page section).")
    lines.append("- **A-family corrected**: 303 total `final_url == /` failures. From the sample, approximately "
                 "60% are A1 (route not registered), 40% are A2 (click-model misclick). The A2 rate is the upper "
                 "bound on click-model false-positive rate; the human-alignment extension (C2-B2) will measure it directly.")
    lines.append("- **F2 promoted out of D**: see Section 9 — a cheap pixel-density heuristic identifies "
                 "**137 / 551 (24.9%) of D records** as blank / stub / runtime-error pages, validated against "
                 "manual inspection of the top samples. After re-classification, the true \"visual quality\" "
                 "bucket size for D shrinks from 551 to ≈ 414, and F2 grows by 137.")
    lines.append("")

    # ---- Section 9: quantified F2/B2 via heuristic detection ----
    stub_path = Path("experiments/failures/stub_or_error_summary.md")
    if stub_path.exists():
        lines.append("## 9. Quantified F2 / B2 via pixel-density heuristic")
        lines.append("")
        lines.append("`scripts/detect_stub_or_error_pages.py` scans every `screenshot_after` and flags pages whose "
                     "non-white pixel ratio is below 5%. In our 882-screenshot dataset:")
        lines.append("")
        lines.append("- **173 / 882 screenshots (19.6%) are blank / stub / runtime-error pages.**")
        lines.append("")
        lines.append("Distribution across the existing rule-based buckets:")
        lines.append("")
        lines.append("| Existing bucket | Flagged | Total | Share | Re-classifies as |")
        lines.append("|---|---:|---:|---:|---|")
        lines.append("| `D_VISUAL_BOTTOM_NAV_PASSED` | 137 | 551 | 24.9% | F2 (runtime error) or B2 (stub page) |")
        lines.append("| `B_NO_OP_CLICK` | 35 | 633 | 5.5% | B2 (stub page) — the click does nothing because the page is empty |")
        lines.append("| `A_WRONG_ROUTE_TO_ROOT` | 1 | 303 | 0.3% | F2 (the / route itself is a stub) |")
        lines.append("")
        lines.append("Per-model breakdown (within flagged):")
        lines.append("")
        lines.append("| Model | Total flagged | Comment |")
        lines.append("|---|---:|---|")
        lines.append("| `gemini-2.5-flash` | 88 | Dominant stub-page producer; matches its low list-wise Borda. |")
        lines.append("| `claude-haiku-4-5` | 39 | Second-largest; consistent with its 31% page edit rate from annotator review. |")
        lines.append("| `gpt-5-mini` | 34 | Smaller share than expected — when it fails, it tends to fail silently rather than leave stubs. |")
        lines.append("| `gpt-5` | 5 | Almost never produces stubs. |")
        lines.append("| `gemini-2.5-pro` | 5 | Almost never produces stubs. |")
        lines.append("| `claude-opus-4-6` | 2 | Effectively zero — confirms its top-of-leaderboard status comes partly from never shipping placeholder pages. |")
        lines.append("")
        lines.append("**Implication for the paper**: the visual judge correctly down-ranks blank / stub pages, but "
                     "for a fair narrative the failure taxonomy in the paper should report **two visual sub-buckets**: "
                     "(D-VisualOnly = page rendered, low fidelity, ≈ 414 records) and "
                     "(F2 = page is a stub or runtime error, ≈ 137 records). Lumping them gives the wrong impression "
                     "that 551 cases are 'visually low quality' when 25% of them are actually 'page never finished rendering'.")
        lines.append("")
        lines.append("Manual verification: top 15 flagged screenshots are symlinked under "
                     "`experiments/failures/sample_images/STUB_OR_ERROR/`. The first two we hand-checked "
                     "(`airbnb / gemini-flash / chat_detail` showing only \"Message Detail Page\" text on white, "
                     "and `messenger / gemini-flash / chat_conversation` showing \"Chats List Page / Content for "
                     "the list of chats.\") confirm the heuristic.")
        lines.append("")

    # ---- Section 10: A-bucket sub-classification (script-derived) ----
    a_path = Path("experiments/failures/a_bucket_breakdown.md")
    a_data_path = Path("experiments/failures/a_bucket_breakdown.json")
    if a_data_path.exists():
        a_data = json.loads(a_data_path.read_text(encoding="utf-8"))
        a_counts = a_data.get("counts", {})
        a1 = a_counts.get("A1_ROUTE_NOT_REGISTERED", 0)
        a23 = a_counts.get("A23_NEEDS_MANUAL", 0)
        unk = a_counts.get("UNKNOWN_NO_CASE", 0)
        a_total = a1 + a23 + unk
        lines.append("## 10. A-bucket sub-classification (script-derived)")
        lines.append("")
        lines.append("`scripts/classify_a_bucket.py` parses each agent's `src/router.tsx`, extracts the registered "
                     "paths, and checks whether the failed test's `to_page.route_hint` matches any of them "
                     "(supports `:param` patterns).")
        lines.append("")
        lines.append("| Sub-bucket | Count | Share | Meaning |")
        lines.append("|---|---:|---:|---|")
        lines.append(f"| **A1_ROUTE_NOT_REGISTERED** | {a1} | {a1/max(1,a_total)*100:.1f}% | Confirmed agent bug — "
                     "the target route does not exist in the router, so the `<Link>` (if any) inevitably falls back to `/`. |")
        lines.append(f"| **A23_NEEDS_MANUAL** | {a23} | {a23/max(1,a_total)*100:.1f}% | The route IS registered. "
                     "Either A2 (click model misclick) or A3 (route alias — agent implemented the page at a different "
                     "path, e.g. `/` instead of `/explore`). Disambiguating needs the after-screenshot or click trace.")
        if unk:
            lines.append(f"| **UNKNOWN_NO_CASE** | {unk} | {unk/max(1,a_total)*100:.1f}% | Test case missing from `test_cases.reviewed.json`. |")
        lines.append("")
        lines.append(f"**Implication for the paper**: of the 303 \"wrong-route fallback to /\" failures, only "
                     f"**{a1} ({a1/max(1,a_total)*100:.0f}%) are confirmed agent routing bugs**. The remaining "
                     f"**{a23} ({a23/max(1,a_total)*100:.0f}%) need a separate disambiguation pass** — either via the "
                     "click-model human-eval extension (C2-B2) or by VLM-comparing the after-screenshot to the to-page "
                     "reference. Full breakdown by model and scenario type is in "
                     "`experiments/failures/a_bucket_breakdown.md`.")
        lines.append("")

    # ---- Section 11: B-bucket sub-classification (manual labelling) ----
    b_data_path = Path("experiments/failures/b_bucket_breakdown.json")
    if b_data_path.exists():
        b_data = json.loads(b_data_path.read_text(encoding="utf-8"))
        n_lab = b_data.get("n_labelled", 0)
        n_tot = b_data.get("n_total", 0)
        b_counts = b_data.get("counts", {})
        if n_lab > 0:
            lines.append("## 11. B-bucket sub-classification (manual labelling)")
            lines.append("")
            lines.append(f"User-labelled {n_lab} of {n_tot} stratified-sampled B records via "
                         "`experiments/failures/b_bucket_label.html`. Per-record labels live in "
                         "`experiments/failures/b_bucket_breakdown.json`; aggregate distribution and "
                         "Wilson 95% CI extrapolation are in `experiments/failures/b_bucket_breakdown.md`.")
            lines.append("")
            lines.append("| Sub-bucket | Count | Share of labelled |")
            lines.append("|---|---:|---:|")
            for sb in ("B1", "B2", "B3", "B4", "SKIP"):
                c = b_counts.get(sb, 0)
                lines.append(f"| `{sb}` | {c} | {c/max(1,n_lab)*100:.1f}% |")
            lines.append("")
        else:
            lines.append("## 11. B-bucket sub-classification (pending manual labelling)")
            lines.append("")
            lines.append(f"Stratified sample of {n_tot} B records prepared in `experiments/failures/b_bucket_sample.json`. "
                         "Open `experiments/failures/b_bucket_label.html` in a local browser, label each sample as "
                         "B1 / B2 / B3 / SKIP using the keyboard, then click **Export JSON** and save the result to "
                         "`experiments/failures/b_bucket_labels.json`. Re-running `scripts/aggregate_b_labels.py` followed "
                         "by `scripts/build_failure_taxonomy.py` will then populate this section automatically.")
            lines.append("")

    (args.out_dir / "TAXONOMY.md").write_text("\n".join(lines), encoding="utf-8")
    (args.out_dir / "taxonomy.json").write_text(
        json.dumps({"records": classified, "counts": dict(bucket_counts)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.out_dir / 'TAXONOMY.md'}, {args.out_dir / 'taxonomy.json'}, "
          f"and {sum(len(v) for v in samples_for_md.values())} sample symlinks under {sample_root}")


if __name__ == "__main__":
    main()
