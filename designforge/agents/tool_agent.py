"""
ToolAgent — base class for agents that operate in a tool-use conversation loop.

Subclasses (PageAgent, MergeAgent, BuildAgent) define:
  - get_tools(): LLM-visible tool schemas
  - execute_tool(name, args): dispatch a tool call to real Python functions
  - verify_completion(): programmatic self-check when LLM says "I'm done"

The loop:
  1. Send messages + tool definitions to LLM
  2. If response contains tool_calls → execute → append results → continue
     (If ``task_done`` is among the calls, non-task tools run first, then
     verify_completion runs once; on pass the loop returns immediately.)
  3. If response has no tool_calls (LLM thinks it's done) → verify_completion()
     3a. Passed → return
     3b. Failed → inject failure reasons as user message → continue
  4. Exceeds max_iterations → force return
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from pathlib import Path
from typing import Any

from designforge import config
from designforge.agents.base_agent import extract_json as _extract_json, _wrap_usage
from designforge.llm_client import completion
from designforge.metrics import get_metrics
from designforge.trace_writer import IterationRecord, MAX_TOOL_RESULT_LEN

logger = logging.getLogger(__name__)

_todo_output_dir: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_todo_output_dir", default=None
)


def _truncate_command_output_for_llm(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    note = "(...truncated at start; showing tail of command output)\n"
    tail_len = max(0, max_chars - len(note))
    if tail_len < 80:
        return text[-max_chars:]
    return note + text[-tail_len:]


TOOL_DEFS: dict[str, dict] = {
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content. Parent directories are created automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read file contents. Very large files may be truncated in the response "
                "(head + tail); use grep to locate lines. Pass max_chars=0 to request no limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "max_chars": {
                        "type": "integer",
                        "description": (
                            "Optional cap on returned characters (0 = unlimited). "
                            "Omit to use server default."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    "str_replace": {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Replace exactly one occurrence of old_string with new_string in the file. Useful for targeted edits without rewriting the entire file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Exact text to find (must appear exactly once)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    "list_files": {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Recursively list files under a directory. "
                "Common noise dirs (e.g. node_modules, .designforge, dist) are skipped automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory path to list"},
                    "pattern": {"type": "string", "description": "Glob pattern to filter files (default: *)"},
                },
                "required": ["directory"],
            },
        },
    },
    "delete_file": {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to delete"},
                },
                "required": ["path"],
            },
        },
    },
    "run_command": {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command and return its exit code and output. Timeout: 180s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                },
                "required": ["cmd"],
            },
        },
    },
    "grep": {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search text files under a directory for lines containing a literal substring. "
                "Returns path:line:content for each match (capped)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Root directory to search (recursive)"},
                    "pattern": {"type": "string", "description": "Literal substring to find (not regex)"},
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern for files, e.g. *.tsx (default: all files)",
                    },
                },
                "required": ["directory", "pattern"],
            },
        },
    },
    "batch_str_replace": {
        "type": "function",
        "function": {
            "name": "batch_str_replace",
            "description": (
                "Apply multiple targeted edits across one or more files in a single call. "
                "Much more efficient than calling str_replace repeatedly.\n\n"
                "HOW TO USE:\n"
                "1. Plan all edits first — list every file and every change you intend to make.\n"
                "2. For each file, ensure every old_string is unique (appears exactly once) "
                "and that no two old_string regions overlap in the file.\n"
                "3. Submit all edits in one call. The tool handles correct ordering internally.\n\n"
                "SAFETY: Edits within a file are applied in reverse positional order to avoid "
                "position shifts. If any edit in a file fails (not found, ambiguous, or "
                "overlapping), ALL edits for that file are rolled back — other files proceed "
                "independently. The result clearly reports which files succeeded and which "
                "failed with classified errors (not_found / ambiguous / overlap)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": (
                            "Array of edit operations. Edits targeting different files are "
                            "independent. Edits targeting the same file must not overlap. "
                            "Keep to ≤15 edits per call for best results."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File path to edit"},
                                "old_string": {
                                    "type": "string",
                                    "description": "Exact text to find (must appear exactly once in the file)",
                                },
                                "new_string": {
                                    "type": "string",
                                    "description": "Replacement text",
                                },
                            },
                            "required": ["path", "old_string", "new_string"],
                        },
                    },
                },
                "required": ["edits"],
            },
        },
    },
    "task_done": {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": (
                "Signal that you believe the task is complete. Triggers programmatic verification. "
                "Call only after edits are saved. If verification fails, fix issues and call again. "
                "Do not use in the same turn as exploratory reads only — finish substantive work first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One short sentence on what was completed (optional, for logging)",
                    },
                },
                "required": [],
            },
        },
    },
    "update_tasks": {
        "type": "function",
        "function": {
            "name": "update_tasks",
            "description": (
                "Update the status of one or more stages. "
                "Set to 'in_progress' before starting a stage, 'done' after finishing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "enum": ["analyze", "foundation", "components", "pages", "assembly", "verify"],
                                    "description": "Stage id",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["in_progress", "done"],
                                    "description": "New status",
                                },
                            },
                            "required": ["id", "status"],
                        },
                        "description": "List of status updates",
                    },
                },
                "required": ["updates"],
            },
        },
    },
}


def _execute_builtin_tool(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch to the actual Python implementations in tools/."""
    from designforge.tools.command import run_command
    from designforge.tools.file_ops import (
        batch_str_replace,
        delete_file,
        grep_files,
        list_files,
        read_file,
        str_replace,
        write_file,
    )

    if name == "write_file":
        return write_file(arguments["path"], arguments["content"])
    elif name == "read_file":
        mc = arguments.get("max_chars")
        if mc is None:
            return read_file(arguments["path"])
        return read_file(arguments["path"], max_chars=int(mc))
    elif name == "str_replace":
        return str_replace(arguments["path"], arguments["old_string"], arguments["new_string"])
    elif name == "list_files":
        files = list_files(arguments["directory"], arguments.get("pattern", "*"))
        return "\n".join(files) if files else "(no files found)"
    elif name == "delete_file":
        return delete_file(arguments["path"])
    elif name == "run_command":
        from designforge.tools.file_ops import get_sandbox_root, resolve_sandbox_path
        cwd_arg = arguments.get("cwd")
        if cwd_arg:
            resolved_cwd = str(resolve_sandbox_path(cwd_arg))
        else:
            sb = get_sandbox_root()
            resolved_cwd = str(sb) if sb is not None else None
        result = run_command(arguments["cmd"], cwd=resolved_cwd, timeout=180)
        out = f"exit_code={result.return_code}\n{result.output}"
        return _truncate_command_output_for_llm(out, config.RUN_COMMAND_MAX_OUTPUT_CHARS)
    elif name == "batch_str_replace":
        return batch_str_replace(arguments.get("edits", []))
    elif name == "grep":
        return grep_files(
            arguments["directory"],
            arguments["pattern"],
            arguments.get("glob") or "*",
        )
    elif name == "update_tasks":
        from designforge.tools.todo import update_tasks
        output_dir = _todo_output_dir.get()
        if output_dir is None:
            return "Error: todo tool not initialized (no output_dir set)"
        return update_tasks(output_dir, arguments["updates"])
    else:
        return f"Unknown tool: {name}"


