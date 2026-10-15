"""
Project quality evaluator — regex-based static analysis of src/.

Measures scale, component reuse, type system usage, and design consistency.
Corresponds to benchmark §4.4.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from evaluator.utils import count_lines, find_component_files, find_page_files, find_src_files

logger = logging.getLogger(__name__)


def evaluate_project_quality(run_dir: str) -> dict:
    """Analyse src/ for scale, component reuse, type system, and design consistency."""
    rd = Path(run_dir)
    src_dir = rd / "src"
    if not src_dir.is_dir():
        return {"error": "src/ directory not found"}

    scale = _evaluate_scale(src_dir)
    component_reuse = _evaluate_component_reuse(src_dir)
    type_system = _evaluate_type_system(src_dir)
    design_consistency = _evaluate_design_consistency(src_dir)

    return {
        "scale": scale,
        "component_reuse": component_reuse,
        "type_system": type_system,
        "design_consistency": design_consistency,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------
# §4.4.1 Scale metrics
# ------------------------------------------------------------------


def _evaluate_scale(src_dir: Path) -> dict:
    files = find_src_files(src_dir)
    loc_per_file: list[tuple[Path, int]] = [(f, count_lines(f)) for f in files]
    total_loc = sum(loc for _, loc in loc_per_file)
    file_count = len(files)

    page_count = len(find_page_files(src_dir))
    comp_count = len(find_component_files(src_dir))
    other_count = file_count - page_count - comp_count

    max_file_loc = 0
    max_file_path = ""
    for f, loc in loc_per_file:
        if loc > max_file_loc:
            max_file_loc = loc
            max_file_path = str(f.relative_to(src_dir.parent))

    return {
        "total_loc": total_loc,
        "file_count": file_count,
        "avg_loc_per_file": round(total_loc / file_count, 1) if file_count else 0,
        "max_file_loc": max_file_loc,
        "max_file_path": max_file_path,
        "directory_distribution": {
            "pages": page_count,
            "components": comp_count,
            "other": other_count,
        },
    }


# ------------------------------------------------------------------
# §4.4.2 Component reuse
# ------------------------------------------------------------------

_IMPORT_RE = re.compile(
    r"""import\s+(?:(?:\{[^}]*\})|(?:\w+))"""
    r"""\s+from\s+['"]\.\.?/components/(\w+)['"]""",
)
_EXPORT_DEFAULT_RE = re.compile(r"export\s+default\s+function\s+(\w+)")


def _evaluate_component_reuse(src_dir: Path) -> dict:
    comp_files = find_component_files(src_dir)
    if not comp_files:
        return {
            "shared_component_count": 0,
            "component_reuse_rate": 0.0,
            "dead_component_rate": 0.0,
            "dead_components": [],
            "max_reuse_count": 0,
            "max_reuse_component": "",
            "details": {},
        }

    # Discover component names from filenames
    component_names: dict[str, Path] = {}
    for cf in comp_files:
        name = cf.stem
        component_names[name] = cf

    # Count imports across all non-component source files
    all_files = find_src_files(src_dir)
    non_comp_files = [f for f in all_files if f not in set(comp_files)]

    usage_count: dict[str, int] = {name: 0 for name in component_names}
    usage_by: dict[str, list[str]] = {name: [] for name in component_names}

    for f in non_comp_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for match in _IMPORT_RE.finditer(text):
            comp_name = match.group(1)
            if comp_name in usage_count:
                usage_count[comp_name] += 1
                rel = str(f.relative_to(src_dir.parent))
                usage_by[comp_name].append(rel)

    total_components = len(component_names)
    dead = [name for name, count in usage_count.items() if count == 0]
    total_usages = sum(usage_count.values())
    max_reuse = max(usage_count.values()) if usage_count else 0
    max_reuse_comp = max(usage_count, key=usage_count.get) if usage_count else ""

    return {
        "shared_component_count": total_components,
        "component_reuse_rate": round(total_usages / total_components, 2) if total_components else 0.0,
        "dead_component_rate": round(len(dead) / total_components, 3) if total_components else 0.0,
        "dead_components": dead,
        "max_reuse_count": max_reuse,
        "max_reuse_component": max_reuse_comp,
        "details": {
            name: {
                "used_by": usage_count[name],
                "file": str(component_names[name].relative_to(src_dir.parent)),
            }
            for name in sorted(component_names)
        },
    }


