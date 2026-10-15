"""
Trajectory analyser — reads .designforge/traces/*.trace.json files.

Detects phases from update_tasks tool calls, computes token attribution
per iteration, and extracts agent behaviour patterns (write granularity,
self-repair count, read frequency).

Also provides plotting helpers for token composition visualisation:
  - plot_token_composition()            — single run → eval/token_composition.png
  - plot_token_composition_comparison() — multiple runs → one comparison figure
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluator.utils import read_json

logger = logging.getLogger(__name__)

_KNOWN_PHASES = ["analyze", "foundation", "components", "pages", "assembly", "verify"]


def analyze_trajectory(run_dir: str) -> dict | None:
    """Analyse trace files and return trajectory metrics."""
    rd = Path(run_dir)
    traces_dir = rd / ".designforge" / "traces"
    if not traces_dir.is_dir():
        logger.warning("traces/ not found at %s", traces_dir)
        return None

    traces = _load_traces(traces_dir)
    if not traces:
        logger.warning("No trace files found in %s", traces_dir)
        return None

    # Use the longest trace (the main agentic trace)
    trace = max(traces, key=lambda t: len(t.get("iterations", [])))

    iterations = trace.get("iterations", [])
    if not iterations:
        return None

    phase_analysis = _detect_phases(iterations)
    token_attribution = _compute_token_attribution(iterations, trace)
    behavior_patterns = _extract_behavior_patterns(iterations)

    return {
        "phase_analysis": phase_analysis,
        "token_attribution": token_attribution,
        "behavior_patterns": behavior_patterns,
        "total_iterations": len(iterations),
        "total_elapsed_sec": trace.get("total_elapsed_sec", 0),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------
# Trace file loading
# ------------------------------------------------------------------


def _load_traces(traces_dir: Path) -> list[dict]:
    """Recursively find and load all .trace.json files."""
    traces: list[dict] = []
    for f in traces_dir.rglob("*.trace.json"):
        data = read_json(f)
        if data and "iterations" in data:
            traces.append(data)
    return traces


# ------------------------------------------------------------------
# Phase detection from update_tasks tool calls
# ------------------------------------------------------------------


def _detect_phases(iterations: list[dict]) -> dict:
    """Detect phase boundaries from update_tasks calls in tool_calls."""
    phases: list[dict] = []
    current_phase: str | None = None
    phase_start: int = 1
    seen_phases: set[str] = set()

    for it in iterations:
        iter_num = it.get("iteration", 0)
        for tc in it.get("tool_calls", []):
            if tc.get("name") != "update_tasks":
                continue
            args = tc.get("arguments", {})
            updates = args.get("updates", [])
            for upd in updates:
                status = upd.get("status", "")
                phase_id = upd.get("id", "")

                # Detect analyze phase that was already in_progress before traces
                if (status == "done" and phase_id in _KNOWN_PHASES
                        and phase_id not in seen_phases and current_phase is None):
                    phases.append({
                        "id": phase_id,
                        "start_iter": 1,
                        "end_iter": iter_num,
                        "iterations": iter_num,
                    })
                    seen_phases.add(phase_id)
                    continue

                if status == "in_progress" and phase_id in _KNOWN_PHASES:
                    if current_phase and current_phase != phase_id:
                        phases.append({
                            "id": current_phase,
                            "start_iter": phase_start,
                            "end_iter": iter_num - 1,
                            "iterations": max(1, iter_num - phase_start),
                        })
                    current_phase = phase_id
                    phase_start = iter_num
                    seen_phases.add(phase_id)

    # Close the last phase
    if current_phase:
        last_iter = iterations[-1].get("iteration", phase_start) if iterations else phase_start
        phases.append({
            "id": current_phase,
            "start_iter": phase_start,
            "end_iter": last_iter,
            "iterations": max(1, last_iter - phase_start + 1),
        })

    # If no update_tasks found, try artifact-based detection
    if not phases:
        phases = _detect_phases_by_artifact(iterations)

    return {
        "phases": phases,
        "detection_method": "update_tasks" if phases and any(
            p["id"] in _KNOWN_PHASES for p in phases
        ) else "artifact",
        "phase_count": len(phases),
    }


def _detect_phases_by_artifact(iterations: list[dict]) -> list[dict]:
    """Fallback: detect phases from which files are written."""
    phases: list[dict] = []
    current_phase = "analyze"
    phase_start = 1

    for it in iterations:
        iter_num = it.get("iteration", 0)
        detected = _classify_iteration(it)
        if detected != current_phase:
            phases.append({
                "id": current_phase,
                "start_iter": phase_start,
                "end_iter": iter_num - 1,
                "iterations": max(1, iter_num - phase_start),
            })
            current_phase = detected
            phase_start = iter_num

    if current_phase:
        last_iter = iterations[-1].get("iteration", phase_start) if iterations else phase_start
        phases.append({
            "id": current_phase,
            "start_iter": phase_start,
            "end_iter": last_iter,
            "iterations": max(1, last_iter - phase_start + 1),
        })

    return phases


def _classify_iteration(it: dict) -> str:
    """Classify an iteration's phase from its tool calls."""
    tool_calls = it.get("tool_calls", [])
    tool_names = [tc.get("name", "") for tc in tool_calls]
    written_paths = []
    for tc in tool_calls:
        if tc.get("name") == "write_file":
            path = tc.get("arguments", {}).get("path", "")
            written_paths.append(path)

    if "task_done" in tool_names:
        return "verify"
    if "run_command" in tool_names:
        return "verify"

    for p in written_paths:
        if "/components/" in p:
            return "components"
        if "/pages/" in p:
            return "pages"
        if "router.tsx" in p or "App.tsx" in p:
            return "assembly"
        if "types.ts" in p:
            return "foundation"

    if not tool_calls or all(
        tc.get("name") in ("list_files", "read_file", "update_tasks")
        for tc in tool_calls
    ):
        return "analyze"

    return "pages"