_SHORT_VALUE_KEYS = {"path", "directory", "pattern", "cmd", "cwd", "glob", "summary"}
_MAX_SHORT_LEN = 120


def _summarize_args(fn_name: str, args: dict[str, Any]) -> str:
    """Build a concise summary of tool arguments for logging.

    Short values (paths, commands) are shown in full; long values
    (file content, old_string, new_string) are shown as ``<N chars>``.
    """
    if fn_name == "batch_str_replace":
        edits = args.get("edits", [])
        paths = sorted({
            (e.get("path", "?") if isinstance(e, dict) else str(e)[:60])
            for e in edits
        })
        return f"edits={len(edits)} across files=[{', '.join(paths)}]"

    parts: list[str] = []
    for k, v in args.items():
        if k in _SHORT_VALUE_KEYS:
            s = str(v)
            if len(s) > _MAX_SHORT_LEN:
                s = s[:_MAX_SHORT_LEN] + "…"
            parts.append(f"{k}={s!r}")
        elif isinstance(v, str):
            parts.append(f"{k}=<{len(v)} chars>")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)


def _serialize_assistant_message(msg: Any) -> dict[str, Any]:
    """Convert a LiteLLM ChatCompletionMessage to a plain dict safe for re-sending.

    Preserves ``thought_signature`` from Gemini 3 models via the
    ``extra_content.google`` path that Chat API uses.
    """
    d: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        tc_list = []
        for tc in msg.tool_calls:
            tc_dict: dict[str, Any] = {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            sig = getattr(tc, "thought_signature", None)
            if sig:
                tc_dict["thought_signature"] = sig
                tc_dict["extra_content"] = {"google": {"thought_signature": sig}}
            tc_list.append(tc_dict)
        d["tool_calls"] = tc_list
    return d


_PARALLEL_SAFE_TOOLS = frozenset({"read_file", "write_file"})


def _group_tool_calls(calls: list[Any]) -> list[list[Any]]:
    """Group consecutive parallel-safe tool calls into batches.

    Consecutive read_file/write_file calls form a parallel batch.
    Any other tool acts as a barrier and becomes a single-item batch.
    """
    groups: list[list[Any]] = []
    current_batch: list[Any] = []

    for tc in calls:
        if tc.function.name in _PARALLEL_SAFE_TOOLS:
            current_batch.append(tc)
        else:
            if current_batch:
                groups.append(current_batch)
                current_batch = []
            groups.append([tc])

    if current_batch:
        groups.append(current_batch)

    return groups


class ToolAgent:
    """
    Base class for tool-using agents.

    Inherits LLM access from BaseAgent (via composition-like __init__),
    adds the tool-use loop with programmatic self-verification.
    """

    name: str = "tool_agent"

    def __init__(self, model: str | None = None, max_tokens: int | None = None):
        self.model = model or config.AGENT_MODEL
        self.max_tokens = max_tokens or config.get_max_tokens(self.model)
        self._current_page_id: str = ""
        self._protected_paths: set[str] = set()

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def get_tools(self) -> list[dict]:
        """Return LLM-visible tool definitions (OpenAI function-calling schema)."""
        raise NotImplementedError

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call with write-protection enforcement.

        If _protected_paths is set and the tool targets a protected file,
        the write is blocked and an error message is returned to the LLM.
        Path resolution uses the same sandbox-based logic as file_ops so
        that relative paths (e.g. ``src/main.tsx``) match correctly.
        """
        if self._protected_paths:
            from designforge.tools.file_ops import resolve_sandbox_path

            if name in ("write_file", "str_replace"):
                target = arguments.get("path", "")
                try:
                    resolved = str(resolve_sandbox_path(target))
                except (PermissionError, Exception):
                    resolved = str(Path(target).resolve())
                if resolved in self._protected_paths:
                    fname = Path(target).name
                    return (
                        f"BLOCKED: '{fname}' is a scaffold-managed infrastructure file "
                        f"and cannot be modified. Focus on application files "
                        f"(src/pages/, src/components/, src/router.tsx, src/App.tsx, etc.)."
                    )
            elif name == "batch_str_replace":
                edits = arguments.get("edits", [])
                filtered: list[dict] = []
                blocked_files: list[str] = []
                for edit in edits:
                    try:
                        resolved = str(resolve_sandbox_path(edit.get("path", "")))
                    except (PermissionError, Exception):
                        resolved = str(Path(edit.get("path", "")).resolve())
                    if resolved in self._protected_paths:
                        blocked_files.append(Path(edit["path"]).name)
                    else:
                        filtered.append(edit)
                if blocked_files:
                    unique = sorted(set(blocked_files))
                    msg = (
                        f"BLOCKED edits targeting scaffold-managed files: "
                        f"{', '.join(unique)}. Those edits were removed. "
                    )
                    if not filtered:
                        return msg + "No edits remaining."
                    arguments = {**arguments, "edits": filtered}
                    result = _execute_builtin_tool(name, arguments)
                    return msg + "\n" + result
        return _execute_builtin_tool(name, arguments)

    def verify_completion(self) -> tuple[bool, list[str]]:
        """Programmatic check after LLM signals completion.
        Returns (passed, list_of_error_messages).
        """
        return True, []

    def _execute_tool_batch(
        self, batch: list[Any]
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Execute a batch of tool calls, parallelizing when safe.

        Returns a list of (result_str, elapsed_sec, parsed_args) tuples,
        in the same order as the input batch.
        """
        parsed: list[tuple[Any, str, dict[str, Any]]] = []
        for tc in batch:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                logger.warning(
                    "[%s] failed to parse tool arguments for %s: %s",
                    self.name, fn_name, tc.function.arguments[:200],
                )
                fn_args = {}
            parsed.append((tc, fn_name, fn_args))

        def _run_one(tc: Any, fn_name: str, fn_args: dict) -> tuple[str, float, dict]:
            logger.info("[%s] tool: %s(%s)", self.name, fn_name, _summarize_args(fn_name, fn_args))
            t0 = time.time()
            try:
                result = self.execute_tool(fn_name, fn_args)
            except Exception as e:
                result = f"ERROR: {type(e).__name__}: {e}"
                logger.warning(
                    "[%s] tool %s raised: %s | parsed_keys=%s",
                    self.name, fn_name, e, sorted(fn_args.keys()),
                )
            elapsed = time.time() - t0
            result_s = str(result)
            preview = result_s[:500] + ("..." if len(result_s) > 500 else "")
            logger.info("[%s] tool %s returned (%d chars, %.2fs): %s",
                        self.name, fn_name, len(result_s), elapsed, preview)
            return result_s, elapsed, fn_args

        if len(parsed) == 1:
            tc, fn_name, fn_args = parsed[0]
            return [_run_one(tc, fn_name, fn_args)]

        from concurrent.futures import ThreadPoolExecutor

        results: list[tuple[str, float, dict[str, Any]]] = [("", 0.0, {})] * len(parsed)
        with ThreadPoolExecutor(max_workers=len(parsed)) as pool:
            futures = {
                pool.submit(_run_one, tc, fn_name, fn_args): i
                for i, (tc, fn_name, fn_args) in enumerate(parsed)
            }
            for fut in futures:
                idx = futures[fut]
                results[idx] = fut.result()

        return results

    # ------------------------------------------------------------------
    # Core agent loop
    # ------------------------------------------------------------------

    def run_agent_loop(
        self,
        system: str,
        initial_user_content: str | list[dict[str, Any]],
        *,
        max_iterations: int | None = None,
    ) -> str:
        """Execute the tool-use conversation loop. Returns the final text output."""
        max_iters = max_iterations or config.MAX_AGENT_ITERATIONS
        tools = self.get_tools()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": initial_user_content},
        ]

        loop_trace = self._begin_loop_trace(system, initial_user_content)
        last_text = ""
        trace_finalized = False

        try:
            for iteration in range(1, max_iters + 1):
                logger.info("[%s] iteration %d/%d", self.name, iteration, max_iters)

                t0 = time.time()
                resp = completion(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    tools=tools if tools else None,
                )
                elapsed = time.time() - t0

                choice = resp.choices[0]
                assistant_msg = choice.message
                text_content = assistant_msg.content or ""
                if text_content:
                    last_text = text_content

                self._record_metrics(resp, "agent", elapsed, text_content)
                logger.info(
                    "[%s] iter %d | %.1fs | in=%d out=%d | stop=%s",
                    self.name, iteration, elapsed,
                    resp.usage.prompt_tokens, resp.usage.completion_tokens,
                    choice.finish_reason,
                )

                iter_rec = IterationRecord(
                    iteration=iteration,
                    elapsed_sec=elapsed,
                    input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
                    finish_reason=choice.finish_reason or "",
                    assistant_text=text_content or None,
                )

                messages.append(_serialize_assistant_message(assistant_msg))

                # --- Handle tool calls ---
                if assistant_msg.tool_calls:
                    regular_calls = [
                        tc for tc in assistant_msg.tool_calls if tc.function.name != "task_done"
                    ]
                    done_calls = [
                        tc for tc in assistant_msg.tool_calls if tc.function.name == "task_done"
                    ]

                    for batch in _group_tool_calls(regular_calls):
                        batch_results = self._execute_tool_batch(batch)
                        for tc, (result_str, tc_elapsed, fn_args) in zip(batch, batch_results):
                            display_args = {
                                k: (v[:200] + "...") if isinstance(v, str) and len(v) > 200 else v
                                for k, v in fn_args.items()
                            }
                            tc_rec: dict[str, Any] = {
                                "id": tc.id,
                                "name": tc.function.name,
                                "arguments": display_args,
                                "result": result_str[:MAX_TOOL_RESULT_LEN] + ("..." if len(result_str) > MAX_TOOL_RESULT_LEN else ""),
                                "elapsed_sec": round(tc_elapsed, 3),
                            }
                            if result_str.startswith("ERROR:"):
                                tc_rec["diagnostic"] = {
                                    "parsed_argument_keys": sorted(fn_args.keys()),
                                    "raw_arguments_head": (tc.function.arguments or "")[:800],
                                }
                            iter_rec.tool_calls.append(tc_rec)
                            tool_msg: dict[str, Any] = {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result_str,
                            }
                            sig = getattr(tc, "thought_signature", None)
                            if sig:
                                tool_msg["thought_signature"] = sig
                            messages.append(tool_msg)

                    if done_calls:
                        verify_t0 = time.time()
                        passed, errors = self.verify_completion()
                        verify_elapsed = time.time() - verify_t0
                        if passed:
                            result_str = (
                                "VERIFICATION PASSED. Task complete — do not call tools again unless "
                                "addressing new issues."
                            )
                        else:
                            result_str = (
                                "VERIFICATION FAILED. Fix the issues below, then call task_done again "
                                "after saving changes:\n- " + "\n- ".join(errors)
                            )

                        iter_rec.verification = {"passed": passed, "errors": errors}

                        for tc in done_calls:
                            try:
                                fn_args = json.loads(tc.function.arguments)
                            except json.JSONDecodeError:
                                fn_args = {}
                            logger.info(
                                "[%s] tool: task_done(%s)",
                                self.name, _summarize_args("task_done", fn_args),
                            )
                            display_args = {
                                k: (v[:200] + "...") if isinstance(v, str) and len(v) > 200 else v
                                for k, v in fn_args.items()
                            }
                            iter_rec.tool_calls.append({
                                "id": tc.id,
                                "name": "task_done",
                                "arguments": display_args,
                                "result": result_str[:MAX_TOOL_RESULT_LEN] + (
                                    "..." if len(result_str) > MAX_TOOL_RESULT_LEN else ""
                                ),
                                "elapsed_sec": round(verify_elapsed / max(len(done_calls), 1), 3),
                            })
                            tool_msg: dict[str, Any] = {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result_str,
                            }
                            sig = getattr(tc, "thought_signature", None)
                            if sig:
                                tool_msg["thought_signature"] = sig
                            messages.append(tool_msg)

                        if loop_trace:
                            loop_trace.add_iteration(iter_rec)

                        if passed:
                            logger.info(
                                "[%s] verify_completion PASSED via task_done after %d iterations",
                                self.name, iteration,
                            )
                            if loop_trace:
                                loop_trace.finalize()
                                trace_finalized = True
                            return last_text

                        continue

                    if loop_trace:
                        loop_trace.add_iteration(iter_rec)
                    continue

                # --- No tool calls → LLM thinks it's done → verify ---
                passed, errors = self.verify_completion()
                iter_rec.verification = {"passed": passed, "errors": errors}
                if loop_trace:
                    loop_trace.add_iteration(iter_rec)

                if passed:
                    logger.info(
                        "[%s] verify_completion PASSED after %d iterations", self.name, iteration
                    )
                    if loop_trace:
                        loop_trace.finalize()
                        trace_finalized = True
                    return last_text

                feedback = (
                    "VERIFICATION FAILED. The following checks did not pass:\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\n\nPlease fix these issues using the available tools, then call task_done "
                    "(or stop calling tools if task_done is not available) to re-run verification."
                )
                logger.warning("[%s] verify_completion FAILED: %s", self.name, errors)
                messages.append({"role": "user", "content": feedback})

            logger.error("[%s] reached max iterations (%d) without passing verification", self.name, max_iters)
            return last_text
        finally:
            if loop_trace and not trace_finalized:
                loop_trace.finalize()

    # ------------------------------------------------------------------
    # Trace support
    # ------------------------------------------------------------------

    def _begin_loop_trace(
        self, system: str, initial_user_content: str | list[dict[str, Any]]
    ):
        mc = get_metrics()
        if mc is None or mc.trace_writer is None:
            return None
        tw = mc.trace_writer
        return tw.begin_agent_loop(
            agent_name=self.name,
            stage=tw.current_stage,
            page_id=self._current_page_id,
            model=self.model,
            system_prompt=system,
            initial_user_content=initial_user_content,
        )

    # ------------------------------------------------------------------
    # Metrics (mirrors BaseAgent)
    # ------------------------------------------------------------------

    def _record_metrics(self, resp: Any, call_type: str, elapsed: float, raw_text: str) -> None:
        mc = get_metrics()
        if mc is None:
            return
        mc.record_call(
            agent_name=self.name,
            call_type=call_type,
            model=self.model,
            usage=_wrap_usage(resp.usage),
            elapsed_sec=elapsed,
            raw_output=raw_text,
            page_id=self._current_page_id,
        )

    @staticmethod
    def extract_json(text: str) -> Any:
        return _extract_json(text)
