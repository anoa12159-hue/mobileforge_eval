#!/usr/bin/env python3
"""Extract and analyze context_needs declarations from hook experiment traces."""

import json
import re
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TRACE_FILES = {
    "Opus 4-6": str(PROJECT_ROOT / "output/wxreader-10-hook__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/6c877dfe32b6.trace.json"),
    "Sonnet 4": str(PROJECT_ROOT / "output/wxreader-10-hook__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/449ed45f20ff.trace.json"),
    "GPT-5": str(PROJECT_ROOT / "output/wxreader-10-hook__agentic__gpt-5/.designforge/traces/00_/agentic/3ea0b6afc2c9.trace.json"),
}

CONTEXT_NEEDS_RE = re.compile(r'<context_needs>\s*(\{.*?\})\s*</context_needs>', re.DOTALL)


def classify_iteration(it):
    tool_calls = it.get("tool_calls", []) or []
    tool_names = [tc.get("name", "") for tc in tool_calls]
    files_written = []
    for tc in tool_calls:
        if tc.get("name") == "write_file":
            args = tc.get("arguments", {})
            path = args.get("path", "") if isinstance(args, dict) else ""
            files_written.append(os.path.basename(path))

    if "task_done" in tool_names:
        return "done", files_written
    if "write_file" in tool_names:
        has_types = any("types" in f.lower() for f in files_written)
        has_component = any(f in ["BottomTabBar.tsx", "SearchBar.tsx", "BookCard.tsx",
                                   "ImportModal.tsx", "BottomNavigation.tsx", "BookCard.tsx",
                                   "TabBar.tsx", "Layout.tsx"] for f in files_written)
        has_page = any("Page" in f or f in ["ReadingHome.tsx", "ShelfDefault.tsx",
                                             "ShelfUpdates.tsx", "ShelfImportSheet.tsx",
                                             "FriendsFeed.tsx", "Profile.tsx",
                                             "Notifications.tsx"] for f in files_written)
        has_router = any("router" in f.lower() or f == "App.tsx" for f in files_written)

        parts = []
        if has_types:
            parts.append("types")
        if has_component:
            parts.append("components")
        if has_page:
            parts.append("pages")
        if has_router:
            parts.append("router")
        return "+".join(parts) if parts else "write_unknown", files_written

    if any(n in ["run_command"] for n in tool_names):
        return "verify", files_written
    if any(n in ["str_replace", "batch_str_replace"] for n in tool_names):
        return "fix", files_written
    if any(n in ["list_files", "read_file"] for n in tool_names):
        return "explore", files_written
    return "other", files_written


