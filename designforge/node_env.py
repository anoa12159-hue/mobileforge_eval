"""
Node.js environment resolver — guarantees the correct Node version for builds.

Design: the scaffold declares REQUIRED_NODE_MAJOR (single source of truth).
This module resolves a matching Node binary at startup and produces a frozen
env dict that run_command() passes to every subprocess.

Resolution order:
  1. Current PATH node — if it already satisfies the requirement, use it.
  2. NVM_DIR/versions/node/ — scan for the highest installed version that
     matches.  Checks $NVM_DIR, then common fallback paths.
  3. If nothing is found, raise NodeEnvironmentError with actionable guidance.

The resolved env is cached for the lifetime of the process via @lru_cache.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from designforge.scaffold import REQUIRED_NODE_MAJOR

logger = logging.getLogger(__name__)

_NVM_FALLBACK_DIRS = ("/opt/nvm", os.path.expanduser("~/.nvm"))


class NodeEnvironmentError(RuntimeError):
    """Raised when no suitable Node.js installation can be found."""


def _parse_node_major(version_string: str) -> int | None:
    m = re.match(r"v(\d+)", version_string.strip())
    return int(m.group(1)) if m else None


def _current_node_major() -> int | None:
    """Return the major version of the node currently on PATH, or None."""
    try:
        out = subprocess.check_output(
            ["node", "--version"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return _parse_node_major(out)
    except Exception:
        return None


def _find_nvm_node_bin() -> str | None:
    """Scan nvm installations for the best matching Node >= REQUIRED_NODE_MAJOR.

    Returns the *bin directory* path (e.g. /opt/nvm/versions/node/v20.18.3/bin)
    or None.
    """
    nvm_dir = os.environ.get("NVM_DIR", "")
    search_roots: list[str] = []
    if nvm_dir:
        search_roots.append(nvm_dir)
    search_roots.extend(_NVM_FALLBACK_DIRS)

    candidates: list[tuple[tuple[int, int, int], Path]] = []

    for root in search_roots:
        versions_dir = Path(root) / "versions" / "node"
        if not versions_dir.is_dir():
            continue
        for entry in versions_dir.iterdir():
            m = re.match(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
            if not m:
                continue
            major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if major < REQUIRED_NODE_MAJOR:
                continue
            bin_dir = entry / "bin"
            if (bin_dir / "node").exists():
                candidates.append(((major, minor, patch), bin_dir))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_version, best_bin = candidates[0]
    logger.info(
        "nvm scan: selected Node v%d.%d.%d at %s",
        *best_version, best_bin,
    )
    return str(best_bin)


@lru_cache(maxsize=1)
def resolve_node_env() -> dict[str, str]:
    """Return an env dict with PATH pointing to a Node >= REQUIRED_NODE_MAJOR.

    Raises NodeEnvironmentError if no suitable Node can be found.
    """
    current = _current_node_major()
    if current is not None and current >= REQUIRED_NODE_MAJOR:
        logger.info("Current Node v%d satisfies requirement (>= %d)", current, REQUIRED_NODE_MAJOR)
        return os.environ.copy()

    logger.warning(
        "Current Node %s does not satisfy >= %d; searching nvm...",
        f"v{current}" if current else "(not found)",
        REQUIRED_NODE_MAJOR,
    )

    nvm_bin = _find_nvm_node_bin()
    if nvm_bin is None:
        raise NodeEnvironmentError(
            f"Node.js >= {REQUIRED_NODE_MAJOR} is required but not found.\n"
            f"Current node: {'v' + str(current) if current else '(not installed)'}.\n"
            f"Fix: install via nvm —\n"
            f"  nvm install {REQUIRED_NODE_MAJOR}\n"
            f"  nvm alias default {REQUIRED_NODE_MAJOR}\n"
        )

    env = os.environ.copy()
    env["PATH"] = nvm_bin + os.pathsep + env.get("PATH", "")
    return env


def get_node_version_display(env: dict[str, str] | None = None) -> str:
    """Return the 'node --version' string using the given (or resolved) env."""
    if env is None:
        env = resolve_node_env()
    try:
        return subprocess.check_output(
            ["node", "--version"], text=True, env=env, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "(unknown)"