# ------------------------------------------------------------------
# §4.4.3 Type system
# ------------------------------------------------------------------

_TYPE_DEF_RE = re.compile(
    r"(?:export\s+)?(?:type|interface)\s+(\w+)",
)


def _evaluate_type_system(src_dir: Path) -> dict:
    types_file = src_dir / "types.ts"

    type_names: set[str] = set()
    all_files = find_src_files(src_dir)

    # Gather type definitions
    for f in all_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _TYPE_DEF_RE.finditer(text):
            type_names.add(m.group(1))

    if not type_names:
        return {
            "type_definition_count": 0,
            "type_usage_rate": 0.0,
            "unused_types": [],
        }

    # Count usages (excluding definitions)
    type_used: dict[str, bool] = {name: False for name in type_names}
    for f in all_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for name in type_names:
            usage_pattern = re.compile(
                rf"(?::\s*{re.escape(name)}(?:\s*[<\[\|;,\)\}}])|"
                rf"(?:import.*\b{re.escape(name)}\b)|"
                rf"(?:<{re.escape(name)}[>\s,]))"
            )
            if usage_pattern.search(text):
                type_used[name] = True

    used = sum(1 for v in type_used.values() if v)
    unused = [n for n, v in type_used.items() if not v]

    return {
        "type_definition_count": len(type_names),
        "type_usage_rate": round(used / len(type_names), 2) if type_names else 0.0,
        "unused_types": sorted(unused),
    }


# ------------------------------------------------------------------
# §4.4.4 Design consistency
# ------------------------------------------------------------------

_TAILWIND_COLOR_RE = re.compile(
    r"(?:bg|text|border|ring|fill|stroke)-(?:"
    r"(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green"
    r"|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)"
    r"-\d{2,3}|black|white|transparent)"
)
_SPACING_RE = re.compile(r"(?:p|m|gap|space)-(?:[xy]-)?(\d+(?:\.\d+)?)")


def _evaluate_design_consistency(src_dir: Path) -> dict:
    all_files = find_src_files(src_dir)

    color_classes: dict[str, int] = {}
    spacing_values: list[float] = []

    for f in all_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for m in _TAILWIND_COLOR_RE.finditer(text):
            cls = m.group(0)
            color_classes[cls] = color_classes.get(cls, 0) + 1

        for m in _SPACING_RE.finditer(text):
            try:
                spacing_values.append(float(m.group(1)))
            except ValueError:
                pass

    total_color_usages = sum(color_classes.values())
    unique_colors = len(color_classes)

    # Color consistency: fewer unique colors relative to total usages = more consistent
    color_consistency = 0.0
    if total_color_usages > 0 and unique_colors > 0:
        color_consistency = round(
            1.0 - (unique_colors / max(total_color_usages, 1)), 2
        )
        color_consistency = max(0.0, min(1.0, color_consistency))

    # Spacing consistency: coefficient of variation (lower = more consistent)
    spacing_cv = 0.0
    if spacing_values:
        mean = sum(spacing_values) / len(spacing_values)
        if mean > 0:
            variance = sum((v - mean) ** 2 for v in spacing_values) / len(spacing_values)
            spacing_cv = round((variance ** 0.5) / mean, 2)

    # Navigation consistency: check if BottomNav/TabBar is used across pages
    page_files = find_page_files(src_dir)
    nav_in_pages = 0
    for f in page_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"import.*(?:BottomNav|TabBar|BottomTabBar|Navigation)", text):
                nav_in_pages += 1
        except Exception:
            pass
    nav_consistency = round(nav_in_pages / len(page_files), 2) if page_files else 0.0

    return {
        "color_token_consistency": color_consistency,
        "unique_color_classes": unique_colors,
        "total_color_usages": total_color_usages,
        "spacing_consistency_cv": spacing_cv,
        "navigation_consistency": nav_consistency,
    }
