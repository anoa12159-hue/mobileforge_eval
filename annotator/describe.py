"""
Single-screenshot description — Phase 1 of annotation.

Calls VLM with one image + DESCRIBE_SINGLE_PROMPT to produce a structured
multi-dimensional descriptor for page matching.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from designforge.llm_client import completion
from designforge.agents.base_agent import encode_image_for_message, extract_json

from annotator.prompts import DESCRIBE_SINGLE_PROMPT

logger = logging.getLogger(__name__)


def describe_screenshot(image_path: str, model: str) -> dict[str, Any]:
    """Describe a single screenshot using VLM.

    Args:
        image_path: Path to the screenshot image file.
        model: VLM model name for the completion call.

    Returns:
        Structured descriptor dict with page_name, page_type, description,
        keywords, visual_anchors, structural_features.

    Raises:
        RuntimeError: If the VLM call fails after retries or response is unparseable.
    """
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"截图不存在: {image_path}")

    logger.info("[describe] 处理截图: %s", p.name)
    image_block = encode_image_for_message(image_path)

    t0 = time.time()
    resp = completion(
        model=model,
        messages=[
            {"role": "system", "content": "You are a mobile UI analysis expert. Output JSON only."},
            {
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": DESCRIBE_SINGLE_PROMPT},
                ],
            },
        ],
    )
    elapsed = time.time() - t0

    raw_text = resp.choices[0].message.content or ""
    logger.info(
        "[describe] %s 完成 (%.1fs, in=%d out=%d)",
        p.name, elapsed,
        resp.usage.prompt_tokens, resp.usage.completion_tokens,
    )
    logger.debug("[describe] 原始输出:\n%s", raw_text[:500])

    try:
        descriptor = extract_json(raw_text)
    except Exception as e:
        raise RuntimeError(
            f"无法解析 VLM 输出为 JSON (截图: {p.name}): {e}\n原始输出: {raw_text[:300]}"
        ) from e

    descriptor["screenshot"] = p.name
    _validate_descriptor(descriptor, p.name)

    return descriptor


def _validate_descriptor(descriptor: dict, filename: str) -> None:
    """Warn on missing or malformed fields without failing hard."""
    required_fields = ("page_name", "page_type", "description", "keywords", "visual_anchors")
    for field in required_fields:
        if field not in descriptor:
            logger.warning("[describe] %s: 缺少字段 '%s'", filename, field)

    valid_types = {"main_page", "sub_page", "overlay", "state_variant"}
    page_type = descriptor.get("page_type", "")
    if page_type not in valid_types:
        logger.warning(
            "[describe] %s: page_type='%s' 不在合法值 %s 中",
            filename, page_type, valid_types,
        )

    sf = descriptor.get("structural_features")
    if not isinstance(sf, dict):
        logger.warning("[describe] %s: structural_features 缺失或不是 dict", filename)
