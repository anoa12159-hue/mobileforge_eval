"""
Sequential-Accumulate Baseline — pages generated one-by-one with growing context.

Mimics a human developer building pages incrementally: each subsequent page sees
the code of all previously generated pages, maintaining style consistency through
context accumulation rather than a separate design system.

Tests: consistency via context accumulation, but at the cost of growing token
usage and sequential (non-parallelizable) execution.
"""

from __future__ import annotations

import logging
from pathlib import Path

from designforge import config
from designforge.baselines.common import (
    assemble_app,
    assemble_router,
    assemble_types,
    build_single_image_content,
    call_vlm,
    extract_page_from_file_tag,
    scaffold_and_build,
    write_page_file,
)

logger = logging.getLogger(__name__)

_MAX_PREV_CODE_CHARS = 3000

SYSTEM_PROMPT = """\
You are an expert frontend developer building a multi-page React + TypeScript + \
Tailwind CSS mobile app one page at a time.

You will receive:
1. A design screenshot for the current page
2. Code of previously generated pages (if any)

Requirements:
- Use React + TypeScript (.tsx) with Tailwind CSS
- The component MUST have `export default function ComponentName()`
- Be visually faithful to the design screenshot
- MAINTAIN CONSISTENCY with previously generated pages:
  - Use the same color palette, font sizes, spacing
  - Use the same component patterns and naming conventions
  - Reuse similar Tailwind utility combinations
- Use useNavigate from react-router-dom for navigation between pages
- Import only from 'react' and 'react-router-dom'

Output the code wrapped in a file tag with a descriptive kebab-case filename:
<file path="src/pages/meaningful-name.tsx">
// code here
</file>
Choose a name based on the page content (e.g., bookshelf.tsx, reading.tsx)."""


FIRST_PAGE_TEMPLATE = """\
This is the FIRST page of a mobile app with {total_pages} pages.{desc_section}
Generate the React component. Choose a meaningful kebab-case filename based on \
what you see in the screenshot (e.g., bookshelf.tsx, not page-1.tsx)."""


SUBSEQUENT_PAGE_TEMPLATE = """\
This is page {page_num}/{total_pages} of the mobile app.{desc_section}

Previously generated pages (maintain consistency with these):
{previous_pages_summary}

Generate the React component. Choose a meaningful kebab-case filename based on \
what you see in the screenshot."""


class SequentialBaseline:
    """Sequential page generation with accumulated context."""

    name = "sequential"

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

        desc_section = f"\nApp description: {desc}" if desc else ""

        generated_pages: list[tuple[str, str]] = []

        for idx, img_path in enumerate(image_paths):
            if idx == 0:
                user_text = FIRST_PAGE_TEMPLATE.format(
                    total_pages=len(image_paths),
                    desc_section=desc_section,
                )
            else:
                summary = _build_previous_pages_summary(generated_pages)
                user_text = SUBSEQUENT_PAGE_TEMPLATE.format(
                    page_num=idx + 1,
                    total_pages=len(image_paths),
                    desc_section=desc_section,
                    previous_pages_summary=summary,
                )

            content = build_single_image_content(img_path, user_text)

            logger.info(
                "[sequential] generating page %d/%d (context: %d prev pages)",
                idx + 1, len(image_paths), len(generated_pages),
            )

            resp = call_vlm(
                model=model,
                system=SYSTEM_PROMPT,
                content=content,
                agent_name="sequential",
                page_id=f"page-{idx + 1}",
            )

            pid, code = extract_page_from_file_tag(resp)
            if not pid:
                pid = f"page-{idx + 1}"
            write_page_file(output_dir, pid, code)
            generated_pages.append((pid, code))
            logger.info("[sequential] generated %s (%d chars)", pid, len(code))

        if not generated_pages:
            logger.error("[sequential] no pages generated")
            return False

        ordered_ids = [pid for pid, _ in generated_pages]
        assemble_router(output_dir, ordered_ids, ordered_ids[0])
        assemble_app(output_dir)
        assemble_types(output_dir)

        success, msg = scaffold_and_build(output_dir)
        logger.info("[sequential] build result: %s — %s", success, msg)
        return success


def _build_previous_pages_summary(pages: list[tuple[str, str]]) -> str:
    """Build a summary of previously generated pages for context injection.

    Includes full code for recent pages, truncated for earlier ones.
    """
    parts: list[str] = []
    for i, (pid, code) in enumerate(pages):
        is_recent = i >= len(pages) - 2
        if is_recent:
            display = code
        else:
            display = code[:_MAX_PREV_CODE_CHARS]
            if len(code) > _MAX_PREV_CODE_CHARS:
                display += f"\n// ... ({len(code) - _MAX_PREV_CODE_CHARS} more chars)"
        parts.append(f"--- src/pages/{pid}.tsx ---\n{display}")
    return "\n\n".join(parts)
