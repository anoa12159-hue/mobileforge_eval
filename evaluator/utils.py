"""
Shared utilities for the evaluator package.

Provides filesystem helpers, router parsing, and atomic JSON I/O.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Atomic JSON write
# ------------------------------------------------------------------


def atomic_json_write(path: Path, data: Any) -> None:
    """Write *data* as pretty-printed JSON with atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=f".{path.stem}_"
    )
    closed = False
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.replace(tmp, str(path))
    except BaseException:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    """Read a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read JSON from %s: %s", path, exc)
        return None


# ------------------------------------------------------------------
# Router parsing
# ------------------------------------------------------------------

_ROUTE_PATH_RE = re.compile(r"""path\s*:\s*['"]([^'"]+)['"]""")


def parse_routes_from_router(router_path: Path) -> list[str]:
    """Extract route paths from a router.tsx file via regex."""
    if not router_path.is_file():
        return []
    text = router_path.read_text(encoding="utf-8")
    return _ROUTE_PATH_RE.findall(text)


# ------------------------------------------------------------------
# Page file discovery
# ------------------------------------------------------------------


def find_page_files(src_dir: Path) -> list[Path]:
    """Return all .tsx files under src/pages/."""
    pages_dir = src_dir / "pages"
    if not pages_dir.is_dir():
        return []
    return sorted(p for p in pages_dir.iterdir() if p.suffix == ".tsx")


def find_component_files(src_dir: Path) -> list[Path]:
    """Return all .tsx files under src/components/."""
    comp_dir = src_dir / "components"
    if not comp_dir.is_dir():
        return []
    return sorted(p for p in comp_dir.iterdir() if p.suffix == ".tsx")


def find_src_files(src_dir: Path, extensions: frozenset[str] | None = None) -> list[Path]:
    """Recursively find source files under *src_dir*.

    Skips node_modules, dist, .designforge, .git.
    """
    extensions = extensions or frozenset({".ts", ".tsx", ".js", ".jsx"})
    skip_dirs = {"node_modules", "dist", ".designforge", ".git", ".vite"}
    results: list[Path] = []
    if not src_dir.is_dir():
        return results
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            p = Path(root) / f
            if p.suffix in extensions:
                results.append(p)
    return sorted(results)


# ------------------------------------------------------------------
# Line counting
# ------------------------------------------------------------------


def count_lines(path: Path) -> int:
    """Count non-empty lines in a file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return sum(1 for line in text.splitlines() if line.strip())
    except Exception:
        return 0


# ------------------------------------------------------------------
# Input dir inference
# ------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent


def infer_input_dir(run_dir: Path, explicit: str | None = None) -> Path | None:
    """Best-effort inference of the input directory for a run.

    Priority: explicit > metrics.json field > directory name heuristic.
    """
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p

    metrics_path = run_dir / ".designforge" / "metrics.json"
    if metrics_path.is_file():
        data = read_json(metrics_path)
        if data:
            input_dir_str = (data.get("experiment") or {}).get("input_dir")
            if input_dir_str:
                p = Path(input_dir_str)
                if p.is_dir():
                    return p

    dir_name = run_dir.name
    app_name = dir_name.split("__")[0] if "__" in dir_name else dir_name
    candidate = _PROJECT_ROOT / "input" / app_name
    if candidate.is_dir():
        return candidate

    return None


# ------------------------------------------------------------------
# Model tag sanitisation
# ------------------------------------------------------------------


def sanitize_model_tag(model_name: str) -> str:
    """Turn a model name into a filesystem-safe short tag."""
    tag = model_name.rsplit("/", 1)[-1]
    return tag.replace(":", "-").replace(" ", "-")
