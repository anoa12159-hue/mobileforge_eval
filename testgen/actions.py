"""Action execution strategies for Playwright pages.

Each strategy attempts to locate and click a target element on the page.
Returns True if the click was performed, False otherwise.

Strategy fallback chain:
  tab_click / text_match / dom_selector → (all fail) → vlm_coordinate
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

_CLICK_TIMEOUT = 3000  # ms to wait for element to appear
_VLM_CLICK_MODEL: str | None = None  # set by runner before execution


def set_vlm_model(model: str | None) -> None:
    """Configure the VLM model used for coordinate-based click fallback."""
    global _VLM_CLICK_MODEL
    _VLM_CLICK_MODEL = model


def execute_action(page: Page, action_dict: dict) -> bool:
    """Dispatch to the appropriate strategy based on action_dict['strategy'].

    If the primary strategy fails and a VLM model is configured, falls back to
    vlm_coordinate as a last resort.
    """
    strategy = action_dict.get("strategy", "")
    handlers = {
        "tab_click": _execute_tab_click,
        "text_match": _execute_text_match,
        "dom_selector": _execute_dom_selector,
        "vlm_coordinate": _execute_vlm_coordinate,
    }
    handler = handlers.get(strategy)
    if handler is None:
        logger.warning("Unknown action strategy: %s", strategy)
        return False

    result = handler(page, action_dict)
    if result:
        return True

    # Fallback: try VLM coordinate if the primary strategy wasn't already vlm_coordinate
    if strategy != "vlm_coordinate" and _VLM_CLICK_MODEL:
        logger.info(
            "Primary strategy '%s' failed, falling back to VLM coordinate for: %s",
            strategy, action_dict.get("description"),
        )
        return _execute_vlm_coordinate(page, action_dict)

    return False


def _execute_tab_click(page: Page, action: dict) -> bool:
    """Click a tab bar item using a 4-level fallback chain.

    1. ``target_route`` → ``a[href=route]`` / ``[data-to=route]``
       (runner injects this for tab_navigation cases; it's the strongest
       signal because the route comes directly from the resolver).
    2. ``dom_selector_hints`` from the generated test case.
    3. ``text_hints`` from the generated test case.
    4. ``tab_position`` within a tab-bar-ish container, via recursive
       role-based lookup (``get_by_role("button").nth(N-1)``). Unlike
       the old ``> *:nth-child`` selectors this works on nested
       ``<div><div><button>`` layouts, which modern Tailwind-style
       React tabbars overwhelmingly use.

    The legacy ``nth-child`` selectors in the test case JSON are
    preserved as the level-2 hints; they still win when they match,
    so this change is purely additive for older fixtures.
    """
    target_route = action.get("target_route")
    if target_route:
        for selector in (
            f"a[href='{target_route}']",
            f"a[href='#{target_route}']",
            f"[data-to='{target_route}']",
            f"[data-path='{target_route}']",
        ):
            if _try_click_selector(page, selector):
                return True

    for selector in action.get("dom_selector_hints", []):
        if _try_click_selector(page, selector):
            return True

    for text in action.get("text_hints", []):
        if _try_click_by_text(page, text):
            return True

    tab_pos = action.get("tab_position")
    if tab_pos is not None and _try_click_tab_by_position(page, tab_pos):
        return True

    logger.warning("tab_click failed for action: %s", action.get("description"))
    return False


_TABBAR_CONTAINER_SELECTORS: tuple[str, ...] = (
    "[class*='fixed'][class*='bottom']",
    "[class*='tabbar']",
    "[class*='tab-bar']",
    "[class*='bottom-nav']",
    "[class*='bottomnav']",
    "[role='tablist']",
    "nav",
    "footer",
)


def _try_click_tab_by_position(page: Page, tab_pos: int) -> bool:
    """Find the tab-bar container and click its N-th ``button``/``link``.

    Uses Playwright's role-based ``nth()`` which searches recursively,
    so wrapper ``<div>`` layers don't break the count. Clicks on the
    element's geometric center, avoiding the "click lands in padding"
    problem that ``page.mouse.click(x, y)`` suffers from.
    """
    for container_sel in _TABBAR_CONTAINER_SELECTORS:
        try:
            container = page.locator(container_sel).last
            for role in ("button", "link"):
                try:
                    target = container.get_by_role(role).nth(tab_pos - 1)
                    if target.is_visible(timeout=1000):
                        target.click(timeout=_CLICK_TIMEOUT)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _execute_text_match(page: Page, action: dict) -> bool:
    """Click an element matching text hints."""
    for selector in action.get("dom_selector_hints", []):
        if _try_click_selector(page, selector):
            return True

    for text in action.get("text_hints", []):
        if _try_click_by_text(page, text):
            return True

    logger.warning("text_match failed for action: %s", action.get("description"))
    return False


def _execute_dom_selector(page: Page, action: dict) -> bool:
    """Click using explicit DOM selectors."""
    for selector in action.get("dom_selector_hints", []):
        if _try_click_selector(page, selector):
            return True
    return False


def _execute_vlm_coordinate(page: Page, action: dict) -> bool:
    """Use VLM to identify click coordinates from a screenshot + action description."""
    if not _VLM_CLICK_MODEL:
        logger.info("vlm_coordinate: no VLM model configured, skipping: %s", action.get("description"))
        return False

    description = action.get("description", "")
    logger.info("vlm_coordinate: asking VLM to locate '%s'", description)

    try:
        coords = _vlm_get_click_coords(page, description)
        if coords is None:
            logger.warning("vlm_coordinate: VLM returned no coordinates")
            return False

        x, y = coords
        viewport = page.viewport_size
        vw = viewport["width"] if viewport else 390
        vh = viewport["height"] if viewport else 844

        if not (0 <= x <= vw and 0 <= y <= vh):
            logger.warning("vlm_coordinate: coordinates (%d, %d) out of viewport (%d×%d)", x, y, vw, vh)
            return False

        page.mouse.click(x, y)
        logger.info("vlm_coordinate: clicked at (%d, %d)", x, y)
        return True

    except Exception as exc:
        logger.warning("vlm_coordinate failed: %s", exc)
        return False


_VLM_CLICK_PROMPT = """Given a screenshot of a mobile app page, identify the pixel coordinates where I should click to perform the described action.

