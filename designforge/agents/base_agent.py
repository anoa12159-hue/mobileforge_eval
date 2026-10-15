"""
Base agent class — model-agnostic via unified llm_client.

Pure-LLM agents (Map / Reduce / Contract) use call_llm / call_vlm.
Tool-using agents (Generate / Merge / Build) inherit from ToolAgent instead.

Every call automatically records token usage + raw output via MetricsCollector.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from designforge import config
from designforge.llm_client import completion
from designforge.metrics import get_metrics

logger = logging.getLogger(__name__)


def extract_json(text: str) -> Any:
    """Best-effort extraction of a JSON object/array from LLM output.

    Handles code-fenced JSON, bare JSON, and truncated fences gracefully.
    """
    text = text.strip()
    for fence in ("```json", "```"):
        if fence in text:
            start = text.index(fence) + len(fence)
            try:
                end = text.index("```", start)
                text = text[start:end].strip()
            except ValueError:
                text = text[start:].strip()
            break
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            return json.loads(text[first : last + 1])
        first = text.find("[")
        last = text.rfind("]")
        if first != -1 and last > first:
            return json.loads(text[first : last + 1])
        raise


_MAX_IMAGE_DIMENSION = 2000


def _resize_image_if_needed(raw_bytes: bytes, max_dim: int = _MAX_IMAGE_DIMENSION) -> tuple[bytes, str]:
    """Resize image so its longest side <= max_dim. Returns (bytes, media_type).

    Uses Pillow for decoding/resizing; falls back to original bytes if Pillow
    is unavailable or the image is already within limits.
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if max(w, h) <= max_dim:
            fmt = img.format or "PNG"
            media_type = {"JPEG": "image/jpeg", "PNG": "image/png",
                          "GIF": "image/gif", "WEBP": "image/webp"}.get(fmt, "image/png")
            return raw_bytes, media_type

        scale = max_dim / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        logger.info("[image] resizing %dx%d -> %dx%d (max_dim=%d)", w, h, new_w, new_h, max_dim)

        if img.mode in ("RGBA", "P"):
            out_fmt, media_type = "PNG", "image/png"
        else:
            out_fmt, media_type = "JPEG", "image/jpeg"

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format=out_fmt, quality=90)
        return buf.getvalue(), media_type
    except ImportError:
        logger.warning("[image] Pillow not installed — skipping resize")
        return raw_bytes, "image/png"


def encode_image_for_message(path: str) -> dict[str, Any]:
    """Build a LiteLLM-compatible image_url content block from a file path.

    Automatically down-scales images whose longest side exceeds 2000 px to
    stay within Claude's many-image request limit.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    raw_bytes = p.read_bytes()
    resized_bytes, media_type = _resize_image_if_needed(raw_bytes)

    if media_type == "image/png" and suffix in media_map:
        media_type = media_map[suffix]

    data = base64.standard_b64encode(resized_bytes).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


class BaseAgent:
    """Model-agnostic base class for all LLM/VLM calls."""

    name: str = "base"

    def __init__(self, model: str | None = None, max_tokens: int | None = None):
        self.model = model or config.TEXT_MODEL
        self.max_tokens = max_tokens or config.get_max_tokens(self.model)
        self._current_page_id: str = ""

    # ------------------------------------------------------------------
    # Core LLM calls
    # ------------------------------------------------------------------

    def call_llm(self, system: str, user_prompt: str) -> str:
        """Text-only LLM call with token tracking."""
        logger.info("[%s] calling LLM (text-only) ...", self.name)
        t0 = time.time()
        resp = completion(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        )
        elapsed = time.time() - t0
        text = resp.choices[0].message.content

        self._record_metrics(
            resp, "llm", elapsed, text,
            system_prompt=system, user_prompt=user_prompt,
        )
        logger.info(
            "[%s] LLM responded in %.1fs | in=%d out=%d",
            self.name, elapsed,
            resp.usage.prompt_tokens, resp.usage.completion_tokens,
        )
        return text

    def call_vlm(self, system: str, image_path: str, user_prompt: str) -> str:
        """Vision-language model call with a single image."""
        logger.info("[%s] calling VLM with image %s ...", self.name, image_path)
        image_block = encode_image_for_message(image_path)
        t0 = time.time()
        resp = completion(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        image_block,
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
        )
        elapsed = time.time() - t0
        text = resp.choices[0].message.content

        self._record_metrics(
            resp, "vlm", elapsed, text,
            system_prompt=system, user_prompt=user_prompt, image_path=image_path,
        )
        logger.info(
            "[%s] VLM responded in %.1fs | in=%d out=%d",
            self.name, elapsed,
            resp.usage.prompt_tokens, resp.usage.completion_tokens,
        )
        return text

    # ------------------------------------------------------------------
    # Metrics integration
    # ------------------------------------------------------------------

    def _record_metrics(
        self,
        resp: Any,
        call_type: str,
        elapsed: float,
        raw_text: str,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        image_path: str = "",
    ) -> None:
        mc = get_metrics()
        if mc is None:
            return
        usage = resp.usage
        mc.record_call(
            agent_name=self.name,
            call_type=call_type,
            model=self.model,
            usage=_wrap_usage(usage),
            elapsed_sec=elapsed,
            raw_output=raw_text or "",
            page_id=self._current_page_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=image_path,
        )

    # ------------------------------------------------------------------
    # JSON extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def extract_json(text: str) -> Any:
        """Best-effort extraction of a JSON object from LLM output."""
        return extract_json(text)

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    def call_llm_with_retry(
        self, system: str, user_prompt: str, *, retries: int | None = None
    ) -> str:
        retries = retries if retries is not None else config.MAX_RETRIES
        last_err: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                return self.call_llm(system, user_prompt)
            except Exception as e:
                last_err = e
                logger.warning("[%s] attempt %d failed: %s", self.name, attempt, e)
                if attempt <= retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"[{self.name}] all {retries + 1} attempts failed") from last_err

    def call_vlm_with_retry(
        self, system: str, image_path: str, user_prompt: str, *, retries: int | None = None
    ) -> str:
        retries = retries if retries is not None else config.MAX_RETRIES
        last_err: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                return self.call_vlm(system, image_path, user_prompt)
            except Exception as e:
                last_err = e
                logger.warning("[%s] attempt %d failed: %s", self.name, attempt, e)
                if attempt <= retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"[{self.name}] all {retries + 1} attempts failed") from last_err


class _UsageAdapter:
    """Adapt usage objects to MetricsCollector's expected interface."""

    def __init__(self, usage: Any):
        self.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0


def _wrap_usage(usage: Any) -> _UsageAdapter:
    return _UsageAdapter(usage)
