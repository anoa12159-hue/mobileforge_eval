#!/usr/bin/env python3
"""Extract per-iteration context resource inventory from agent trace JSON."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 3.5
IMAGE_TOKENS = 1700

WROTE_CHARS_RE = re.compile(r"wrote\s+(\d+)\s+chars", re.I)


def est_tokens(chars: int | None, *, is_image: bool = False) -> int:
    if is_image:
        return IMAGE_TOKENS
    if chars is None:
        return 0
    return int(round(chars / CHARS_PER_TOKEN))


def basename(path: str) -> str:
    return os.path.basename(path or "") or "—"


def cmd_summary(cmd: str, max_len: int = 72) -> str:
    cmd = (cmd or "").replace("\n", " ").strip()
    if len(cmd) <= max_len:
        return cmd
    return cmd[: max_len - 3] + "..."


def parse_wrote_chars(result: str | None) -> int | None:
    if not result:
        return None
    m = WROTE_CHARS_RE.search(result)
    if m:
        return int(m.group(1))
    return None


def image_target_from_user_block(user: list[dict[str, Any]], idx: int) -> str:
    """Next text block after image often is [Page: Screenshot_...]."""
    if idx + 1 < len(user):
        nxt = user[idx + 1]
        if isinstance(nxt, dict) and nxt.get("type") == "text":
            t = nxt.get("text") or ""
            if t.startswith("[Page:") and t.endswith("]"):
                inner = t[6:-1].strip()
                return basename(inner.replace(" ", "_"))
    return f"image_{idx // 2 + 1}"


def collect_rows(trace: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seq = 0

    def push(row: dict[str, Any]) -> None:
        nonlocal seq
        row["_seq"] = seq
        seq += 1
        rows.append(row)
    inp = trace.get("input") or {}
    system = inp.get("system") or ""
    if isinstance(system, str):
        slen = len(system)
        push(
            {
                "iter": 0,
                "type": "system_prompt",
                "target": "—",
                "chars": slen,
                "est_tokens": est_tokens(slen),
            }
        )

    user = inp.get("user") or []
    if not isinstance(user, list):
        user = []

    text_char_total = 0
    for i, block in enumerate(user):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "image_url":
            push(
                {
                    "iter": 0,
                    "type": "image",
                    "target": image_target_from_user_block(user, i),
                    "chars": None,
                    "est_tokens": est_tokens(None, is_image=True),
                }
            )
        elif btype == "text":
            t = block.get("text") or ""
            if isinstance(t, str):
                text_char_total += len(t)

    push(
        {
            "iter": 0,
            "type": "user_text",
            "target": "instruction",
            "chars": text_char_total,
            "est_tokens": est_tokens(text_char_total),
        }
    )

    for it in trace.get("iterations") or []:
        if not isinstance(it, dict):
            continue
        iteration = int(it.get("iteration", 0))
        at = it.get("assistant_text")
        if isinstance(at, str) and at:
            push(
                {
                    "iter": iteration,
                    "type": "assistant_text",
                    "target": "analysis" if iteration == 1 else "—",
                    "chars": len(at),
                    "est_tokens": est_tokens(len(at)),
                }
            )

        for tc in it.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = (tc.get("name") or "").strip()
            args = tc.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            result = tc.get("result")
            if not isinstance(result, str):
                result = "" if result is None else str(result)

            if name == "write_file":
                path = args.get("path") or ""
                content = args.get("content") or ""
                if not isinstance(content, str):
                    content = str(content)
                bn = basename(str(path))
                args_chars = len(content)
                wrote = parse_wrote_chars(result)
                push(
                    {
                        "iter": iteration,
                        "type": "write_file_args",
                        "target": bn,
                        "chars": args_chars,
                        "est_tokens": est_tokens(args_chars),
                    }
                )
                if wrote is not None and wrote != args_chars:
                    push(
                        {
                            "iter": iteration,
                            "type": "write_file_payload",
                            "target": bn,
                            "chars": wrote,
                            "est_tokens": est_tokens(wrote),
                        }
                    )
                push(
                    {
                        "iter": iteration,
                        "type": "write_file_result",
                        "target": bn,
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

            elif name == "read_file":
                path = args.get("path") or ""
                bn = basename(str(path))
                push(
                    {
                        "iter": iteration,
                        "type": "read_file",
                        "target": bn,
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

            elif name == "list_files":
                d = args.get("directory") or args.get("path") or ""
                push(
                    {
                        "iter": iteration,
                        "type": "list_files",
                        "target": basename(str(d)),
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

            elif name == "run_command":
                cmd = args.get("cmd") or args.get("command") or ""
                if not isinstance(cmd, str):
                    cmd = str(cmd)
                push(
                    {
                        "iter": iteration,
                        "type": "run_command",
                        "target": cmd_summary(cmd),
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

            elif name in ("str_replace", "batch_str_replace"):
                path = args.get("path") or ""
                bn = basename(str(path))
                if name == "str_replace":
                    old_s = args.get("old_string") or ""
                    new_s = args.get("new_string") or ""
                    if not isinstance(old_s, str):
                        old_s = str(old_s)
                    if not isinstance(new_s, str):
                        new_s = str(new_s)
                    pair_chars = len(old_s) + len(new_s)
                    push(
                        {
                            "iter": iteration,
                            "type": "str_replace_args",
                            "target": bn,
                            "chars": pair_chars,
                            "est_tokens": est_tokens(pair_chars),
                        }
                    )
                    push(
                        {
                            "iter": iteration,
                            "type": "str_replace_result",
                            "target": bn,
                            "chars": len(result),
                            "est_tokens": est_tokens(len(result)),
                        }
                    )
                else:
                    reps = args.get("replacements") or args.get("edits") or []
                    total_old_new = 0
                    if isinstance(reps, list):
                        for r in reps:
                            if not isinstance(r, dict):
                                continue
                            o = r.get("old_string") or ""
                            n = r.get("new_string") or ""
                            if not isinstance(o, str):
                                o = str(o)
                            if not isinstance(n, str):
                                n = str(n)
                            total_old_new += len(o) + len(n)
                    else:
                        old_s = args.get("old_string") or ""
                        new_s = args.get("new_string") or ""
                        if not isinstance(old_s, str):
                            old_s = str(old_s)
                        if not isinstance(new_s, str):
                            new_s = str(new_s)
                        total_old_new = len(old_s) + len(new_s)
                    push(
                        {
                            "iter": iteration,
                            "type": "batch_str_replace_args",
                            "target": bn or "—",
                            "chars": total_old_new,
                            "est_tokens": est_tokens(total_old_new),
                        }
                    )
                    push(
                        {
                            "iter": iteration,
                            "type": "batch_str_replace_result",
                            "target": bn or "—",
                            "chars": len(result),
                            "est_tokens": est_tokens(len(result)),
                        }
                    )

            elif name == "task_done":
                summ = args.get("summary") or ""
                if not isinstance(summ, str):
                    summ = str(summ)
                push(
                    {
                        "iter": iteration,
                        "type": "task_done_args",
                        "target": "summary",
                        "chars": len(summ),
                        "est_tokens": est_tokens(len(summ)),
                    }
                )
                push(
                    {
                        "iter": iteration,
                        "type": "task_done_result",
                        "target": "—",
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

            else:
                try:
                    args_blob = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args_blob = str(args)
                push(
                    {
                        "iter": iteration,
                        "type": f"{name}_args",
                        "target": "—",
                        "chars": len(args_blob),
                        "est_tokens": est_tokens(len(args_blob)),
                    }
                )
                push(
                    {
                        "iter": iteration,
                        "type": f"{name}_result",
                        "target": "—",
                        "chars": len(result),
                        "est_tokens": est_tokens(len(result)),
                    }
                )

    rows.sort(key=lambda r: (r["iter"], r["_seq"]))
    for r in rows:
        del r["_seq"]
    return rows


def format_table(rows: list[dict[str, Any]], title: str) -> str:
    lines: list[str] = []
    lines.append(title)
    lines.append("")
    col_iter, col_type, col_target, col_chars, col_tok = 4, 19, 52, 6, 10
    head = (
        f"{'iter':>{col_iter}} | "
        f"{'type':<{col_type}} | "
        f"{'target':<{col_target}} | "
        f"{'chars':>{col_chars}} | "
        f"{'est_tokens':>{col_tok}}"
    )
    sep = (
        f"{'-' * col_iter}-|-"
        f"{'-' * col_type}-|-"
        f"{'-' * col_target}-|-"
        f"{'-' * col_chars}-|-"
        f"{'-' * col_tok}"
    )
    lines.append(head)
    lines.append(sep)
    for r in rows:
        ch = r["chars"]
        chs = "—" if ch is None else str(ch)
        tgt = str(r["target"])
        if len(tgt) > col_target:
            tgt = tgt[: col_target - 3] + "..."
        lines.append(
            f"{r['iter']:>{col_iter}} | "
            f"{r['type']:<{col_type}} | "
            f"{tgt:<{col_target}} | "
            f"{chs:>{col_chars}} | "
            f"{r['est_tokens']:>{col_tok}}"
        )
    return "\n".join(lines)


def cumulative_by_type(rows: list[dict[str, Any]]) -> str:
    agg: dict[str, int] = defaultdict(int)
    for r in rows:
        agg[str(r["type"])] += int(r["est_tokens"])
    lines = ["", "Cumulative est_tokens by type:", "-" * 40]
    for k in sorted(agg.keys()):
        lines.append(f"  {k:<28} {agg[k]:>10}")
    lines.append("-" * 40)
    lines.append(f"  {'TOTAL':<28} {sum(agg.values()):>10}")
    return "\n".join(lines)


def image_count(user: Any) -> int:
    if not isinstance(user, list):
        return 0
    return sum(1 for b in user if isinstance(b, dict) and b.get("type") == "image_url")


def analyze_path(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        trace = json.load(f)
    rows = collect_rows(trace)
    tid = trace.get("trace_id", "?")
    out = [
        "=" * 80,
        f"Trace: {path}",
        f"trace_id={tid}  model={trace.get('model')}  iterations={trace.get('total_iterations')}",
        "=" * 80,
        "",
        "Summary (initial turn):",
        f"  image_count (user message):     {image_count(trace.get('input', {}).get('user'))}",
        f"  system_prompt chars:            {len((trace.get('input') or {}).get('system') or '')}",
    ]
    ut = trace.get("input", {}).get("user") or []
    text_total = 0
    if isinstance(ut, list):
        for b in ut:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text") or ""
                if isinstance(t, str):
                    text_total += len(t)
    out.append(f"  user_text (non-image) chars:    {text_total}")
    out.append("")
    out.append(format_table(rows, "DETAILED TABLE (sorted by iter, then appearance order)"))
    out.append(cumulative_by_type(rows))
    return "\n".join(out)


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    paths = sys.argv[1:] or [
        str(project_root / "output/wxreader-7__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/44e2457b2445.trace.json"),
        str(project_root / "output/wxreader-10__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/a52e32a5e136.trace.json"),
    ]
    blocks = []
    for p in paths:
        blocks.append(analyze_path(p))
        blocks.append("")
    print("\n".join(blocks).rstrip())


if __name__ == "__main__":
    main()
