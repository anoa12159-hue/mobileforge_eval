#!/usr/bin/env python3
"""Analyze token composition from agent trace JSON files."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 3.5
IMAGE_TOKENS_EST = 1700
_WROTE_CHARS_RE = re.compile(r"wrote\s+(\d+)\s+chars", re.IGNORECASE)


def chars_to_tokens(n: int) -> float:
    return n / CHARS_PER_TOKEN


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    return str(x)


def count_image_blocks(user: list[dict[str, Any]] | None) -> int:
    if not user:
        return 0
    return sum(1 for b in user if isinstance(b, dict) and b.get("type") == "image_url")


def sum_user_text_lens(user: list[dict[str, Any]] | None) -> int:
    if not user:
        return 0
    total = 0
    for b in user:
        if isinstance(b, dict) and b.get("type") == "text":
            total += len(safe_str(b.get("text")))
    return total


def extract_wrote_chars(result: str) -> int | None:
    m = _WROTE_CHARS_RE.search(result or "")
    if m:
        return int(m.group(1))
    return None


def str_replace_arg_chars(args: dict[str, Any] | None, result: str) -> int:
    if not isinstance(args, dict):
        return len(result or "")
    old_s = args.get("old_string")
    new_s = args.get("new_string")
    if isinstance(old_s, str) and isinstance(new_s, str) and (old_s or new_s):
        return len(old_s) + len(new_s)
    # batch_str_replace: list of replacements
    reps = args.get("replacements")
    if isinstance(reps, list):
        acc = 0
        for r in reps:
            if isinstance(r, dict):
                o = r.get("old_string")
                n = r.get("new_string")
                if isinstance(o, str) and isinstance(n, str):
                    acc += len(o) + len(n)
        if acc:
            return acc
    return len(result or "")


def assistant_chars(prev: dict[str, Any]) -> int:
    """Trace writers may omit empty assistant text; some providers omit the field entirely."""
    for key in ("assistant_text", "assistant", "assistant_content"):
        v = prev.get(key)
        if isinstance(v, str) and v.strip():
            return len(v)
    return 0


def categorize_prev_iteration(prev: dict[str, Any]) -> dict[str, int]:
    """Return character counts per category from one iteration's assistant + tools."""
    out: dict[str, int] = {
        "assistant_text": assistant_chars(prev),
        "write_file_args": 0,
        "read_file_result": 0,
        "list_files_result": 0,
        "run_command_result": 0,
        "str_replace_args": 0,
        "other_tool_result": 0,
    }
    for tc in prev.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        name = safe_str(tc.get("name"))
        result = safe_str(tc.get("result"))
        args = tc.get("arguments")
        if not isinstance(args, dict):
            args = {}

        if name == "write_file":
            n = extract_wrote_chars(result)
            if n is not None:
                out["write_file_args"] += n
            else:
                c = args.get("content")
                out["write_file_args"] += len(c) if isinstance(c, str) else 0
        elif name == "read_file":
            out["read_file_result"] += len(result)
        elif name == "list_files":
            out["list_files_result"] += len(result)
        elif name == "run_command":
            out["run_command_result"] += len(result)
        elif name in ("str_replace", "batch_str_replace"):
            out["str_replace_args"] += str_replace_arg_chars(args, result)
        else:
            out["other_tool_result"] += len(result)
    return out


def char_counts_to_token_estimates(chars: dict[str, int]) -> dict[str, float]:
    est: dict[str, float] = {}
    for k, v in chars.items():
        est[k] = chars_to_tokens(v)
    return est


def sum_token_estimates(est: dict[str, float], keys: list[str]) -> float:
    return sum(est.get(k, 0.0) for k in keys)


