#!/usr/bin/env python3
"""Analyze trace files to extract per-phase (stage) data for multi-stage agent workflows."""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TRACE_FILES = {
    "Opus v0": str(PROJECT_ROOT / "output/wxreader-7__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/44e2457b2445.trace.json"),
    "Opus PACF": str(PROJECT_ROOT / "output/wxreader-10__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/a52e32a5e136.trace.json"),
    "GPT-5 v0": str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5/.designforge/traces/00_/agentic/4ebe69ba5fb2.trace.json"),
    "GPT-5.1 v0": str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5.1/.designforge/traces/00_/agentic/98326931a205.trace.json"),
    "Sonnet v0": str(PROJECT_ROOT / "output/wxreader-8__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/2c376c4bbbea.trace.json"),
    "Sonnet PACF": str(PROJECT_ROOT / "output/wxreader-9__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/7a33123c0ddc.trace.json"),
}

STAGE_NAMES = {
    "S0": "Explore",
    "S1": "Analyze",
    "S2": "Types",
    "S3": "Components",
    "S4": "Pages",
    "S5": "Router+App",
    "S6": "Verify+Fix",
    "S7": "Done",
}

SHARED_COMPONENT_NAMES = {
    "tabbar", "searchar", "bookcard", "statusbar", "bottomnav", "bottomtabbar",
    "headersearch", "sectiontitle", "topstatusbar", "navbar", "navigationbar",
    "tab", "tabs", "icon", "icons", "badge", "avatar", "header", "footer",
    "layout", "bottomtab", "tabnavigation", "importmodal", "importdialog",
}