The screenshot is captured at viewport size {width}×{height} with device_scale_factor=2, so the actual image dimensions are {img_width}×{img_height} pixels.

Instructions:
1. Identify the target UI element (button, icon, text, or interactive area)
2. Return the CENTER coordinates of that element in VIEWPORT space ({width}×{height})
3. If the target has visible text, confirm the text matches the action

IMPORTANT: Return coordinates in the VIEWPORT coordinate space ({width}×{height}), NOT in the full image pixel space.

Action: {description}

Respond with ONLY this JSON, nothing else: {{"x": <int>, "y": <int>}}"""


def _vlm_get_click_coords(page: Page, description: str) -> tuple[int, int] | None:
    """Send page screenshot + action description to VLM, get (x, y) coordinates."""
    import tempfile
    from pathlib import Path

    try:
        from designforge.llm_client import completion
        from designforge.agents.base_agent import encode_image_for_message, extract_json
    except ImportError:
        logger.warning("designforge not importable, cannot use VLM coordinate")
        return None

    viewport = page.viewport_size
    vw = viewport["width"] if viewport else 390
    vh = viewport["height"] if viewport else 844

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        screenshot_path = tmp.name
        page.screenshot(path=screenshot_path, full_page=False)

    try:
        img_block = encode_image_for_message(screenshot_path)
        prompt = _VLM_CLICK_PROMPT.format(
            width=vw, height=vh,
            img_width=vw * 2, img_height=vh * 2,
            description=description,
        )

        resp = completion(
            model=_VLM_CLICK_MODEL,
            messages=[
                {"role": "system", "content": "You are a UI automation assistant that identifies click coordinates."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Screenshot:"},
                        img_block,
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            max_tokens=1024,
        )
        text = resp.choices[0].message.content
        logger.debug("vlm_coordinate: raw VLM response: %s", text)

        try:
            result = extract_json(text)
        except Exception:
            import re
            m = re.search(r'"x"\s*:\s*(\d+)\s*,\s*"y"\s*:\s*(\d+)', text or "")
            if not m:
                m = re.search(r'(\d+)\s*,\s*(\d+)', text or "")
            if m:
                logger.info("vlm_coordinate: regex fallback parsed coordinates from: %s", text)
                return (int(m.group(1)), int(m.group(2)))
            logger.warning("VLM coordinate extraction failed, raw response: %.200s", text)
            return None

        x = int(result["x"])
        y = int(result["y"])
        return (x, y)

    except Exception as exc:
        logger.warning("VLM coordinate extraction failed: %s", exc)
        return None
    finally:
        Path(screenshot_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _try_click_selector(page: Page, selector: str) -> bool:
    """Try to click the first visible element matching *selector*."""
    try:
        loc = page.locator(selector).first
        if loc.is_visible(timeout=_CLICK_TIMEOUT):
            loc.click(timeout=_CLICK_TIMEOUT)
            return True
    except Exception:
        pass
    return False


def _try_click_by_text(page: Page, text: str) -> bool:
    """Try to click a visible element containing *text*."""
    try:
        for role in ("link", "button"):
            loc = page.get_by_role(role, name=text).first
            try:
                if loc.is_visible(timeout=1000):
                    loc.click(timeout=_CLICK_TIMEOUT)
                    return True
            except Exception:
                continue

        loc = page.get_by_text(text, exact=False).first
        if loc.is_visible(timeout=1000):
            loc.click(timeout=_CLICK_TIMEOUT)
            return True
    except Exception:
        pass
    return False
