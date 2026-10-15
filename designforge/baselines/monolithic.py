"""
Monolithic Baseline — single VLM call generates the entire project.

All design screenshots are sent in one request. The model outputs all source
files at once. No cross-stage pipeline, no tool loop, no iterative refinement.

Tests the upper bound of single-shot generation and how it degrades as page
count grows (context window pressure, output token limits).
"""

from __future__ import annotations

import logging
from pathlib import Path

from designforge import config
from designforge.baselines.common import (
    assemble_app,
    assemble_router,
    assemble_types,
    build_multi_image_content,
    call_vlm,
    derive_page_id,
    filter_scaffold_conflicts,
    parse_files_from_response,
    scaffold_and_build,
    write_page_file,
    write_source_files,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert frontend developer. You will receive multiple design screenshots \
of a mobile app. Generate a complete React + TypeScript + Tailwind CSS project.

Output EVERY source file using this exact format for EACH file:

<file path="src/pages/example.tsx">
// file content here
</file>

You MUST generate:
1. src/pages/*.tsx — One page component per design screenshot. Each MUST have \
`export default function ComponentName()`. Use Tailwind CSS classes for styling.
2. src/router.tsx — React Router v7 config connecting all pages. Use \
createBrowserRouter from react-router-dom.
3. src/App.tsx — Root component rendering the router.
4. src/types.ts — Shared TypeScript interfaces used across pages.

Requirements:
- Use TypeScript with proper type annotations
- Use Tailwind CSS utility classes for ALL styling (no inline styles, no CSS files)
- Maintain consistent design tokens (colors, fonts, spacing) across all pages
- Include navigation between pages via react-router-dom useNavigate/Link
- Each page must be visually faithful to its design screenshot
- Mobile-first responsive layout

Name each page file with a descriptive kebab-case name based on its content \
(e.g., bookshelf.tsx, reading.tsx), NOT generic names like page-1.tsx.

Output ONLY the <file> blocks. No other text."""


USER_TEMPLATE = """\
Generate the complete React project for this mobile app.
{desc_section}
The app has {page_count} pages. Generate all source files now."""


class MonolithicBaseline:
    """Single-shot VLM call to generate the entire project."""

    name = "monolithic"

    def run(
        self,
        image_paths: list[str],
        output_dir: str,
        desc: str = "",
        framework: str = "react",
        css_strategy: str = "tailwind",
    ) -> bool:
        model = config.AGENT_MODEL
        page_ids = [derive_page_id(p) for p in image_paths]

        desc_section = f"\nApp description: {desc}\n" if desc else ""
        user_text = USER_TEMPLATE.format(
            desc_section=desc_section,
            page_count=len(image_paths),
        )

        content = build_multi_image_content(image_paths, user_text)

        logger.info(
            "[monolithic] sending %d images to VLM in single call", len(image_paths)
        )
        response = call_vlm(
            model=model,
            system=SYSTEM_PROMPT,
            content=content,
            agent_name="monolithic",
        )

        files = parse_files_from_response(response)

        if not files:
            logger.error("[monolithic] failed to parse any files from response")
            return False

        logger.info("[monolithic] parsed %d files from response", len(files))

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        generated_page_ids: list[str] = []
        other_files: dict[str, str] = {}

        for rel_path, code in files.items():
            norm = rel_path.lstrip("./")
            if norm.startswith("src/pages/") and norm.endswith(".tsx"):
                pid = Path(norm).stem
                write_page_file(output_dir, pid, code)
                generated_page_ids.append(pid)
            else:
                other_files[norm] = code

        other_files = filter_scaffold_conflicts(other_files)
        if other_files:
            write_source_files(output_dir, other_files)

        has_router = any("router" in f.lower() for f in files)
        has_app = any(f.endswith("App.tsx") for f in files)

        if not has_router:
            entry = generated_page_ids[0] if generated_page_ids else page_ids[0]
            assemble_router(output_dir, generated_page_ids or page_ids, entry)
        if not has_app:
            assemble_app(output_dir)
        if not any("types" in f for f in files):
            assemble_types(output_dir)

        success, msg = scaffold_and_build(output_dir)
        logger.info("[monolithic] build result: %s — %s", success, msg)
        return success
