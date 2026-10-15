"""
Page-Independent Baseline — per-page VLM calls + deterministic assembly.

Each design screenshot is processed independently with zero cross-page context.
A deterministic script then assembles router, App, and minimal shared types.

Tests the penalty of having no shared design system, no data contract, and no
merge step — pure per-page generation quality in isolation.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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

SYSTEM_PROMPT = """\
You are an expert frontend developer. Generate a single React page component \
from the design screenshot.

Requirements:
- Use React + TypeScript (.tsx)
- Use Tailwind CSS utility classes for ALL styling
- The component MUST have `export default function ComponentName()`
- Be visually faithful to the design screenshot
- Mobile-first responsive layout
- Use proper TypeScript types
- Do NOT import from other project files — this page is self-contained
- Import only from 'react' and 'react-router-dom'

Output the code wrapped in a file tag with a descriptive kebab-case filename:
<file path="src/pages/meaningful-name.tsx">
// code here
</file>
Choose a name based on the page content (e.g., bookshelf.tsx, reading.tsx)."""


USER_TEMPLATE = """\
This is page {page_num} of a {total_pages}-page mobile app.{desc_section}
Generate the React component based on the screenshot."""


class PageIndependentBaseline:
    """Per-page independent generation + deterministic assembly."""

    name = "page_independent"

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
        total_pages = len(image_paths)

        generated: dict[str, str] = {}
        index_to_pid: dict[int, str] = {}
        max_workers = min(len(image_paths), config.MAX_PARALLEL_PAGES)

        def generate_one(img_path: str, page_num: int) -> tuple[str, str]:
            user_text = USER_TEMPLATE.format(
                page_num=page_num, total_pages=total_pages,
                desc_section=desc_section,
            )
            content = build_single_image_content(img_path, user_text)
            resp = call_vlm(
                model=model,
                system=SYSTEM_PROMPT,
                content=content,
                agent_name="page_independent",
                page_id=f"page-{page_num}",
            )
            pid, code = extract_page_from_file_tag(resp)
            if not pid:
                pid = f"page-{page_num}"
            return pid, code

        logger.info(
            "[page_independent] generating %d pages in parallel (workers=%d)",
            len(image_paths), max_workers,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(generate_one, img, i + 1): i
                for i, img in enumerate(image_paths)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    pid, code = future.result()
                    generated[pid] = code
                    index_to_pid[idx] = pid
                    write_page_file(output_dir, pid, code)
                    logger.info("[page_independent] generated %s", pid)
                except Exception as e:
                    logger.error("[page_independent] failed page %d: %s", idx + 1, e)

        if not generated:
            logger.error("[page_independent] no pages generated")
            return False

        ordered_ids = [index_to_pid[i] for i in sorted(index_to_pid.keys())]
        assemble_router(output_dir, ordered_ids, ordered_ids[0])
        assemble_app(output_dir)
        assemble_types(output_dir)

        success, msg = scaffold_and_build(output_dir)
        logger.info("[page_independent] build result: %s — %s", success, msg)
        return success