# ------------------------------------------------------------------
# Token attribution  (delta-based method)
# ------------------------------------------------------------------

_CHARS_PER_TOKEN = 3.5
_WROTE_CHARS_RE = re.compile(r"wrote\s+(\d+)\s+chars", re.IGNORECASE)

# Per-image token estimates by model family.
# Derived from official vendor formulas, validated against trace data
# using 1080×2376 mobile screenshots as the reference image size.
#
# Claude:  (w×h)/750 with auto-resize → ~1700 tok/img (Opus ≈ 1730, Sonnet ≈ 1705)
# OpenAI:  tile-based (2048→768 shortest side, 512px tiles)
#          GPT-5/5.1: base=70 + tiles×140 → ~1190 tok/img (trace: ~1310 incl. overhead)
# Gemini:  fixed 1120 tok/img standard res (trace: ~363, likely low-res mode)
_IMAGE_TOKENS_BY_FAMILY: dict[str, int] = {
    "claude":  1700,
    "openai":  1300,
    "gemini":  360,
}
_IMAGE_TOKENS_DEFAULT = 1300


def _model_family(model: str) -> str:
    """Map a model name to its provider family."""
    m = model.lower()
    if any(k in m for k in ("claude", "opus", "sonnet", "haiku")):
        return "claude"
    if any(k in m for k in ("gpt", "o1", "o3", "o4")):
        return "openai"
    if any(k in m for k in ("gemini",)):
        return "gemini"
    return "unknown"


def _image_tokens_per_image(model: str) -> int:
    """Return the per-image token estimate for the given model."""
    family = _model_family(model)
    return _IMAGE_TOKENS_BY_FAMILY.get(family, _IMAGE_TOKENS_DEFAULT)


def _chars_to_tokens(n: int) -> float:
    return n / _CHARS_PER_TOKEN


def _count_image_blocks(user: list | None) -> int:
    if not user or not isinstance(user, list):
        return 0
    return sum(1 for b in user if isinstance(b, dict) and b.get("type") == "image_url")


def _sum_user_text_chars(user: list | None) -> int:
    if not user or not isinstance(user, list):
        return 0
    total = 0
    for b in user:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            total += len(text) if isinstance(text, str) else 0
    return total


def _assistant_chars(it: dict) -> int:
    for key in ("assistant_text", "assistant", "assistant_content"):
        v = it.get(key)
        if isinstance(v, str) and v.strip():
            return len(v)
    return 0


