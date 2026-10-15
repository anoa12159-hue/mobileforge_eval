"""
Plan-then-Generate Baseline — global planning phase → parallel page generation.

Phase 1 (Plan): All screenshots → VLM produces a structured plan containing
  design system tokens, navigation graph, and shared TypeScript types.
Phase 2 (Generate): Each page generated independently with the plan as context,
  in parallel — similar to DesignForge's Generate stage but without the
  Map-Reduce decomposition, Contract/Mock stages, or Merge post-processing.

Tests whether a single-step global plan can substitute DesignForge's multi-stage
Map → Reduce → Contract pipeline. The key missing piece is the Merge agent.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from designforge import config
from designforge.baselines.common import (
    assemble_app,
    assemble_router,
    assemble_types,
    build_multi_image_content,
    build_single_image_content,
    call_vlm,
    extract_code_block,
    normalize_page_id,
    scaffold_and_build,
    write_page_file,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Phase 1: Planning
# ======================================================================

PLAN_SYSTEM_PROMPT = """\
You are an expert frontend architect. Analyze these mobile app design screenshots \
and create a comprehensive implementation plan for a React + TypeScript + Tailwind \
CSS project.

The screenshots are labeled [Page 1], [Page 2], etc. in input order.

Output a JSON object with EXACTLY these keys:

{
  "design_system": {
    "colors": {"primary": "#...", "secondary": "#...", "background": "#...", "text": "#..."},
    "font_sizes": {"sm": "text-sm", "base": "text-base", "lg": "text-lg", "xl": "text-xl"},
    "spacing": {"sm": "p-2", "md": "p-4", "lg": "p-6"},
    "border_radius": "rounded-lg",
    "common_patterns": "description of reusable UI patterns across pages"
  },
  "navigation": {
    "pages": [
      {"id": "descriptive-kebab-id", "name": "Human Name", "route": "/descriptive-kebab-id"}
    ],
    "entry_point": "id-of-home-page",
    "edges": [{"from": "page-a-id", "to": "page-b-id", "trigger": "button click"}]
  },
  "shared_types": "TypeScript type definitions as a string (the content of types.ts)"
}

IMPORTANT: List pages in the SAME ORDER as the input screenshots (Page 1 first, \
Page 2 second, etc.). Use descriptive kebab-case IDs based on page content \
(e.g., "bookshelf", "reading", "book-store").

Be thorough. The design system should capture visual consistency across pages.
Navigation should cover all inter-page links visible in the screenshots.
Shared types should define interfaces for data entities visible in the UI.

Output ONLY the JSON object."""


PLAN_USER_TEMPLATE = """\
Analyze these {page_count} design screenshots and create the implementation plan.
{desc_section}
Output the JSON plan now."""


# ======================================================================
# Phase 2: Per-page generation
# ======================================================================

GENERATE_SYSTEM_PROMPT = """\
You are an expert frontend developer implementing a page according to a \
pre-defined design plan.

Requirements:
- Use React + TypeScript (.tsx) with Tailwind CSS
- The component MUST have `export default function ComponentName()`
- Follow the design system tokens from the plan EXACTLY
- Be visually faithful to the design screenshot
- Use useNavigate from react-router-dom for navigation
- Import shared types from '../types' if needed
- Mobile-first responsive layout

Output ONLY the TypeScript code. No explanation, no markdown fences."""


GENERATE_USER_TEMPLATE = """\
Generate the React component for file: src/pages/{page_id}.tsx

DESIGN PLAN:
{plan_summary}

NAVIGATION for this page:
- Route: {page_route}
- Links to: {nav_targets}

