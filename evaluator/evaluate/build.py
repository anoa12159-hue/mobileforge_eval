"""
Build evaluator — runs tsc and vite build, parses router for route coverage.

Corresponds to benchmark §4.2 build correctness.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from evaluator.utils import find_page_files, parse_routes_from_router

logger = logging.getLogger(__name__)

_TIMEOUT = 120


def evaluate_build(run_dir: str) -> dict:
    """Run tsc --noEmit and vite build in run_dir, compute route/page metrics."""
    rd = Path(run_dir)

    tsc_result = _run_tsc(rd)
    vite_result = _run_vite_build(rd)

    routes = parse_routes_from_router(rd / "src" / "router.tsx")
    page_files = find_page_files(rd / "src")

    build_passed = tsc_result["passed"] and vite_result["passed"]
    dist_exists = (rd / "dist").is_dir()

    return {
        "build_passed": build_passed,
        "tsc_passed": tsc_result["passed"],
        "vite_passed": vite_result["passed"],
        "tsc_error_count": tsc_result["error_count"],
        "tsc_errors": tsc_result["errors"][:20],
        "vite_error_count": vite_result["error_count"],
        "vite_errors": vite_result["errors"][:20],
        "dist_exists": dist_exists,
        "route_count": len(routes),
        "routes": routes,
        "page_count": len(page_files),
        "page_files": [p.name for p in page_files],
        "route_coverage": _compute_route_coverage(routes, page_files),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _run_tsc(run_dir: Path) -> dict:
    """Run npx tsc --noEmit and parse errors."""
    if not (run_dir / "tsconfig.json").is_file():
        return {"passed": False, "error_count": 1, "errors": ["tsconfig.json not found"]}

    try:
        proc = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        errors = _parse_tsc_errors(proc.stdout + proc.stderr)
        return {
            "passed": proc.returncode == 0,
            "error_count": len(errors),
            "errors": errors,
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error_count": 1, "errors": ["tsc timed out"]}
    except FileNotFoundError:
        return {"passed": False, "error_count": 1, "errors": ["npx not found"]}
    except Exception as exc:
        return {"passed": False, "error_count": 1, "errors": [str(exc)]}


def _run_vite_build(run_dir: Path) -> dict:
    """Run npx vite build and parse errors."""
    if not (run_dir / "package.json").is_file():
        return {"passed": False, "error_count": 1, "errors": ["package.json not found"]}

    try:
        proc = subprocess.run(
            ["npx", "vite", "build"],
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        output = proc.stdout + proc.stderr
        errors = []
        if proc.returncode != 0:
            for line in output.splitlines():
                line = line.strip()
                if line and ("error" in line.lower() or "Error" in line):
                    errors.append(line[:200])
            if not errors:
                errors = [output[:500]]
        return {
            "passed": proc.returncode == 0,
            "error_count": len(errors),
            "errors": errors,
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error_count": 1, "errors": ["vite build timed out"]}
    except FileNotFoundError:
        return {"passed": False, "error_count": 1, "errors": ["npx not found"]}
    except Exception as exc:
        return {"passed": False, "error_count": 1, "errors": [str(exc)]}


def _parse_tsc_errors(output: str) -> list[str]:
    """Extract individual tsc error lines."""
    errors: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if "error TS" in line:
            errors.append(line[:200])
    return errors


def _compute_route_coverage(routes: list[str], page_files: list[Path]) -> float:
    """Route coverage: how many page files have corresponding routes.

    Returns 1.0 if all page files are covered, 0.0 if none.
    Falls back to min(routes, pages)/max(routes, pages) if names differ.
    """
    if not page_files:
        return 0.0
    if not routes:
        return 0.0
    return min(len(routes), len(page_files)) / max(len(routes), len(page_files))