def _categorize_iteration(it: dict) -> dict[str, int]:
    """Return character counts per category from one iteration's output."""
    out: dict[str, int] = {
        "assistant_text": _assistant_chars(it),
        "write_file": 0,
        "read_file": 0,
        "list_files": 0,
        "run_command": 0,
        "str_replace": 0,
        "update_tasks": 0,
        "other_tool": 0,
    }
    for tc in it.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name", "")
        result = tc.get("result", "") or ""
        if not isinstance(result, str):
            result = str(result)
        args = tc.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}

        if name == "write_file":
            m = _WROTE_CHARS_RE.search(result)
            if m:
                out["write_file"] += int(m.group(1))
            else:
                c = args.get("content")
                out["write_file"] += len(c) if isinstance(c, str) else 0
        elif name == "read_file":
            out["read_file"] += len(result)
        elif name == "list_files":
            out["list_files"] += len(result)
        elif name == "run_command":
            out["run_command"] += len(result)
        elif name in ("str_replace", "batch_str_replace"):
            old_s = args.get("old_string")
            new_s = args.get("new_string")
            if isinstance(old_s, str) and isinstance(new_s, str) and (old_s or new_s):
                out["str_replace"] += len(old_s) + len(new_s)
            else:
                reps = args.get("replacements")
                if isinstance(reps, list):
                    for r in reps:
                        if isinstance(r, dict):
                            o = r.get("old_string")
                            n = r.get("new_string")
                            if isinstance(o, str) and isinstance(n, str):
                                out["str_replace"] += len(o) + len(n)
                if out["str_replace"] == 0:
                    out["str_replace"] += len(result)
        elif name == "update_tasks":
            out["update_tasks"] += len(result)
        else:
            out["other_tool"] += len(result)
    return out


def _compute_token_attribution(
    iterations: list[dict],
    trace: dict,
) -> dict:
    """Compute per-iteration token attribution using delta-based method.

    For iteration 1: decompose the initial prompt (system + images + user text).
    For iteration i>1: delta = input[i] - input[i-1], attributed to iteration
    i-1's assistant output + tool call contents.
    Cumulative totals are computed at the last iteration's context boundary.
    """
    trace_input = trace.get("input", {})
    system_text = trace_input.get("system", "") or ""
    user_blocks = trace_input.get("user")

    model = trace.get("model", "")

    system_tokens = _chars_to_tokens(len(system_text))
    n_images = _count_image_blocks(user_blocks)
    per_image = _image_tokens_per_image(model)
    image_tokens_est = n_images * per_image
    user_text_tokens = _chars_to_tokens(_sum_user_text_chars(user_blocks))

    # Will be refined in iteration-0 processing when overhead < 0
    image_tokens_final = image_tokens_est

    per_iter: list[dict] = []

    _CONTENT_KEYS = [
        "assistant_text", "write_file", "read_file", "list_files",
        "run_command", "str_replace", "update_tasks", "other_tool",
    ]

    for idx, it in enumerate(iterations):
        iter_num = it.get("iteration", idx + 1)
        input_tokens = it.get("tokens", {}).get("input", 0)
        output_tokens = it.get("tokens", {}).get("output", 0)

        if idx == 0:
            init_est = system_tokens + image_tokens_est + user_text_tokens
            overhead = input_tokens - init_est
            actual_image_tokens = image_tokens_est
            # Safety: if estimate still overshoots, clamp images to residual
            if overhead < 0:
                actual_image_tokens = max(0.0, image_tokens_est + overhead)
                overhead = max(0.0, input_tokens - system_tokens - actual_image_tokens - user_text_tokens)
            image_tokens_final = actual_image_tokens
            breakdown = {
                "system_prompt": round(system_tokens, 1),
                "images": round(actual_image_tokens, 1),
                "user_text": round(user_text_tokens, 1),
                "message_overhead": round(overhead, 1),
            }
            per_iter.append({
                "iteration": iter_num,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "elapsed_sec": round(it.get("elapsed_sec", 0), 2),
                "delta_input": 0,
                "breakdown": breakdown,
            })
        else:
            prev_input = iterations[idx - 1].get("tokens", {}).get("input", 0)
            delta = input_tokens - prev_input
            prev_it = iterations[idx - 1]
            chars = _categorize_iteration(prev_it)
            token_est = {k: round(_chars_to_tokens(chars[k]), 1) for k in _CONTENT_KEYS}
            est_sum = sum(token_est.values())
            overhead = round(delta - est_sum, 1)

            breakdown = {**token_est, "overhead": overhead}
            per_iter.append({
                "iteration": iter_num,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "elapsed_sec": round(it.get("elapsed_sec", 0), 2),
                "delta_input": delta,
                "breakdown": breakdown,
            })

    # Cumulative breakdown at last iteration's context boundary:
    # sum over iters[:-1] because last iter's input does not include its own output.
    cum_chars: dict[str, int] = {k: 0 for k in _CONTENT_KEYS}
    for it in iterations[:-1]:
        c = _categorize_iteration(it)
        for k in _CONTENT_KEYS:
            cum_chars[k] += c[k]

    last_input = iterations[-1].get("tokens", {}).get("input", 0) if iterations else 0
    cum_tokens = {k: round(_chars_to_tokens(v), 1) for k, v in cum_chars.items()}
    cum_tokens["system_prompt"] = round(system_tokens, 1)
    cum_tokens["images"] = round(image_tokens_final, 1)
    cum_tokens["user_text"] = round(user_text_tokens, 1)
    est_total = sum(cum_tokens.values())
    cum_tokens["other_overhead"] = round(last_input - est_total, 1)

    cum_pct: dict[str, float] = {}
    for k, v in cum_tokens.items():
        cum_pct[k] = round(100.0 * v / last_input, 2) if last_input else 0.0

    return {
        "per_iteration": per_iter,
        "cumulative_at_last_iter": {
            "actual_input_tokens": last_input,
            "estimated_tokens": cum_tokens,
            "percentages": cum_pct,
        },
    }


