"""VLM-as-Judge — call a vision-language model to score design vs implementation.

Extracted from evaluator.evaluate.visual so that testgen has zero dependency
on the evaluator package.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DIMENSION_WEIGHTS = {
    "layout": 0.30,
    "style": 0.25,
    "content": 0.25,
    "consistency": 0.20,
}

_JUDGE_PROMPT = """You are a professional UI design evaluator. Compare the DESIGN screenshot (reference) with the IMPLEMENTATION screenshot (generated code output).

Rate the implementation on four dimensions, each from 1 to 5:

1. **Layout** (30%): Spatial arrangement of elements — positions, sizes, alignment, spacing, grid structure.
   - 5: Pixel-perfect layout match
   - 3: Roughly correct positions, noticeable spacing/size differences
   - 1: Completely different arrangement

2. **Style** (25%): Visual design fidelity — colors, fonts, rounded corners, shadows, icons.
   - 5: Colors, typography and decorative elements match closely
   - 3: Similar color palette but noticeable differences in details
   - 1: Completely different visual style

3. **Content** (25%): Information completeness — text content, images, data items, labels.
   - 5: All visible text/images/items are present and correctly placed
   - 3: Most content present but some missing or placeholder items
   - 1: Major content missing or entirely wrong

4. **Consistency** (20%): Cross-page design coherence — navigation patterns, shared components.
   - 5: Navigation, headers, shared components look identical across pages
   - 3: Shared components exist but have visible inconsistencies
   - 1: No consistent design system visible

Output ONLY a JSON object:
{
  "layout": <1-5>,
  "style": <1-5>,
  "content": <1-5>,
  "consistency": <1-5>,
  "issues": ["<brief issue 1>", "<brief issue 2>", ...]
}"""


def judge_pair(
    design_path: str,
    impl_path: str,
    model: str,
) -> dict[str, Any] | None:
    """Call VLM with both screenshots and parse the judge scores."""
    try:
        from designforge.llm_client import completion
        from designforge.agents.base_agent import encode_image_for_message, extract_json

        design_block = encode_image_for_message(design_path)
        impl_block = encode_image_for_message(impl_path)

        resp = completion(
            model=model,
            messages=[
                {"role": "system", "content": "You are a professional UI design evaluator."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "DESIGN (reference):"},
                        design_block,
                        {"type": "text", "text": "IMPLEMENTATION (generated):"},
                        impl_block,
                        {"type": "text", "text": _JUDGE_PROMPT},
                    ],
                },
            ],
            max_tokens=1024,
        )
        text = resp.choices[0].message.content
        logger.debug("VLM judge raw response: %s", text)

        result = _parse_judge_response(text, extract_json)

        for dim in ("layout", "style", "content", "consistency"):
            val = result.get(dim, 3)
            result[dim] = max(1, min(5, int(val)))

        return result

    except Exception as exc:
        logger.warning("VLM judge call failed: %s", exc)
        return None


def _parse_judge_response(text: str, extract_json_fn) -> dict[str, Any]:
    """Parse VLM judge response with multiple fallback strategies."""
    # Strategy 1: standard extract_json
    try:
        return extract_json_fn(text)
    except Exception:
        pass

    # Strategy 2: fix common JSON issues
    cleaned = text
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last > first:
        cleaned = cleaned[first : last + 1]
    cleaned = cleaned.replace("'", '"')
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    cleaned = re.sub(r'(?<=[{,\n])\s*(\w+)\s*:', r' "\1":', cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Strategy 3: regex extraction of individual scores
    scores: dict[str, Any] = {}
    for dim in ("layout", "style", "content", "consistency"):
        m = re.search(rf'["\']?{dim}["\']?\s*:\s*(\d)', text, re.IGNORECASE)
        if m:
            scores[dim] = int(m.group(1))
    if scores:
        issues_m = re.search(r'issues["\']?\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if issues_m:
            scores["issues"] = re.findall(r'"([^"]+)"', issues_m.group(1))
        else:
            scores["issues"] = []
        logger.info("VLM judge: regex fallback parsed scores: %s", scores)
        return scores

    raise ValueError(f"Cannot parse VLM judge response: {text[:200]}")