def format_table(headers: list[str], rows: list[list[Any]], col_widths: list[int] | None = None) -> str:
    if not rows:
        return ""
    if col_widths is None:
        col_widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    sep = " | "
    head = sep.join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers))
    bar = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    lines = [head, bar]
    for row in rows:
        lines.append(sep.join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


def analyze_trace(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    iters: list[dict[str, Any]] = data.get("iterations") or []
    inp = data.get("input") or {}
    system = safe_str(inp.get("system"))
    user = inp.get("user")
    if not isinstance(user, list):
        user = []

    n_images = count_image_blocks(user)
    user_text_chars = sum_user_text_lens(user)

    print("=" * 100)
    print(f"Trace: {path}")
    print(f"  trace_id={data.get('trace_id')}  model={data.get('model')}  iterations={len(iters)}")
    if iters:
        last_in = iters[-1].get("tokens", {}).get("input")
        print(f"  last_iteration_input_tokens (actual): {last_in}")
        asst_logged = sum(assistant_chars(it) for it in iters[:-1])
        if asst_logged == 0 and len(iters) > 1:
            print(
                "  note: 多轮迭代中未记录非空的 assistant 文本（常见于仅 tool_calls 的 trace）；"
                "累计「assistant_text」会偏低，差额会体现在 other_overhead。"
            )

    # --- Iteration 1 initial composition ---
    print("\n## Iteration 1 — initial prompt composition (estimate vs actual first-call input)\n")
    if not iters:
        print("(no iterations)")
        return
    iter1_in = int(iters[0].get("tokens", {}).get("input") or 0)
    sys_tok = chars_to_tokens(len(system))
    img_tok = n_images * IMAGE_TOKENS_EST
    ut_tok = chars_to_tokens(user_text_chars)
    init_sum = sys_tok + img_tok + ut_tok
    init_overhead = iter1_in - init_sum
    h = ["component", "chars_or_units", "est_tokens", "notes"]
    rows_i1: list[list[Any]] = [
        ["system_prompt", len(system), f"{sys_tok:.1f}", ""],
        ["images", f"{n_images} × {IMAGE_TOKENS_EST}", f"{img_tok:.1f}", "count × ~1700 tok/image"],
        ["user_text", user_text_chars, f"{ut_tok:.1f}", "text blocks only"],
        ["─ sum (est.)", "—", f"{init_sum:.1f}", ""],
        ["message_overhead", "—", f"{init_overhead:.1f}", "first_call_input − sum(est.)"],
        ["actual first_call_input", "—", iter1_in, "tokens.input @ iteration 1"],
    ]
    print(format_table(h, rows_i1))

    # --- Per-iteration delta ---
    print("\n## Per-iteration Δinput (delta = input[i] − input[i−1])\n")
    delta_keys = [
        "delta_actual",
        "assistant_tok",
        "write_file_tok",
        "read_file_tok",
        "list_files_tok",
        "run_cmd_tok",
        "str_replace_tok",
        "other_tool_tok",
        "sum_est_tok",
        "overhead_tok",
    ]
    delta_rows: list[list[Any]] = []

    # Row for iteration 1: no previous delta in the same sense; show first input only
    delta_rows.append(
        [
            1,
            "—",
            iter1_in,
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "(first call; see section above)",
        ]
    )

    content_keys = [
        "assistant_text",
        "write_file_args",
        "read_file_result",
        "list_files_result",
        "run_command_result",
        "str_replace_args",
        "other_tool_result",
    ]

    for j in range(1, len(iters)):
        prev = iters[j - 1]
        cur_in = int(iters[j].get("tokens", {}).get("input") or 0)
        prev_in = int(iters[j - 1].get("tokens", {}).get("input") or 0)
        delta = cur_in - prev_in
        ch = categorize_prev_iteration(prev)
        est = char_counts_to_token_estimates(ch)
        sum_est = sum_token_estimates(est, content_keys)
        overhead = delta - sum_est
        note = ""
        if delta < 0:
            note = "Δ<0 (context shrink, e.g. PACF)"
        delta_rows.append(
            [
                j + 1,
                delta,
                cur_in,
                f"{est['assistant_text']:.1f}",
                f"{est['write_file_args']:.1f}",
                f"{est['read_file_result']:.1f}",
                f"{est['list_files_result']:.1f}",
                f"{est['run_command_result']:.1f}",
                f"{est['str_replace_args']:.1f}",
                f"{est['other_tool_result']:.1f}",
                f"{sum_est:.1f}",
                f"{overhead:.1f}",
                note,
            ]
        )

    hdr = [
        "iter",
        "Δin",
        "input",
        "asst",
        "write",
        "read",
        "list",
        "run",
        "srepl",
        "oth",
        "Σest",
        "ovhd",
        "note",
    ]
    print(format_table(hdr, delta_rows))

    # --- Cumulative final summary (last iteration) ---
    print("\n## Cumulative breakdown @ last iteration (est. vs actual)\n")
    last_in = int(iters[-1].get("tokens", {}).get("input") or 0)

    cum_chars: dict[str, int] = {
        "system_prompt": len(system),
        "user_text": user_text_chars,
        "images": 0,  # handled separately as n_images * 1700
        "assistant_text": 0,
        "write_file_args": 0,
        "read_file_result": 0,
        "list_files_result": 0,
        "run_command_result": 0,
        "str_replace_args": 0,
        "other_tool_result": 0,
    }
    # Last row's `tokens.input` is context *before* that turn's assistant/tools are appended.
    for it in iters[:-1]:
        c = categorize_prev_iteration(it)
        cum_chars["assistant_text"] += c["assistant_text"]
        cum_chars["write_file_args"] += c["write_file_args"]
        cum_chars["read_file_result"] += c["read_file_result"]
        cum_chars["list_files_result"] += c["list_files_result"]
        cum_chars["run_command_result"] += c["run_command_result"]
        cum_chars["str_replace_args"] += c["str_replace_args"]
        cum_chars["other_tool_result"] += c["other_tool_result"]

    # Heuristic: PACF / context pruning often drops screenshots after turn 1.
    pacf_images_removed = (
        len(iters) >= 2
        and int(iters[1].get("tokens", {}).get("input") or 0)
        < int(iters[0].get("tokens", {}).get("input") or 0) - 3000
    )
    image_tok_final = 0.0 if pacf_images_removed else float(n_images * IMAGE_TOKENS_EST)

    # Token estimates
    rows_f: list[list[Any]] = []
    categories: list[tuple[str, float]] = []

    def add_row(label: str, est_tok: float, extra: str = ""):
        categories.append((label, est_tok))
        rows_f.append([label, f"{est_tok:.1f}", extra])

    add_row("system_prompt", chars_to_tokens(cum_chars["system_prompt"]))
    img_note = (
        f"0 (PACF heuristic: iter2 input ≪ iter1)"
        if pacf_images_removed
        else f"{n_images} images × {IMAGE_TOKENS_EST}"
    )
    add_row("images", image_tok_final, img_note)
    add_row("user_text (initial)", chars_to_tokens(cum_chars["user_text"]))
    add_row("assistant_text (all turns)", chars_to_tokens(cum_chars["assistant_text"]))
    add_row("write_file args (wrote N chars)", chars_to_tokens(cum_chars["write_file_args"]))
    add_row("read_file results", chars_to_tokens(cum_chars["read_file_result"]))
    add_row("list_files results", chars_to_tokens(cum_chars["list_files_result"]))
    add_row("run_command results", chars_to_tokens(cum_chars["run_command_result"]))
    add_row("str_replace / batch args", chars_to_tokens(cum_chars["str_replace_args"]))
    add_row("other tool results", chars_to_tokens(cum_chars["other_tool_result"]))

    sum_est_total = sum(t for _, t in categories)
    other_overhead = last_in - sum_est_total
    categories.append(("other_overhead (remainder)", other_overhead))
    rows_f.append(["other_overhead (actual − Σ est.)", f"{other_overhead:.1f}", "JSON/roles/tool markup, estimator error, pruned context…"])
    rows_f.append(["─ actual last input_tokens", str(last_in), "from trace"])

    print(format_table(["category", "est_tokens", "notes"], rows_f))

    pct_rows: list[list[Any]] = []
    for label, est_tok in categories:
        pct = (100.0 * est_tok / last_in) if last_in else 0.0
        pct_rows.append([label, f"{est_tok:.1f}", f"{pct:.2f}%"])
    print("\nPercentages relative to **actual** last `tokens.input`:\n")
    print(format_table(["category", "est_tokens", "% of actual input"], pct_rows))
    print()


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TRACES = [
    str(PROJECT_ROOT / "output/wxreader-7__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/44e2457b2445.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-10__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/a52e32a5e136.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5/.designforge/traces/00_/agentic/4ebe69ba5fb2.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5.1/.designforge/traces/00_/agentic/98326931a205.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/2c376c4bbbea.trace.json"),
]


def main() -> None:
    paths = [Path(p) for p in (sys.argv[1:] or DEFAULT_TRACES)]
    for p in paths:
        if not p.is_file():
            print(f"MISSING: {p}", file=sys.stderr)
            continue
        analyze_trace(p)


if __name__ == "__main__":
    main()