# ------------------------------------------------------------------
# Behaviour patterns
# ------------------------------------------------------------------


def _extract_behavior_patterns(iterations: list[dict]) -> dict:
    """Extract agent behaviour metrics from tool call sequences."""
    write_counts: list[int] = []
    total_reads = 0
    verify_reads = 0
    verify_fail_count = 0
    total_fix_iterations = 0
    in_verify = False
    fix_types: list[str] = []

    for it in iterations:
        tool_calls = it.get("tool_calls", [])
        tool_names = [tc.get("name", "") for tc in tool_calls]

        # Write granularity
        writes_this_iter = sum(1 for n in tool_names if n == "write_file")
        if writes_this_iter > 0:
            write_counts.append(writes_this_iter)

        # Read frequency
        reads = sum(1 for n in tool_names if n == "read_file")
        total_reads += reads

        # Phase tracking for verify
        for tc in tool_calls:
            if tc.get("name") == "update_tasks":
                updates = tc.get("arguments", {}).get("updates", [])
                for upd in updates:
                    if upd.get("id") == "verify" and upd.get("status") == "in_progress":
                        in_verify = True

        if in_verify:
            verify_reads += reads

        # Self-repair detection
        verification = it.get("verification", {})
        if verification and not verification.get("passed", True):
            verify_fail_count += 1
            errors = verification.get("errors", [])
            for err in errors:
                if "tsc" in str(err).lower() or "TS" in str(err):
                    fix_types.append("tsc_error")
                elif "import" in str(err).lower():
                    fix_types.append("missing_import")
                else:
                    fix_types.append("build_error")

        # Count fix iterations: write_file after a run_command in verify phase
        if in_verify and "run_command" in tool_names and "write_file" in tool_names:
            total_fix_iterations += 1

    avg_writes = (
        sum(write_counts) / len(write_counts) if write_counts else 0.0
    )
    max_writes = max(write_counts) if write_counts else 0
    batch_ratio = (
        sum(1 for w in write_counts if w > 1) / len(write_counts)
        if write_counts else 0.0
    )

    return {
        "write_granularity": {
            "avg_files_per_write_call": round(avg_writes, 1),
            "max_files_per_write_call": max_writes,
            "batch_write_ratio": round(batch_ratio, 2),
        },
        "self_repair": {
            "verify_fail_count": verify_fail_count,
            "total_fix_iterations": total_fix_iterations,
            "fix_types": list(set(fix_types)),
        },
        "read_file_frequency": {
            "total_reads": total_reads,
            "reads_in_verify_phase": verify_reads,
            "reads_in_other_phases": total_reads - verify_reads,
        },
    }