Output only the complete TypeScript code."""


class PlanThenGenerateBaseline:
    """Two-phase baseline: global planning then parallel generation."""

    name = "plan_then_generate"

    def run(
        self,
        image_paths: list[str],
        output_dir: str,
        desc: str = "",
        framework: str = "react",
        css_strategy: str = "tailwind",
    ) -> bool:
        model = config.AGENT_MODEL
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # ── Phase 1: Planning ──
        plan = self._run_planning(model, image_paths, desc)
        if plan is None:
            logger.error("[plan_then_generate] planning phase failed")
            return False

        plan_summary = _format_plan_for_prompt(plan)
        nav_info = plan.get("navigation", {})

        plan_pages = nav_info.get("pages", [])
        page_ids = []
        for i in range(len(image_paths)):
            if i < len(plan_pages):
                raw_id = plan_pages[i].get("id", "")
                page_ids.append(normalize_page_id(raw_id) if raw_id else f"page-{i + 1}")
            else:
                page_ids.append(f"page-{i + 1}")

        edges = [
            {"from": normalize_page_id(e.get("from", "")),
             "to": normalize_page_id(e.get("to", "")),
             "trigger": e.get("trigger", "")}
            for e in nav_info.get("edges", [])
        ]

        entry_raw = nav_info.get("entry_point", "")
        entry_point = normalize_page_id(entry_raw) if entry_raw else ""
        if not entry_point or entry_point not in page_ids:
            entry_point = page_ids[0] if page_ids else "home"

        types_code = plan.get("shared_types", "")
        if isinstance(types_code, str) and types_code.strip():
            assemble_types(output_dir, types_code)
        else:
            assemble_types(output_dir)

        # ── Phase 2: Parallel page generation ──
        generated = self._run_generation(
            model, image_paths, page_ids, plan_summary, edges, output_dir
        )

        if not generated:
            logger.error("[plan_then_generate] no pages generated")
            return False

        ordered_ids = [pid for pid in page_ids if pid in generated]
        if entry_point not in ordered_ids and ordered_ids:
            entry_point = ordered_ids[0]
        assemble_router(output_dir, ordered_ids, entry_point)
        assemble_app(output_dir)

        success, msg = scaffold_and_build(output_dir)
        logger.info("[plan_then_generate] build result: %s — %s", success, msg)
        return success

    def _run_planning(
        self, model: str, image_paths: list[str], desc: str
    ) -> dict[str, Any] | None:
        """Phase 1: send all images → get structured plan."""
        desc_section = f"\nApp description: {desc}" if desc else ""
        user_text = PLAN_USER_TEMPLATE.format(
            page_count=len(image_paths), desc_section=desc_section
        )
        content = build_multi_image_content(image_paths, user_text)

        logger.info(
            "[plan_then_generate] Phase 1: planning with %d images", len(image_paths)
        )
        resp = call_vlm(
            model=model,
            system=PLAN_SYSTEM_PROMPT,
            content=content,
            agent_name="plan_then_generate_plan",
        )

        try:
            from designforge.agents.base_agent import extract_json
            plan = extract_json(resp)
            if not isinstance(plan, dict):
                raise ValueError("plan is not a dict")
            logger.info("[plan_then_generate] planning succeeded, keys: %s", list(plan.keys()))
            return plan
        except Exception as e:
            logger.error("[plan_then_generate] failed to parse plan: %s", e)
            return None

    def _run_generation(
        self,
        model: str,
        image_paths: list[str],
        page_ids: list[str],
        plan_summary: str,
        edges: list[dict[str, str]],
        output_dir: str,
    ) -> dict[str, str]:
        """Phase 2: generate each page in parallel with plan context."""
        generated: dict[str, str] = {}
        max_workers = min(len(image_paths), config.MAX_PARALLEL_PAGES)

        def generate_one(img_path: str, pid: str) -> tuple[str, str]:
            nav_targets = [
                e["to"] for e in edges
                if e.get("from") == pid
            ]
            user_text = GENERATE_USER_TEMPLATE.format(
                page_id=pid,
                plan_summary=plan_summary,
                page_route=f"/{pid}",
                nav_targets=", ".join(nav_targets) if nav_targets else "none",
            )
            content = build_single_image_content(img_path, user_text)
            resp = call_vlm(
                model=model,
                system=GENERATE_SYSTEM_PROMPT,
                content=content,
                agent_name="plan_then_generate_page",
                page_id=pid,
            )
            return pid, extract_code_block(resp)

        logger.info(
            "[plan_then_generate] Phase 2: generating %d pages (workers=%d)",
            len(image_paths), max_workers,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(generate_one, img, pid): pid
                for img, pid in zip(image_paths, page_ids)
            }
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    pid_result, code = future.result()
                    generated[pid_result] = code
                    write_page_file(output_dir, pid_result, code)
                    logger.info("[plan_then_generate] generated %s", pid_result)
                except Exception as e:
                    logger.error("[plan_then_generate] failed %s: %s", pid, e)

        return generated


def _format_plan_for_prompt(plan: dict[str, Any]) -> str:
    """Format the plan as a concise string for injection into page prompts."""
    parts: list[str] = []

    ds = plan.get("design_system", {})
    if ds:
        parts.append("Design System:")
        if "colors" in ds:
            parts.append(f"  Colors: {json.dumps(ds['colors'])}")
        if "font_sizes" in ds:
            parts.append(f"  Font sizes: {json.dumps(ds['font_sizes'])}")
        if "spacing" in ds:
            parts.append(f"  Spacing: {json.dumps(ds['spacing'])}")
        if "border_radius" in ds:
            parts.append(f"  Border radius: {ds['border_radius']}")
        if "common_patterns" in ds:
            parts.append(f"  Patterns: {ds['common_patterns']}")

    nav = plan.get("navigation", {})
    if nav:
        pages = nav.get("pages", [])
        if pages:
            page_list = ", ".join(
                f"{p.get('id', '?')} ({p.get('name', '?')})" for p in pages
            )
            parts.append(f"\nPages: {page_list}")
        parts.append(f"Entry point: {nav.get('entry_point', '?')}")

    return "\n".join(parts)