def extract_needs(assistant_text):
    if not assistant_text:
        return None
    match = CONTEXT_NEEDS_RE.search(assistant_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def analyze_trace(label, filepath):
    with open(filepath) as f:
        data = json.load(f)

    model = data.get("model", "unknown")
    iterations = data.get("iterations", [])
    total_input = data.get("tokens", {}).get("input", 0)
    total_output = data.get("tokens", {}).get("output", 0)

    print(f"\n{'='*100}")
    print(f"  {label} (model: {model}, iters: {len(iterations)}, "
          f"input: {total_input:,}, output: {total_output:,})")
    print(f"{'='*100}")

    header = f"{'Iter':>4} {'Phase':<25} {'InTok':>8} {'OutTok':>8} {'HasDecl':>8} {'DeclPhase':<16} {'Screenshots':<15} {'FilesNeeded':<20} {'NotNeeded'}"
    print(header)
    print("-" * 100)

    declarations = []
    for it in iterations:
        idx = it.get("iteration", 0)
        tokens = it.get("tokens", {})
        in_tok = tokens.get("input", 0)
        out_tok = tokens.get("output", 0)
        phase, files = classify_iteration(it)
        needs = extract_needs(it.get("assistant_text", ""))

        has_decl = "YES" if needs else "no"
        decl_phase = needs.get("phase", "-") if needs else "-"

        if needs:
            screenshots = needs.get("need_screenshots", [])
            if isinstance(screenshots, list):
                if len(screenshots) == 7:
                    ss_str = "ALL_7"
                elif len(screenshots) == 0:
                    ss_str = "none"
                else:
                    ss_str = f"{len(screenshots)} imgs"
            else:
                ss_str = str(screenshots)[:15]

            files_full = needs.get("need_files_full", [])
            files_sum = needs.get("need_files_summary", [])
            if files_full:
                fn_str = f"full:{len(files_full)}"
            elif files_sum:
                fn_str = f"sum:{len(files_sum)}"
            else:
                fn_str = "none"

            not_needed = needs.get("not_needed", [])
            nn_str = ", ".join(not_needed)[:30] if not_needed else "-"

            declarations.append({
                "iter": idx, "phase": phase, "decl_phase": decl_phase,
                "screenshots": ss_str, "needs": needs
            })
        else:
            ss_str = "-"
            fn_str = "-"
            nn_str = "-"

        files_str = ", ".join(files[:3])
        if len(files) > 3:
            files_str += f"... (+{len(files)-3})"

        print(f"{idx:>4} {phase:<25} {in_tok:>8,} {out_tok:>8,} {has_decl:>8} {decl_phase:<16} {ss_str:<15} {fn_str:<20} {nn_str}")

    print()
    print(f"  Declarations found: {len(declarations)} / {len(iterations)} iterations")
    if declarations:
        print(f"  Declaration details:")
        for d in declarations:
            print(f"    iter {d['iter']}: phase={d['decl_phase']}, screenshots={d['screenshots']}")
            needs = d['needs']
            if needs.get('need_files_full'):
                print(f"      need_files_full: {[os.path.basename(f) for f in needs['need_files_full']]}")
            if needs.get('need_files_summary'):
                print(f"      need_files_summary: {needs['need_files_summary']}")
            if needs.get('need_recent_pages', 0) > 0:
                print(f"      need_recent_pages: {needs['need_recent_pages']}")
            if needs.get('not_needed'):
                print(f"      not_needed: {needs['not_needed']}")

    return {
        "label": label,
        "model": model,
        "total_iters": len(iterations),
        "total_input": total_input,
        "total_output": total_output,
        "declarations": declarations,
        "all_iters": [(it.get("iteration"), classify_iteration(it)[0],
                       it.get("tokens", {}).get("input", 0),
                       it.get("tokens", {}).get("output", 0)) for it in iterations]
    }


def print_comparison(results):
    print(f"\n{'='*100}")
    print("  CROSS-MODEL COMPARISON")
    print(f"{'='*100}")

    print("\n--- Execution Pattern ---")
    for r in results:
        phases = [f"iter{i[0]}:{i[1]}" for i in r["all_iters"]]
        print(f"  {r['label']:>12}: {' → '.join(phases)}")

    print("\n--- Declaration Coverage ---")
    for r in results:
        n_decl = len(r["declarations"])
        n_total = r["total_iters"]
        pct = n_decl / n_total * 100 if n_total > 0 else 0
        print(f"  {r['label']:>12}: {n_decl}/{n_total} ({pct:.0f}%) iterations had declarations")

    print("\n--- Screenshot Needs by Phase ---")
    phase_needs = {}
    for r in results:
        for d in r["declarations"]:
            phase = d["decl_phase"]
            ss = d["screenshots"]
            key = f"{r['label']}:{phase}"
            phase_needs[key] = ss
    for k, v in sorted(phase_needs.items()):
        print(f"  {k:>30}: {v}")

    print("\n--- Efficiency Comparison ---")
    header = f"  {'Model':>12} {'Iters':>6} {'InputTok':>10} {'OutputTok':>10} {'In/Iter':>10} {'Out/Iter':>10}"
    print(header)
    for r in results:
        avg_in = r["total_input"] // r["total_iters"]
        avg_out = r["total_output"] // r["total_iters"]
        print(f"  {r['label']:>12} {r['total_iters']:>6} {r['total_input']:>10,} {r['total_output']:>10,} {avg_in:>10,} {avg_out:>10,}")


def main():
    results = []
    for label, filepath in TRACE_FILES.items():
        if not os.path.exists(filepath):
            print(f"WARNING: {filepath} not found")
            continue
        result = analyze_trace(label, filepath)
        results.append(result)

    print_comparison(results)


if __name__ == "__main__":
    main()