# ------------------------------------------------------------------
# Token composition plotting
# ------------------------------------------------------------------

_PLOT_COMPONENTS = [
    ("images",         "#4E79A7"),
    ("system_prompt",  "#F28E2B"),
    ("user_text",      "#76B7B2"),
    ("write_file",     "#E15759"),
    ("read_file",      "#59A14F"),
    ("list_files",     "#EDC948"),
    ("run_command",    "#B07AA1"),
    ("str_replace",    "#FF9DA7"),
    ("assistant_text", "#9C755F"),
    ("update_tasks",   "#BAB0AC"),
    ("other_tool",     "#D4A6C8"),
    ("overhead",       "#CCCCCC"),
]
_PLOT_COLOR = {name: color for name, color in _PLOT_COMPONENTS}
_PLOT_ORDER = [name for name, _ in _PLOT_COMPONENTS]


def _build_cumulative(
    per_iter: list[dict],
) -> tuple[list[int], dict[str, list[float]], list[int]]:
    """Convert per-iteration delta breakdowns into cumulative stacks."""
    iters: list[int] = []
    actual_input: list[int] = []
    cum: dict[str, list[float]] = {k: [] for k in _PLOT_ORDER}
    running: dict[str, float] = {k: 0.0 for k in _PLOT_ORDER}

    for entry in per_iter:
        iters.append(entry["iteration"])
        actual_input.append(entry["input_tokens"])
        bd = entry["breakdown"]

        if "images" in bd:
            running["images"] = bd.get("images", 0)
            running["system_prompt"] = bd.get("system_prompt", 0)
            running["user_text"] = bd.get("user_text", 0)
            running["overhead"] = bd.get("message_overhead", 0)
        else:
            for k in _PLOT_ORDER:
                running[k] += bd.get(k, 0)

        for k in _PLOT_ORDER:
            cum[k].append(running[k])

    return iters, cum, actual_input


_PHASE_COLORS = {
    "analyze":    "#E8F0FE",
    "foundation": "#FFF3E0",
    "components": "#E8F5E9",
    "pages":      "#FCE4EC",
    "assembly":   "#F3E5F5",
    "verify":     "#FFFDE7",
}
_PHASE_COLOR_FALLBACK = "#F5F5F5"


def _plot_single(
    ax: Any,
    per_iter: list[dict],
    title: str,
    phases: list[dict] | None = None,
    x_axis: str = "iteration",
) -> None:
    """Draw a stacked-area chart for one run on the given Axes.

    Args:
        phases: list of {"id", "start_iter", "end_iter"} dicts (from phase_analysis).
        x_axis: "iteration" (plain iter numbers) or "phase" (iter numbers with
                phase-coloured background bands and labels).
    """
    import matplotlib.ticker as mticker

    iters, cum, actual = _build_cumulative(per_iter)
    x = list(range(len(iters)))
    iter_to_x = {it: xi for xi, it in zip(x, iters)}

    # Phase background bands (when x_axis == "phase" and phases are available)
    if x_axis == "phase" and phases:
        for ph in phases:
            x_start = iter_to_x.get(ph["start_iter"])
            x_end = iter_to_x.get(ph["end_iter"])
            if x_start is None or x_end is None:
                continue
            color = _PHASE_COLORS.get(ph["id"], _PHASE_COLOR_FALLBACK)
            ax.axvspan(x_start - 0.5, x_end + 0.5,
                       color=color, alpha=0.45, zorder=0)
            mid_x = (x_start + x_end) / 2
            ax.text(mid_x, 0, ph["id"],
                    ha="center", va="bottom", fontsize=7.5,
                    fontweight="bold", color="#444444",
                    transform=ax.get_xaxis_transform(),
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#cccccc",
                              alpha=0.85, lw=0.5))

    bottoms = [0.0] * len(iters)
    for comp_name in _PLOT_ORDER:
        values = cum[comp_name]
        if max(values) == 0:
            continue
        ax.fill_between(
            x, bottoms, [b + v for b, v in zip(bottoms, values)],
            label=comp_name, color=_PLOT_COLOR[comp_name], alpha=0.85,
            zorder=1,
        )
        bottoms = [b + v for b, v in zip(bottoms, values)]

    ax.plot(x, actual, color="black", linewidth=1.5, linestyle="--",
            label="actual input_tokens", zorder=10)

    ax.set_xticks(x)
    ax.set_xticklabels(iters, fontsize=8)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Tokens")
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}")
    )


