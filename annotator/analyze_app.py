"""
Global app analysis — Phase 2 of annotation.

Sends ALL screenshots + their Phase 1 descriptors to VLM in a single call
to extract app-level metadata: app name, category, tab structure, page
relationships.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from designforge.llm_client import completion
from designforge.agents.base_agent import encode_image_for_message, extract_json

from annotator.prompts import ANALYZE_APP_PROMPT

logger = logging.getLogger(__name__)


def analyze_app(
    image_paths: list[str],
    descriptors: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    """Analyze all screenshots together for global app metadata.

    Args:
        image_paths: Ordered list of screenshot file paths.
        descriptors: Phase 1 descriptors (one per image, same order).
        model: VLM model name for the completion call.

    Returns:
        Global metadata dict with app_name, category, tab_structure,
        page_relationships, etc.

    Raises:
        RuntimeError: If the VLM call fails or response is unparseable.
    """
    if not image_paths:
        raise ValueError("image_paths 为空，至少需要一张截图")

    logger.info("[analyze] 开始全局分析 (%d 张截图)", len(image_paths))

    descriptors_json = json.dumps(descriptors, ensure_ascii=False, indent=2)
    user_prompt = ANALYZE_APP_PROMPT.replace("{descriptors_json}", descriptors_json)

    content_blocks: list[dict[str, Any]] = []
    for path in image_paths:
        content_blocks.append(encode_image_for_message(path))
    content_blocks.append({"type": "text", "text": user_prompt})

    t0 = time.time()
    resp = completion(
        model=model,
        messages=[
            {"role": "system", "content": "You are a mobile app analysis expert. Output JSON only."},
            {"role": "user", "content": content_blocks},
        ],
    )
    elapsed = time.time() - t0

    raw_text = resp.choices[0].message.content or ""
    logger.info(
        "[analyze] 全局分析完成 (%.1fs, in=%d out=%d)",
        elapsed, resp.usage.prompt_tokens, resp.usage.completion_tokens,
    )
    logger.debug("[analyze] 原始输出:\n%s", raw_text[:500])

    try:
        analysis = extract_json(raw_text)
    except Exception as e:
        raise RuntimeError(
            f"无法解析全局分析 VLM 输出为 JSON: {e}\n原始输出: {raw_text[:300]}"
        ) from e

    _validate_analysis(analysis)
    return analysis


def _validate_analysis(analysis: dict) -> None:
    """Warn on missing fields in global analysis."""
    for field in ("app_name", "category", "tab_structure"):
        if field not in analysis:
            logger.warning("[analyze] 全局分析缺少字段 '%s'", field)

    tab = analysis.get("tab_structure")
    if tab is not None and not isinstance(tab, list):
        logger.warning("[analyze] tab_structure 应为 list，实际为 %s", type(tab).__name__)

    rels = analysis.get("page_relationships")
    if rels is not None and not isinstance(rels, list):
        logger.warning("[analyze] page_relationships 应为 list，实际为 %s", type(rels).__name__)
