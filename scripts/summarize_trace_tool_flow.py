#!/usr/bin/env python3
"""Summarize agent trace JSON: per-iteration tool calls and assistant text preview."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TRACE_PATHS = [
    str(PROJECT_ROOT / "output/wxreader-7__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/44e2457b2445.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/2c376c4bbbea.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5/.designforge/traces/00_/agentic/4ebe69ba5fb2.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__gpt-5.1/.designforge/traces/00_/agentic/98326931a205.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-8__agentic__gemini-2.5-pro/.designforge/traces/00_/agentic/d8d7e3826f4c.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-10__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/a52e32a5e136.trace.json"),
    str(PROJECT_ROOT / "output/wxreader-9__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/7a33123c0ddc.trace.json"),
]


def experiment_id_from_trace_path(trace_path: str) -> str:
    for part in Path(trace_path).parts:
        if part.startswith("wxreader-"):
            return part.split("__")[0]
    m = re.search(r"(wxreader-\d+)", trace_path)
    return m.group(1) if m else "unknown"


def _basename(p: Any) -> str:
    if not p or not isinstance(p, str):
        return ""
    return os.path.basename(p.rstrip("/")) or p


def summarize_tool_call(name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}
    if name == "write_file":
        return f"{name}({_basename(args.get('path'))})"
    if name == "read_file":
        return f"{name}({_basename(args.get('path'))})"
    if name == "delete_file":
        return f"{name}({_basename(args.get('path'))})"
    if name == "list_files":
        d = args.get("directory") or args.get("path")
        label = _basename(d) if d else ""
        if (not label or label == ".") and isinstance(d, str) and d not in (".", ""):
            label = d.rstrip("/").split("/")[-1] or d
        if label == ".":
            label = "."
        return f"{name}({label or str(d)[:40]})"
    if name == "str_replace":
        return f"{name}({_basename(args.get('path'))})"
    if name == "batch_str_replace":
        edits = args.get("edits") or args.get("replacements") or []
        if isinstance(edits, list) and edits:
            basenames: list[str] = []
            for e in edits:
                if isinstance(e, dict) and e.get("path"):
                    basenames.append(_basename(e["path"]))
            uniq = []
            for b in basenames:
                if b not in uniq:
                    uniq.append(b)
            if len(uniq) <= 5:
                files = ",".join(uniq)
            else:
                files = f"{len(uniq)} files: {','.join(uniq[:3])}…"
            return f"{name}({files})"
        return f"{name}()"
    if name == "run_command":
        cmd = (args.get("cmd") or args.get("command") or "").strip().replace("\n", " ")
        if len(cmd) > 72:
            cmd = cmd[:69] + "..."
        cwd = args.get("cwd")
        cwd_b = _basename(cwd) if cwd else ""
        if cwd_b:
            return f"{name}({cmd} @ {cwd_b})"
        return f"{name}({cmd})"
    if name == "grep":
        pat = str(args.get("pattern") or args.get("regex") or "")[:40]
        path = args.get("path")
        directory = args.get("directory")
        loc = ""
        if path:
            loc = _basename(path)
        elif directory:
            loc = _basename(directory)
        if not loc and args.get("glob"):
            loc = str(args.get("glob"))[:30]
        return f"{name}({pat!r} in {loc or '?'})"
    if name == "task_done":
        summ = str(args.get("summary") or "")[:50]
        if summ:
            return f"{name}({summ!r})"
        return f"{name}()"
    # fallback: show a few scalar keys
    bits = []
    for k in ("path", "directory", "cmd", "command", "pattern"):
        if k in args and isinstance(args[k], str):
            v = args[k]
            if k in ("path", "directory"):
                bits.append(f"{k}={_basename(v)}")
            else:
                bits.append(f"{k}={v[:40]}")
    if bits:
        return f"{name}({'; '.join(bits)})"
    return name


def one_line_assistant(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    s = text.replace("\r", "").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def print_trace_table(trace_path: str, assistant_preview_len: int = 80) -> None:
    path = Path(trace_path)
    if not path.is_file():
        print(f"\n### MISSING FILE: {trace_path}\n")
        return

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    model = data.get("model", "?")
    trace_id = data.get("trace_id", path.stem.replace(".trace", ""))
    exp_id = experiment_id_from_trace_path(str(path))
    iterations = data.get("iterations") or []

    print()
    print("=" * 100)
    print(f"Experiment ID: {exp_id}")
    print(f"Model:         {model}")
    print(f"Trace file:    {path}")
    print(f"trace_id:      {trace_id}  |  iterations: {len(iterations)}")
    print("=" * 100)

    col_iter = 5
    col_tools = 100
    col_text = assistant_preview_len

    header = (
        f"{'iter':>{col_iter}} | "
        f"{'tools (name + target / cmd summary)':<{col_tools}} | "
        f"{'assistant_text (first ' + str(assistant_preview_len) + ' chars)':<{col_text}}"
    )
    print(header)
    print("-" * len(header))

    for it in iterations:
        num = it.get("iteration", "?")
        tools = it.get("tool_calls") or []
        parts: list[str] = []
        for tc in tools:
            if isinstance(tc, dict):
                parts.append(summarize_tool_call(str(tc.get("name", "?")), tc.get("arguments")))
            else:
                parts.append(str(tc))
        tools_str = "; ".join(parts) if parts else "(no tools)"
        if len(tools_str) > col_tools:
            tools_str = tools_str[: col_tools - 3] + "..."

        atext = one_line_assistant(it.get("assistant_text"), assistant_preview_len)
        if len(atext) > col_text:
            atext = atext[: col_text - 3] + "..."

        print(f"{num!s:>{col_iter}} | {tools_str:<{col_tools}} | {atext:<{col_text}}")


def main() -> int:
    paths = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_TRACE_PATHS
    for p in paths:
        print_trace_table(p)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