def _collect_used_components(trajectories: list[tuple[str, dict]]) -> set[str]:
    used: set[str] = set()
    for _, traj in trajectories:
        for entry in traj.get("token_attribution", {}).get("per_iteration", []):
            for k, v in entry["breakdown"].items():
                norm_k = k if k != "message_overhead" else "overhead"
                if v and v > 0:
                    used.add(norm_k)
    return used


def _make_figure(
    trajectories: list[tuple[str, dict]],
    output_path: Path,
    suptitle: str = "Token Composition by Iteration",
    x_axis: str = "iteration",
) -> None:
    """Build and save the stacked-area figure for one or more runs.

    Args:
        x_axis: "iteration" — plain iteration numbers on x-axis.
                "phase"     — iteration numbers with phase-coloured background bands.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    n = len(trajectories)
    if n == 1:
        cols, rows = 1, 1
    elif n <= 2:
        cols, rows = n, 1
    elif n <= 4:
        cols, rows = 1, n
    else:
        cols = min(n, 2)
        rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(12 * cols, 4.5 * rows), squeeze=False)

    for idx, (label, traj) in enumerate(trajectories):
        r, c = divmod(idx, cols)
        per_iter = traj.get("token_attribution", {}).get("per_iteration", [])
        phases = traj.get("phase_analysis", {}).get("phases")
        _plot_single(axes[r][c], per_iter, label, phases=phases, x_axis=x_axis)

    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].set_visible(False)

    used = _collect_used_components(trajectories)
    handles = []
    for comp_name, color in _PLOT_COMPONENTS:
        if comp_name in used:
            handles.append(Patch(facecolor=color, alpha=0.85, label=comp_name))
    handles.append(plt.Line2D([0], [0], color="black", linestyle="--",
                              linewidth=1.5, label="actual input_tokens"))

    fig.legend(handles=handles, loc="lower center",
               ncol=min(len(handles), 7), fontsize=9,
               bbox_to_anchor=(0.5, -0.01), frameon=True)
    fig.suptitle(suptitle, fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0.06, 1, 0.96])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved token composition plot: %s", output_path)


def plot_token_composition(
    run_dir: str,
    trajectory_data: dict,
    *,
    x_axis: str = "both",
) -> list[Path]:
    """Generate token-composition plot(s) for a single run.

    Args:
        x_axis: "iteration" — one plot with plain iteration x-axis.
                "phase"     — one plot with phase-band overlay.
                "both"      — generate both variants (default).

    Saves to <run_dir>/eval/token_composition.png (and _by_phase.png).
    Returns list of output paths (may be empty on failure).
    """
    rd = Path(run_dir)
    label = rd.name.replace("__", " / ")
    data = [(label, trajectory_data)]
    outputs: list[Path] = []

    modes = ["iteration", "phase"] if x_axis == "both" else [x_axis]
    for mode in modes:
        suffix = "" if mode == "iteration" else "_by_phase"
        out = rd / "eval" / f"token_composition{suffix}.png"
        try:
            _make_figure(data, out, x_axis=mode)
            outputs.append(out)
        except Exception as exc:
            logger.warning("Token composition plot (%s) failed: %s", mode, exc)

    return outputs


def plot_token_composition_comparison(
    run_trajectories: list[tuple[str, dict]],
    output_path: str | Path,
    *,
    x_axis: str = "phase",
) -> Path | None:
    """Generate a comparison plot for multiple runs.

    Args:
        run_trajectories: list of (label, trajectory_data) pairs.
        output_path: where to save the PNG.
        x_axis: "iteration" or "phase" (default "phase" for comparison view).

    Returns the output path, or None if plotting failed.
    """
    if not run_trajectories:
        return None
    try:
        out = Path(output_path)
        _make_figure(run_trajectories, out, x_axis=x_axis)
        return out
    except Exception as exc:
        logger.warning("Token composition comparison plot failed: %s", exc)
        return None
