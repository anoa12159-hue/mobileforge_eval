#!/usr/bin/env python3
"""Plot cumulative token composition across iterations from trajectory.json.

Usage:
    # Single run
    python scripts/plot_token_composition.py output/wxreader-2__agentic__gpt-5/eval/trajectory.json

    # Multiple runs side-by-side
    python scripts/plot_token_composition.py \
        output/wxreader-2__agentic__gpt-5/eval/trajectory.json \
        output/wxreader-2__agentic__claude-opus-4-6/eval/trajectory.json

    # Batch: all trajectory.json under output/
    python scripts/plot_token_composition.py --batch output/
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch

# Unified component ordering and colour palette
_COMPONENTS = [
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
_COLOR = {name: color for name, color in _COMPONENTS}
_ORDER = [name for name, _ in _COMPONENTS]


def load_trajectory(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_cumulative(per_iter: list[dict]) -> tuple[list[int], dict[str, list[float]], list[int]]:
    """Convert per-iteration delta breakdowns into cumulative stacks.

    Returns (iterations, {component: [cumulative values]}, [actual input_tokens]).
    """
    iters: list[int] = []
    actual_input: list[int] = []
    cum: dict[str, list[float]] = {k: [] for k in _ORDER}

    running: dict[str, float] = {k: 0.0 for k in _ORDER}

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
            for k in _ORDER:
                running[k] += bd.get(k, 0)

        for k in _ORDER:
            cum[k].append(running[k])

    return iters, cum, actual_input


def derive_label(path: Path) -> str:
    """Derive a short human label from file path."""
    parts = path.parts
    for p in parts:
        if "__agentic__" in p or "__workflow__" in p:
            return p
    return path.parent.parent.name


def plot_single(ax: plt.Axes, per_iter: list[dict], title: str) -> None:
    iters, cum, actual = build_cumulative(per_iter)
    x = list(range(len(iters)))

    bottoms = [0.0] * len(iters)
    for comp_name in _ORDER:
        values = cum[comp_name]
        if max(values) == 0:
            continue
        ax.fill_between(x, bottoms, [b + v for b, v in zip(bottoms, values)],
                        label=comp_name, color=_COLOR[comp_name], alpha=0.85)
        bottoms = [b + v for b, v in zip(bottoms, values)]

    ax.plot(x, actual, color="black", linewidth=1.5, linestyle="--",
            label="actual input_tokens", zorder=10)

    ax.set_xticks(x)
    ax.set_xticklabels(iters, fontsize=8)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Tokens")
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if v >= 1000 else f"{v:.0f}"))


def make_figure(trajectories: list[tuple[str, dict]], output_path: Path) -> None:
    n = len(trajectories)
    if n <= 2:
        cols, rows = n, 1
    elif n <= 3:
        cols, rows = 1, n
    else:
        cols = min(n, 2)
        rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(12 * cols, 4.5 * rows), squeeze=False)

    for idx, (label, traj) in enumerate(trajectories):
        r, c = divmod(idx, cols)
        ax = axes[r][c]
        per_iter = traj["token_attribution"]["per_iteration"]
        plot_single(ax, per_iter, label)

    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].set_visible(False)

    # Collect only components that actually appear
    used = set()
    for _, traj in trajectories:
        for entry in traj["token_attribution"]["per_iteration"]:
            for k, v in entry["breakdown"].items():
                norm_k = k if k != "message_overhead" else "overhead"
                if v and v > 0:
                    used.add(norm_k)

    handles = []
    for comp_name, color in _COMPONENTS:
        if comp_name in used:
            handles.append(Patch(facecolor=color, alpha=0.85, label=comp_name))
    handles.append(plt.Line2D([0], [0], color="black", linestyle="--",
                              linewidth=1.5, label="actual input_tokens"))

    fig.legend(handles=handles, loc="lower center", ncol=min(len(handles), 7),
               fontsize=9, bbox_to_anchor=(0.5, -0.01), frameon=True)
    fig.suptitle("Token Composition by Iteration", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0.06, 1, 0.96])
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    print(f"Saved: {output_path}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # --batch mode
    if args[0] == "--batch":
        base = Path(args[1]) if len(args) > 1 else Path("output")
        files = sorted(base.rglob("eval/trajectory.json"))
        if not files:
            print(f"No trajectory.json found under {base}")
            sys.exit(1)
    else:
        files = [Path(a) for a in args if Path(a).is_file()]

    trajectories = []
    for f in files:
        try:
            data = load_trajectory(f)
            if "token_attribution" not in data:
                print(f"Skipping {f}: no token_attribution")
                continue
            label = derive_label(f)
            trajectories.append((label, data))
        except Exception as e:
            print(f"Error loading {f}: {e}")

    if not trajectories:
        print("No valid trajectory files found.")
        sys.exit(1)

    out = Path("output/token_composition.png")
    make_figure(trajectories, out)


if __name__ == "__main__":
    main()