def extract_path_from_args(args):
    """Extract the file path from tool call arguments."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return args
    if isinstance(args, dict):
        return args.get("path", args.get("file_path", ""))
    return ""


def get_basename_lower(path: str) -> str:
    return os.path.basename(path).lower().replace(".tsx", "").replace(".ts", "")


def classify_file(path: str) -> str:
    """Classify a written file into a stage category."""
    p = path.lower()
    basename = get_basename_lower(path)

    if p.endswith("types.ts"):
        return "S2"
    if p.endswith("router.tsx") or basename == "app":
        return "S5"
    if "/components/" in p or basename in SHARED_COMPONENT_NAMES:
        return "S3"
    if "/pages/" in p:
        return "S4"
    return "unknown"


def classify_iteration(iteration: dict) -> tuple[str, list[str]]:
    """Classify an iteration into a stage. Returns (stage, files_written)."""
    tool_calls = iteration.get("tool_calls", []) or []
    assistant_text = iteration.get("assistant_text", "") or ""

    tool_names = set()
    files_written = []
    files_read = []
    str_replace_files = []
    file_stages = []

    for tc in tool_calls:
        name = tc.get("name", "")
        tool_names.add(name)
        args = tc.get("arguments", {})

        if name == "write_file":
            path = extract_path_from_args(args)
            if path:
                files_written.append(path)
                file_stages.append(classify_file(path))
        elif name in ("read_file",):
            path = extract_path_from_args(args)
            if path:
                files_read.append(path)
        elif name in ("str_replace", "batch_str_replace"):
            path = extract_path_from_args(args)
            if path:
                str_replace_files.append(path)
            if name == "batch_str_replace":
                replacements = args.get("replacements", []) if isinstance(args, dict) else []
                for r in replacements:
                    rp = r.get("path", "")
                    if rp:
                        str_replace_files.append(rp)

    if "task_done" in tool_names:
        return "S7", files_written

    has_write = "write_file" in tool_names
    has_read = "read_file" in tool_names or "list_files" in tool_names
    has_run = "run_command" in tool_names
    has_str_replace = "str_replace" in tool_names or "batch_str_replace" in tool_names

    if has_write:
        unique_stages = set(s for s in file_stages if s != "unknown")
        if len(unique_stages) == 1:
            return unique_stages.pop(), files_written
        elif len(unique_stages) > 1:
            stage_order = ["S2", "S3", "S4", "S5"]
            highest = "S2"
            for s in stage_order:
                if s in unique_stages:
                    highest = s
            label = "+".join(sorted(unique_stages, key=lambda x: stage_order.index(x) if x in stage_order else 99))
            return label, files_written
        else:
            return "S4", files_written

    if has_run or has_str_replace:
        return "S6", files_written

    if has_read and not has_write:
        return "S0", files_written

    if not tool_calls or (len(tool_calls) == 0):
        return "S1", files_written

    if tool_names <= {"list_files", "read_file"}:
        return "S0", files_written

    return "S1", files_written


def analyze_trace(label: str, filepath: str) -> dict:
    """Analyze a single trace file and return per-stage data."""
    with open(filepath, "r") as f:
        data = json.load(f)

    iterations = data.get("iterations", [])
    model = data.get("model", "unknown")
    total_iters = data.get("total_iterations", len(iterations))

    stages = defaultdict(lambda: {
        "iterations": [],
        "input_tokens": [],
        "output_tokens": [],
        "files_written": [],
        "elapsed_sec": [],
    })

    for it in iterations:
        idx = it.get("iteration", 0)
        tokens = it.get("tokens", {})
        input_tok = tokens.get("input", 0)
        output_tok = tokens.get("output", 0)
        elapsed = it.get("elapsed_sec", 0)

        stage, files = classify_iteration(it)

        stages[stage]["iterations"].append(idx)
        stages[stage]["input_tokens"].append(input_tok)
        stages[stage]["output_tokens"].append(output_tok)
        stages[stage]["files_written"].extend(files)
        stages[stage]["elapsed_sec"].append(elapsed)

    return {
        "label": label,
        "model": model,
        "total_iterations": total_iters,
        "total_input": data.get("tokens", {}).get("input", 0),
        "total_output": data.get("tokens", {}).get("output", 0),
        "total_elapsed": data.get("total_elapsed_sec", 0),
        "stages": dict(stages),
    }


def shorten_path(path: str) -> str:
    """Shorten a file path to just src/... portion."""
    if "/src/" in path:
        return "src/" + path.split("/src/", 1)[1]
    return os.path.basename(path)


def print_trace_table(result: dict):
    """Print a detailed per-stage table for one trace."""
    label = result["label"]
    model = result["model"]
    stages = result["stages"]

    print(f"\n{'='*120}")
    print(f"  {label}  (model: {model}, total_iters: {result['total_iterations']}, "
          f"total_input: {result['total_input']:,}, total_output: {result['total_output']:,}, "
          f"elapsed: {result['total_elapsed']:.1f}s)")
    print(f"{'='*120}")

    stage_order = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    all_stage_keys = sorted(stages.keys(), key=lambda s: (
        stage_order.index(s) if s in stage_order else 50,
        s
    ))

    header = f"{'Stage':<16} {'Name':<14} {'Iters':>6} {'Which':>14} {'InTok':>10} {'OutTok':>10} {'In/Iter':>10} {'Out/Iter':>10} {'Elapsed':>8} {'Files Written'}"
    print(header)
    print("-" * 120)

    sum_in = 0
    sum_out = 0
    sum_elapsed = 0
    sum_iters = 0

    for sk in all_stage_keys:
        sd = stages[sk]
        n = len(sd["iterations"])
        iters_str = ",".join(str(i) for i in sd["iterations"])
        if len(iters_str) > 12:
            iters_str = iters_str[:11] + "…"
        total_in = sum(sd["input_tokens"])
        total_out = sum(sd["output_tokens"])
        avg_in = total_in // n if n > 0 else 0
        avg_out = total_out // n if n > 0 else 0
        elapsed = sum(sd["elapsed_sec"])
        files = sorted(set(shorten_path(f) for f in sd["files_written"]))
        files_str = ", ".join(files) if files else "-"
        if len(files_str) > 60:
            files_str = files_str[:57] + "..."

        sname = STAGE_NAMES.get(sk, sk)
        print(f"{sk:<16} {sname:<14} {n:>6} {iters_str:>14} {total_in:>10,} {total_out:>10,} {avg_in:>10,} {avg_out:>10,} {elapsed:>7.1f}s {files_str}")

        sum_in += total_in
        sum_out += total_out
        sum_elapsed += elapsed
        sum_iters += n

    print("-" * 120)
    print(f"{'TOTAL':<16} {'':14} {sum_iters:>6} {'':>14} {sum_in:>10,} {sum_out:>10,} {'':>10} {'':>10} {sum_elapsed:>7.1f}s")
    print()


def print_combined_summary(results: list[dict]):
    """Print a combined summary table across all traces."""
    print(f"\n{'='*140}")
    print("  COMBINED SUMMARY: Token Distribution by Stage")
    print(f"{'='*140}")

    stage_order = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    all_keys = set()
    for r in results:
        all_keys.update(r["stages"].keys())

    sorted_keys = sorted(all_keys, key=lambda s: (
        stage_order.index(s) if s in stage_order else 50, s
    ))

    header_parts = [f"{'Stage':<14}"]
    for r in results:
        lbl = r["label"]
        header_parts.append(f"{'InTok':>9} {'OutTok':>8} {'#It':>4}")
    header = " | ".join(header_parts)

    model_header_parts = [f"{'':14}"]
    for r in results:
        lbl = r["label"][:18]
        model_header_parts.append(f"{lbl:^23}")
    model_header = " | ".join(model_header_parts)

    print(model_header)
    print(header)
    print("-" * 140)

    for sk in sorted_keys:
        sname = STAGE_NAMES.get(sk, sk)
        row_parts = [f"{sk + ' ' + sname:<14}"]
        for r in results:
            sd = r["stages"].get(sk)
            if sd:
                total_in = sum(sd["input_tokens"])
                total_out = sum(sd["output_tokens"])
                n = len(sd["iterations"])
                row_parts.append(f"{total_in:>9,} {total_out:>8,} {n:>4}")
            else:
                row_parts.append(f"{'—':>9} {'—':>8} {'—':>4}")
        print(" | ".join(row_parts))

    print("-" * 140)

    totals_parts = [f"{'TOTAL':<14}"]
    for r in results:
        totals_parts.append(f"{r['total_input']:>9,} {r['total_output']:>8,} {r['total_iterations']:>4}")
    print(" | ".join(totals_parts))
    print()

    print(f"\n{'='*140}")
    print("  COMBINED SUMMARY: Token % Distribution by Stage")
    print(f"{'='*140}")

    pct_header_parts = [f"{'Stage':<14}"]
    for r in results:
        lbl = r["label"][:18]
        pct_header_parts.append(f"{'In%':>7} {'Out%':>7} {'#It':>4}")
    pct_header_model = [f"{'':14}"]
    for r in results:
        lbl = r["label"][:18]
        pct_header_model.append(f"{lbl:^20}")

    print(" | ".join(pct_header_model))
    print(" | ".join(pct_header_parts))
    print("-" * 140)

    for sk in sorted_keys:
        sname = STAGE_NAMES.get(sk, sk)
        row_parts = [f"{sk + ' ' + sname:<14}"]
        for r in results:
            sd = r["stages"].get(sk)
            if sd:
                total_in = sum(sd["input_tokens"])
                total_out = sum(sd["output_tokens"])
                n = len(sd["iterations"])
                in_pct = (total_in / r["total_input"] * 100) if r["total_input"] > 0 else 0
                out_pct = (total_out / r["total_output"] * 100) if r["total_output"] > 0 else 0
                row_parts.append(f"{in_pct:>6.1f}% {out_pct:>6.1f}% {n:>4}")
            else:
                row_parts.append(f"{'—':>7} {'—':>7} {'—':>4}")
        print(" | ".join(row_parts))

    print("-" * 140)
    print()


def print_files_per_stage(results: list[dict]):
    """Print files written per stage for each trace."""
    print(f"\n{'='*120}")
    print("  FILES WRITTEN PER STAGE")
    print(f"{'='*120}")

    for r in results:
        print(f"\n--- {r['label']} ({r['model']}) ---")
        stage_order = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
        sorted_keys = sorted(r["stages"].keys(), key=lambda s: (
            stage_order.index(s) if s in stage_order else 50, s
        ))
        for sk in sorted_keys:
            sd = r["stages"][sk]
            files = sorted(set(shorten_path(f) for f in sd["files_written"]))
            if files:
                sname = STAGE_NAMES.get(sk, sk)
                print(f"  {sk} ({sname}): {', '.join(files)}")


def main():
    results = []
    for label, filepath in TRACE_FILES.items():
        if not os.path.exists(filepath):
            print(f"WARNING: {filepath} not found, skipping {label}")
            continue
        result = analyze_trace(label, filepath)
        results.append(result)

    for r in results:
        print_trace_table(r)

    print_combined_summary(results)
    print_files_per_stage(results)


if __name__ == "__main__":
    main()
