"""
VLM Click Accuracy Benchmark — 多 App 全链路测试

Phase 1: 复用 testgen.runner 跑完所有用例（page_exists + tab_click），
         不启用 VLM，验证 DOM/文本导航的基线。
Phase 2: 对 vlm_coordinate 类型用例，将同一张截图发给多个模型，
         逐一验证导航是否成功，并在截图上标注各模型坐标。

用法:
  python scripts/vlm_click_benchmark.py \
    --models gemini-2.5-pro,gemini-2.5-flash,gpt-5,gpt-5-mini,claude-sonnet-4 \
    --run-dirs experiments/20260428_181902_mobileforge-compare/runs/airbnb__claude-opus-4-6__agentic

  python scripts/vlm_click_benchmark.py \
    --models gemini-2.5-pro,gemini-2.5-flash,gpt-5,gpt-5-mini,claude-sonnet-4 \
    --run-dirs \
        experiments/20260428_181902_mobileforge-compare/runs/airbnb__claude-opus-4-6__agentic
    --output-dir .tmp/vlm_benchmark_3  
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ensure project root is on sys.path so testgen / designforge are importable
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _load_model_prices() -> dict[str, dict[str, float]]:
    """Load per-model pricing (USD per 1M tokens) from models.yaml."""
    import yaml
    models_path = Path(_PROJECT_ROOT) / "models.yaml"
    if not models_path.is_file():
        return {}
    data = yaml.safe_load(models_path.read_text("utf-8")) or {}
    return {
        name: {"input": cfg.get("input_price", 0), "output": cfg.get("output_price", 0)}
        for name, cfg in data.items()
        if isinstance(cfg, dict)
    }


_MODEL_PRICES = _load_model_prices()


def _calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate cost in USD for a single call."""
    prices = _MODEL_PRICES.get(model, {})
    input_price = prices.get("input", 0)
    output_price = prices.get("output", 0)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000

VIEWPORT_W, VIEWPORT_H = 440, 956
DEVICE_SCALE_FACTOR = 2
PREVIEW_PORT = 4175
PREVIEW_STARTUP_WAIT = 3
PAGE_LOAD_TIMEOUT = 10_000
POST_ACTION_WAIT = 1500

MODEL_COLORS = [
    (255, 50,  50),    # 红
    (50,  120, 255),   # 蓝
    (50,  200, 50),    # 绿
    (255, 165, 0),     # 橙
    (180, 50,  255),   # 紫
    (0,   200, 200),   # 青
    (255, 105, 180),   # 粉
]

_VLM_CLICK_PROMPT = (
    "Given a screenshot of a mobile app page, identify where to click to perform the action.\n\n"
    "The screenshot is captured at viewport size {vw}×{vh} with device_scale_factor={dsf}, "
    "so the actual image is {img_w}×{img_h} pixels.\n\n"
    "Instructions:\n"
    "1. Identify the target UI element (button, icon, text, or interactive area)\n"
    "2. Return the CENTER coordinates of that element in VIEWPORT space ({vw}×{vh})\n"
    "3. If the target has visible text, confirm the text matches the action\n\n"
    "IMPORTANT: Return coordinates in VIEWPORT space ({vw}×{vh}), NOT image pixel space.\n\n"
    "Action: {description}\n\n"
    'Respond with ONLY this JSON: {{"x": <int>, "y": <int>}}'
)

SCREENSHOT_W = VIEWPORT_W * DEVICE_SCALE_FACTOR
SCREENSHOT_H = VIEWPORT_H * DEVICE_SCALE_FACTOR


def _ensure_ld_library_path():
    conda_prefix = os.environ.get("CONDA_PREFIX") or str(
        Path(sys.executable).parent.parent
    )
    extra_dirs = [
        str(Path(conda_prefix) / "lib"),
        str(Path(conda_prefix) / "x86_64-conda-linux-gnu" / "sysroot" / "usr" / "lib64"),
    ]
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    parts = [p for p in existing.split(":") if p]
    for d in extra_dirs:
        if d not in parts and Path(d).is_dir():
            parts.insert(0, d)
    os.environ["LD_LIBRARY_PATH"] = ":".join(parts)


_ERROR_PATTERNS = re.compile(
    r"404\s*Not\s*Found|Unexpected\s*Application\s*Error|Cannot\s+GET|Page\s+not\s+found",
    re.IGNORECASE,
)


def _is_blank_page(page) -> bool:
    try:
        body_text = page.inner_text("body", timeout=2000)
        text = body_text.strip()
        if len(text) < 5:
            return True
        if _ERROR_PATTERNS.search(text[:500]):
            return True
        return False
    except Exception:
        return True


def _content_hash(page) -> str:
    try:
        html = page.inner_html("body", timeout=2000)
        return hashlib.md5(html.encode()).hexdigest()
    except Exception:
        return ""


def _goto_with_fallback(page, base_url: str, route: str) -> str:
    """Navigate to route; if blank, fallback to root '/'.

    Many SPAs map the first tab to '/' instead of a named route.
    Returns the route that actually loaded.
    """
    page.goto(f"{base_url}{route}", wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
    page.wait_for_timeout(500)

    if not _is_blank_page(page):
        return route

    if route != "/":
        logger.info("Route %s loaded blank, falling back to /", route)
        page.goto(f"{base_url}/", wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        page.wait_for_timeout(500)
        if not _is_blank_page(page):
            return "/"

    return route


# ═══════════════════════════════════════════════════════════════════════════
# VLM coordinate call
# ═══════════════════════════════════════════════════════════════════════════

def call_vlm(model: str, screenshot_path: str, description: str) -> dict:
    """Call VLM to predict click coordinates in viewport space.

    Returns {"x": int, "y": int, ...} in viewport CSS pixel space (VIEWPORT_W×VIEWPORT_H),
    ready to be used directly with page.mouse.click(x, y).
    """
    from designforge.llm_client import completion
    from designforge.agents.base_agent import encode_image_for_message, extract_json

    img_block = encode_image_for_message(screenshot_path)
    prompt = _VLM_CLICK_PROMPT.format(
        vw=VIEWPORT_W, vh=VIEWPORT_H, dsf=DEVICE_SCALE_FACTOR,
        img_w=SCREENSHOT_W, img_h=SCREENSHOT_H,
        description=description,
    )

    t0 = time.time()
    try:
        resp = completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a UI automation assistant. Identify the click target "
                        "and return its center coordinates in viewport space."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Screenshot:"},
                        img_block,
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            max_tokens=512,
        )
        latency = (time.time() - t0) * 1000
        text = resp.choices[0].message.content
        usage = resp.usage

        # Strip markdown code fences if present
        cleaned = text.strip() if text else ""
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```\s*$', '', cleaned)

        try:
            result = extract_json(cleaned)
            x = int(result["x"])
            y = int(result["y"])
        except Exception:
            m = re.search(r'"x"\s*:\s*(\d+)\s*,\s*"y"\s*:\s*(\d+)', cleaned)
            if not m:
                m = re.search(r'"x"\s*:\s*(\d+)\s*,\s*"y"\s*:\s*(\d+)', text or "")
            if not m:
                m = re.search(r'(\d+)\s*,\s*(\d+)', cleaned)
            if m:
                x, y = int(m.group(1)), int(m.group(2))
                result = {"thought": ""}
            else:
                return {
                    "error": f"parse_failed: {text[:200]}",
                    "latency_ms": latency,
                    "raw": text,
                }

        # Auto-correct: if model returned image pixel coords instead of viewport
        if x > VIEWPORT_W or y > VIEWPORT_H:
            x = int(round(x / DEVICE_SCALE_FACTOR))
            y = int(round(y / DEVICE_SCALE_FACTOR))

        # Clamp to viewport bounds
        x = max(0, min(x, VIEWPORT_W - 1))
        y = max(0, min(y, VIEWPORT_H - 1))

        return {
            "x": x,
            "y": y,
            "thought": result.get("thought", "") if isinstance(result, dict) else "",
            "latency_ms": latency,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "raw": text,
        }

    except Exception as e:
        return {"error": str(e), "latency_ms": (time.time() - t0) * 1000}


# ═══════════════════════════════════════════════════════════════════════════
# Screenshot annotation
# ═══════════════════════════════════════════════════════════════════════════

def annotate_screenshot(
    screenshot_path: str,
    predictions: list[dict],
    model_names: list[str],
    output_path: str,
    action_desc: str = "",
):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(screenshot_path)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24
        )
    except OSError:
        font = ImageFont.load_default()

    if action_desc:
        desc_short = action_desc[:80]
        bbox = draw.textbbox((10, 10), desc_short, font=font)
        draw.rectangle(
            [bbox[0] - 4, bbox[1] - 4, bbox[2] + 4, bbox[3] + 4],
            fill=(0, 0, 0, 200),
        )
        draw.text((10, 10), desc_short, fill=(255, 255, 255), font=font)

    for idx, (pred, model_name) in enumerate(zip(predictions, model_names)):
        if "error" in pred:
            continue

        # Viewport coords → image pixel coords for annotation
        x = pred["x"] * DEVICE_SCALE_FACTOR
        y = pred["y"] * DEVICE_SCALE_FACTOR
        color = MODEL_COLORS[idx % len(MODEL_COLORS)]
        r = 14

        draw.line([(x - r, y), (x + r, y)], fill=color, width=4)
        draw.line([(x, y - r), (x, y + r)], fill=color, width=4)
        draw.ellipse([(x - r, y - r), (x + r, y + r)], outline=color, width=3)

        short_name = model_name.split("/")[-1][:20]
        label = f"{short_name} ({pred['x']},{pred['y']})"
        nav_mark = " ✓" if pred.get("nav_success") else " ✗"
        label += nav_mark

        label_y = max(40, y - 30 - idx * 30)
        lbbox = draw.textbbox((x + 18, label_y), label, font=font)
        draw.rectangle(
            [lbbox[0] - 3, lbbox[1] - 2, lbbox[2] + 3, lbbox[3] + 2],
            fill=(0, 0, 0, 200),
        )
        draw.text((x + 18, label_y), label, fill=color, font=font)

    img.save(output_path)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers for multi-app support
# ═══════════════════════════════════════════════════════════════════════════

def _infer_app_name(run_dir: Path) -> str:
    """Extract app name from run dir name, e.g. 'airbnb__claude-opus-4-6__agentic' → 'airbnb'."""
    return run_dir.name.split("__")[0]


def _find_cases_path(app_name: str) -> Path | None:
    """Auto-discover test_cases file under input/mobileforge/<app>/."""
    base = Path(_PROJECT_ROOT) / "input" / "mobileforge" / app_name
    for name in ("test_cases.reviewed.json", "test_cases.json"):
        p = base / name
        if p.is_file():
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Single-app benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

def _run_single_app(
    run_dir: Path,
    cases_path: Path,
    models: list[str],
    output_dir: Path,
    console,
) -> dict:
    """Run the full benchmark (Phase 1 + Phase 2) for a single app.

    Returns a result dict with phase1_results, vlm_results, etc.
    """
    app_name = _infer_app_name(run_dir)
    app_output = output_dir / app_name
    app_output.mkdir(parents=True, exist_ok=True)
    screenshots_dir = app_output / "annotated"
    screenshots_dir.mkdir(exist_ok=True)
    phase1_screenshots = app_output / "phase1_screenshots"
    phase1_screenshots.mkdir(exist_ok=True)

    cases_data = json.loads(cases_path.read_text("utf-8"))
    all_cases: list[dict] = cases_data.get("cases", [])
    if not all_cases:
        console.print(f"[red][{app_name}] No test cases found in {cases_path}[/red]")
        return {"app_name": app_name, "error": "no_cases"}

    dom_cases = [c for c in all_cases if c["action"]["strategy"] != "vlm_coordinate"]
    vlm_cases = [c for c in all_cases if c["action"]["strategy"] == "vlm_coordinate"]

    console.print(f"\n[bold]{'═' * 60}[/bold]")
    console.print(f"[bold]  [{app_name}] VLM Click Accuracy Benchmark[/bold]")
    console.print(f"[bold]{'═' * 60}[/bold]")
    console.print(f"  Run dir  : {run_dir}")
    console.print(f"  Cases    : {cases_path.name}")
    console.print(f"  Total    : {len(all_cases)} ({len(dom_cases)} DOM/text, {len(vlm_cases)} vlm_coordinate)")
    console.print(f"  Models   : {models}")
    console.print(f"  Output   : {app_output}")

    # ── Build ──
    if not (run_dir / "dist").is_dir():
        console.print("[yellow]dist/ not found, building...[/yellow]")
        proc = subprocess.run(
            ["npx", "vite", "build"], cwd=str(run_dir), capture_output=True, timeout=120
        )
        if proc.returncode != 0:
            console.print(f"[red]Build failed: {proc.stderr[:300]}[/red]")
            return {"app_name": app_name, "error": "build_failed"}

    # ── Route resolution ──
    from testgen.route_resolver import resolve_routes

    router_path = run_dir / "src" / "router.tsx"
    page_names = set()
    for c in all_cases:
        page_names.add(c["from_page"]["page_name"])
        page_names.add(c["to_page"]["page_name"])
    route_map = resolve_routes(router_path, list(page_names))
    console.print(f"  Routes   : {len(route_map)} resolved")

    # ── Start vite preview ──
    _ensure_ld_library_path()
    preview_proc = subprocess.Popen(
        ["npx", "vite", "preview", "--port", str(PREVIEW_PORT)],
        cwd=str(run_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(PREVIEW_STARTUP_WAIT)
    if preview_proc.poll() is not None:
        console.print(f"[red][{app_name}] vite preview exited immediately[/red]")
        return {"app_name": app_name, "error": "preview_failed"}

    base_url = f"http://localhost:{PREVIEW_PORT}"
    console.print(f"  Server   : {base_url}")

    phase1_results: list[dict] = []
    vlm_results: dict[str, list[dict]] = {m: [] for m in models}

    try:
        from playwright.sync_api import sync_playwright
        from testgen.actions import execute_action, set_vlm_model

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            # ── Phase 1 ──
            set_vlm_model(None)
            console.print(f"\n[bold]═══ [{app_name}] Phase 1: DOM/Text Navigation (no VLM) ═══[/bold]")

            for i, case in enumerate(dom_cases, 1):
                test_id = case["test_id"]
                test_type = case["type"]
                from_name = case["from_page"]["page_name"]
                from_route = route_map.get(from_name, case["from_page"]["route_hint"])
                if not from_route.startswith("/"):
                    from_route = f"/{from_route}"

                result: dict[str, Any] = {
                    "test_id": test_id,
                    "type": test_type,
                    "strategy": case["action"]["strategy"],
                    "passed": False,
                    "navigation_success": False,
                    "screenshot_before": None,
                    "screenshot_after": None,
                    "error": None,
                }

                context = browser.new_context(
                    viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                    device_scale_factor=DEVICE_SCALE_FACTOR,
                )
                page = context.new_page()
                js_errors: list[str] = []
                page.on("pageerror", lambda err: js_errors.append(str(err)))

                try:
                    actual_route = _goto_with_fallback(page, base_url, from_route)
                    if actual_route != from_route:
                        from_route = actual_route

                    if _is_blank_page(page):
                        result["error"] = f"from_page '{from_name}' loaded as blank"
                        phase1_results.append(result)
                        context.close()
                        mark = "[red]✗ BLANK[/red]"
                        console.print(f"  [{i}/{len(dom_cases)}] {mark} {test_id}")
                        continue

                    before_path = phase1_screenshots / f"{test_id}__before.png"
                    page.screenshot(path=str(before_path), full_page=False)
                    result["screenshot_before"] = str(before_path)

                    if test_type == "page_exists":
                        result["navigation_success"] = True
                        result["passed"] = True
                        result["screenshot_after"] = str(before_path)
                    else:
                        url_before = page.url
                        hash_before = _content_hash(page)

                        action_ok = execute_action(page, case["action"])
                        if not action_ok:
                            result["error"] = f"action failed: {case['action']['description']}"
                        else:
                            page.wait_for_timeout(POST_ACTION_WAIT)

                            after_path = phase1_screenshots / f"{test_id}__after.png"
                            page.screenshot(path=str(after_path), full_page=False)
                            result["screenshot_after"] = str(after_path)

                            url_after = page.url
                            hash_after = _content_hash(page)
                            nav_success = (url_before != url_after) or (hash_before != hash_after)
                            result["navigation_success"] = nav_success
                            result["passed"] = nav_success

                    if js_errors:
                        result["js_errors"] = js_errors[:5]

                except Exception as exc:
                    result["error"] = str(exc)
                finally:
                    context.close()

                phase1_results.append(result)
                if result["passed"]:
                    mark = "[green]✓[/green]"
                elif result["error"]:
                    mark = f"[red]✗ {result['error'][:40]}[/red]"
                else:
                    mark = "[red]✗ NO NAV[/red]"
                console.print(f"  [{i}/{len(dom_cases)}] {mark} {test_id}")

            p1_passed = sum(1 for r in phase1_results if r["passed"])
            console.print(
                f"\n  Phase 1: [bold]{p1_passed}/{len(dom_cases)}[/bold] passed"
            )

            # ── Phase 2 ──
            console.print(
                f"\n[bold]═══ [{app_name}] Phase 2: VLM Coordinate Benchmark "
                f"({len(vlm_cases)} cases × {len(models)} models) ═══[/bold]"
            )

            for case_idx, case in enumerate(vlm_cases):
                test_id = case["test_id"]
                action_desc = case["action"]["description"]
                from_name = case["from_page"]["page_name"]
                from_route = route_map.get(from_name, case["from_page"]["route_hint"])
                if not from_route.startswith("/"):
                    from_route = f"/{from_route}"

                console.print(
                    f"\n  [bold cyan][{case_idx+1}/{len(vlm_cases)}] {test_id}[/bold cyan]"
                )
                console.print(f"    Action: {action_desc}")

                page_loaded = False
                screenshot_ctx = browser.new_context(
                    viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                    device_scale_factor=DEVICE_SCALE_FACTOR,
                )
                screenshot_page = screenshot_ctx.new_page()
                try:
                    actual_route = _goto_with_fallback(screenshot_page, base_url, from_route)
                    if actual_route != from_route:
                        console.print(f"    [dim]fallback: {from_route} → {actual_route}[/dim]")
                        from_route = actual_route

                    if _is_blank_page(screenshot_page):
                        console.print("    [red]Page loaded as blank[/red]")
                    else:
                        page_loaded = True
                except Exception as exc:
                    console.print(f"    [red]Page load failed: {exc}[/red]")

                if not page_loaded:
                    screenshot_ctx.close()
                    for m in models:
                        vlm_results[m].append({
                            "case_idx": case_idx,
                            "test_id": test_id,
                            "action": action_desc,
                            "success": False,
                            "error": "page_load_failed",
                        })
                    continue

                raw_path = str(app_output / f"_raw_{test_id}.png")
                screenshot_page.screenshot(path=raw_path, full_page=False)
                screenshot_ctx.close()

                predictions: list[dict] = []
                for model in models:
                    pred = call_vlm(model, raw_path, action_desc)
                    predictions.append(pred)

                    if "error" in pred:
                        console.print(
                            f"    [red]{model}: ERROR — {pred['error'][:60]}[/red]"
                        )
                    else:
                        console.print(
                            f"    {model}: ({pred['x']}, {pred['y']}) "
                            f"[{pred['latency_ms']:.0f}ms]"
                        )

                for m_idx, (model, pred) in enumerate(zip(models, predictions)):
                    record: dict[str, Any] = {
                        "case_idx": case_idx,
                        "test_id": test_id,
                        "action": action_desc,
                    }

                    if "error" in pred:
                        record["success"] = False
                        record["error"] = pred["error"]
                        record["latency_ms"] = pred.get("latency_ms", 0)
                        pred["nav_success"] = False
                    else:
                        x, y = pred["x"], pred["y"]
                        nav_ok = False

                        vctx = browser.new_context(
                            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                            device_scale_factor=DEVICE_SCALE_FACTOR,
                        )
                        vpage = vctx.new_page()
                        try:
                            _goto_with_fallback(vpage, base_url, from_route)

                            url_before = vpage.url
                            hash_before = _content_hash(vpage)

                            vpage.mouse.click(x, y)
                            vpage.wait_for_timeout(POST_ACTION_WAIT)

                            url_after = vpage.url
                            hash_after = _content_hash(vpage)
                            nav_ok = (url_before != url_after) or (hash_before != hash_after)
                        except Exception as exc:
                            logger.warning("nav check failed for %s: %s", model, exc)
                        finally:
                            vctx.close()

                        record["success"] = nav_ok
                        record["x"] = x
                        record["y"] = y
                        record["latency_ms"] = pred["latency_ms"]
                        record["prompt_tokens"] = pred.get("prompt_tokens", 0)
                        record["completion_tokens"] = pred.get("completion_tokens", 0)
                        record["total_tokens"] = pred.get("total_tokens", 0)
                        record["cost_usd"] = _calc_cost(model, pred.get("prompt_tokens", 0), pred.get("completion_tokens", 0))
                        record["thought"] = pred.get("thought", "")
                        pred["nav_success"] = nav_ok

                        mark = (
                            "[green]✓ NAV[/green]"
                            if nav_ok
                            else "[red]✗ NO NAV[/red]"
                        )
                        console.print(f"    {mark} {model}")

                    vlm_results[model].append(record)

                annotated_path = str(
                    screenshots_dir / f"{case_idx:02d}_{test_id}.png"
                )
                annotate_screenshot(
                    raw_path, predictions, models, annotated_path, action_desc
                )

                Path(raw_path).unlink(missing_ok=True)

            browser.close()

    finally:
        if preview_proc.poll() is None:
            preview_proc.send_signal(signal.SIGTERM)
            try:
                preview_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                preview_proc.kill()

    return {
        "app_name": app_name,
        "run_dir": str(run_dir),
        "route_map": route_map,
        "dom_cases": dom_cases,
        "vlm_cases": vlm_cases,
        "phase1_results": phase1_results,
        "vlm_results": vlm_results,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main — orchestrate multi-app benchmark
# ═══════════════════════════════════════════════════════════════════════════

def main():
    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser(description="VLM Click Accuracy Benchmark")
    parser.add_argument("--models", "-m", required=True, help="逗号分隔的 VLM 模型列表")
    parser.add_argument(
        "--run-dirs", "-r", nargs="+", required=True,
        help="一个或多个已构建的 run 目录 (自动从目录名推断 app 名和 test_cases 路径)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=".tmp/vlm_benchmark",
        help="输出根目录 (default: .tmp/vlm_benchmark)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(name)s %(levelname)s %(message)s"
    )
    console = Console()
    models = [m.strip() for m in args.models.split(",")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print("[bold]═══ VLM Click Accuracy Benchmark (multi-app) ═══[/bold]")
    console.print(f"  Apps     : {len(args.run_dirs)}")
    console.print(f"  Models   : {models}")
    console.print(f"  Viewport : {VIEWPORT_W}×{VIEWPORT_H} @{DEVICE_SCALE_FACTOR}x (image: {SCREENSHOT_W}×{SCREENSHOT_H})")
    console.print(f"  Output   : {output_dir}")

    # ── Run each app sequentially ──
    all_app_results: list[dict] = []
    for rd in args.run_dirs:
        run_dir = Path(rd)
        app_name = _infer_app_name(run_dir)
        cases_path = _find_cases_path(app_name)
        if cases_path is None:
            console.print(
                f"[red]Cannot find test_cases for '{app_name}' "
                f"(looked in input/mobileforge/{app_name}/)[/red]"
            )
            continue

        result = _run_single_app(run_dir, cases_path, models, output_dir, console)
        all_app_results.append(result)

    # ══════════════════════════════════════════════════════════
    # Aggregated summary across all apps
    # ══════════════════════════════════════════════════════════
    valid_results = [r for r in all_app_results if "error" not in r]
    if not valid_results:
        console.print("[red]No valid results.[/red]")
        return

    console.print(f"\n\n[bold]{'═' * 60}[/bold]")
    console.print("[bold]       AGGREGATED RESULTS[/bold]")
    console.print(f"[bold]{'═' * 60}[/bold]")

    # Phase 1 aggregated
    p1_table = Table(title="Phase 1: DOM/Text Navigation (all apps)", show_lines=True)
    p1_table.add_column("App", style="magenta")
    p1_table.add_column("Type", style="cyan")
    p1_table.add_column("Passed", justify="center")
    p1_table.add_column("Total", justify="center")
    p1_table.add_column("Rate", justify="center")

    grand_p1_total = 0
    grand_p1_pass = 0
    for r in valid_results:
        by_type: dict[str, dict[str, int]] = {}
        for rec in r["phase1_results"]:
            t = rec["type"]
            if t not in by_type:
                by_type[t] = {"total": 0, "passed": 0}
            by_type[t]["total"] += 1
            if rec["passed"]:
                by_type[t]["passed"] += 1

        for t_name in ["page_exists", "tab_navigation", "parent_child_navigation"]:
            if t_name not in by_type:
                continue
            s = by_type[t_name]
            rate = s["passed"] / s["total"] if s["total"] else 0
            p1_table.add_row(r["app_name"], t_name, str(s["passed"]), str(s["total"]), f"{rate:.0%}")
            grand_p1_total += s["total"]
            grand_p1_pass += s["passed"]

    p1_table.add_row(
        "[bold]ALL[/bold]", "[bold]TOTAL[/bold]",
        f"[bold]{grand_p1_pass}[/bold]",
        f"[bold]{grand_p1_total}[/bold]",
        f"[bold]{grand_p1_pass/grand_p1_total:.0%}[/bold]" if grand_p1_total else "—",
    )
    console.print(p1_table)

    # Phase 2 aggregated
    merged_vlm: dict[str, list[dict]] = {m: [] for m in models}
    has_vlm = False
    for r in valid_results:
        if r["vlm_cases"]:
            has_vlm = True
        for m in models:
            merged_vlm[m].extend(r["vlm_results"][m])

    if has_vlm:
        console.print()
        v_table = Table(title="Phase 2: VLM Coordinate Benchmark (all apps)", show_lines=True)
        v_table.add_column("Model", style="cyan")
        v_table.add_column("Success", justify="center")
        v_table.add_column("Total", justify="center")
        v_table.add_column("Accuracy", justify="center")
        v_table.add_column("Avg Latency", justify="right")
        v_table.add_column("Avg Tokens", justify="right")
        v_table.add_column("Total Cost", justify="right")
        v_table.add_column("Errors", justify="center")

        for model in models:
            data = merged_vlm[model]
            valid = [d for d in data if "error" not in d]
            successes = sum(1 for d in valid if d["success"])
            total = len(valid)
            errs = sum(1 for d in data if "error" in d)
            acc = successes / total if total else 0
            lat = (
                statistics.mean([d["latency_ms"] for d in valid])
                if valid
                else 0
            )
            avg_tokens = (
                statistics.mean([d.get("total_tokens", 0) for d in valid])
                if valid
                else 0
            )
            total_cost = sum(
                _calc_cost(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0))
                for d in valid
            )
            avg_cost = total_cost / len(valid) if valid else 0
            style = "green" if acc > 0.5 else "yellow" if acc > 0.2 else "red"
            v_table.add_row(
                model,
                str(successes),
                str(total),
                f"[{style}]{acc:.0%}[/{style}]",
                f"{lat:.0f}ms",
                f"{avg_tokens:.0f}",
                f"${total_cost:.4f}",
                str(errs),
            )

        console.print(v_table)

    # ── Save aggregated report ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "models": models,
        "coord_mode": "viewport",
        "viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H, "device_scale_factor": DEVICE_SCALE_FACTOR},
        "apps": [],
    }
    for r in valid_results:
        by_type = {}
        for rec in r["phase1_results"]:
            t = rec["type"]
            if t not in by_type:
                by_type[t] = {"total": 0, "passed": 0}
            by_type[t]["total"] += 1
            if rec["passed"]:
                by_type[t]["passed"] += 1

        report["apps"].append({
            "app_name": r["app_name"],
            "run_dir": r["run_dir"],
            "route_map": r["route_map"],
            "phase1": {
                "total": len(r["dom_cases"]),
                "passed": sum(1 for rec in r["phase1_results"] if rec["passed"]),
                "by_type": {
                    t: {"total": s["total"], "passed": s["passed"],
                        "rate": round(s["passed"] / s["total"], 4) if s["total"] else 0}
                    for t, s in by_type.items()
                },
                "details": r["phase1_results"],
            },
            "phase2_vlm": {
                "total_cases": len(r["vlm_cases"]),
                "cases": [
                    {
                        "test_id": c["test_id"],
                        "action": c["action"]["description"],
                        "from_page": c["from_page"]["page_name"],
                    }
                    for c in r["vlm_cases"]
                ],
                "models": {
                    m: {
                        "results": r["vlm_results"][m],
                        "nav_successes": sum(1 for d in r["vlm_results"][m] if d.get("success")),
                        "errors": sum(1 for d in r["vlm_results"][m] if "error" in d),
                        "accuracy": round(
                            sum(1 for d in r["vlm_results"][m] if d.get("success"))
                            / max(1, len([d for d in r["vlm_results"][m] if "error" not in d])),
                            4,
                        ),
                        "total_cost_usd": round(sum(
                            _calc_cost(m, d.get("prompt_tokens", 0), d.get("completion_tokens", 0))
                            for d in r["vlm_results"][m] if "error" not in d
                        ), 6),
                        "avg_latency_ms": round(statistics.mean(
                            [d.get("latency_ms", 0) for d in r["vlm_results"][m] if "error" not in d]
                        ), 1) if any("error" not in d for d in r["vlm_results"][m]) else 0,
                        "avg_tokens": round(statistics.mean(
                            [d.get("total_tokens", 0) for d in r["vlm_results"][m] if "error" not in d]
                        ), 1) if any("error" not in d for d in r["vlm_results"][m]) else 0,
                    }
                    for m in models
                },
            },
        })

    report_path = output_dir / "benchmark_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── Generate Markdown summary ──
    _COLOR_CIRCLES = ["🔴", "🔵", "🟢", "🟠", "🟣", "🩵", "🩷"]

    md_lines = [
        "# VLM Click Benchmark Summary",
        "",
        f"- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **Viewport**: {VIEWPORT_W}×{VIEWPORT_H} (DSF={DEVICE_SCALE_FACTOR}, image={VIEWPORT_W*DEVICE_SCALE_FACTOR}×{VIEWPORT_H*DEVICE_SCALE_FACTOR})",
        f"- **Coord Mode**: viewport pixel",
        f"- **Models**: {', '.join(models)}",
        "",
    ]

    if has_vlm:
        md_lines.append("## Phase 2: VLM Coordinate Prediction")
        md_lines.append("")
        md_lines.append("| Color | Model | Success | Total | Accuracy | Avg Latency | Avg Tokens | Total Cost | Errors |")
        md_lines.append("|:-----:|-------|:-------:|:-----:|:--------:|------------:|-----------:|-----------:|:------:|")

        for idx, model in enumerate(models):
            data = merged_vlm[model]
            valid = [d for d in data if "error" not in d]
            successes = sum(1 for d in valid if d["success"])
            total = len(valid)
            errs = sum(1 for d in data if "error" in d)
            acc = successes / total if total else 0
            lat = statistics.mean([d["latency_ms"] for d in valid]) if valid else 0
            avg_tok = statistics.mean([d.get("total_tokens", 0) for d in valid]) if valid else 0
            cost = sum(
                _calc_cost(model, d.get("prompt_tokens", 0), d.get("completion_tokens", 0))
                for d in valid
            )
            circle = _COLOR_CIRCLES[idx % len(_COLOR_CIRCLES)]
            acc_str = f"**{acc:.0%}**" if acc > 0.5 else f"{acc:.0%}"
            md_lines.append(
                f"| {circle} | {model} | {successes} | {total} | {acc_str} | {lat:.0f}ms | {avg_tok:.0f} | ${cost:.4f} | {errs} |"
            )

        md_lines.append("")

    # Per-app detail tables
    for r in valid_results:
        app = r["app_name"]
        md_lines.append(f"### {app}")
        md_lines.append("")
        if r["vlm_cases"]:
            md_lines.append("| Case | Action | " + " | ".join(f"{_COLOR_CIRCLES[i % len(_COLOR_CIRCLES)]} {m}" for i, m in enumerate(models)) + " |")
            md_lines.append("|------|--------|" + "|".join([":---:" for _ in models]) + "|")
            for ci, case in enumerate(r["vlm_cases"]):
                action = case["action"]["description"][:40]
                cells = []
                for mi, m in enumerate(models):
                    res = r["vlm_results"][m][ci] if ci < len(r["vlm_results"][m]) else {}
                    if "error" in res:
                        cells.append("❌ err")
                    elif res.get("success"):
                        cells.append(f"✅ ({res.get('x',0)},{res.get('y',0)})")
                    else:
                        cells.append(f"❎ ({res.get('x',0)},{res.get('y',0)})")
                md_lines.append(f"| {ci} | {action} | " + " | ".join(cells) + " |")
            md_lines.append("")

    md_lines.append("---")
    md_lines.append(f"*Generated by vlm_click_benchmark.py*")

    md_path = output_dir / "summary.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    console.print(f"\n[green]Report : {report_path}[/green]")
    console.print(f"[green]Summary: {md_path}[/green]")
    for r in valid_results:
        app = r["app_name"]
        console.print(f"[green]  [{app}] annotated       : {output_dir / app / 'annotated'}/[/green]")
        console.print(f"[green]  [{app}] phase1 screenshots: {output_dir / app / 'phase1_screenshots'}/[/green]")


if __name__ == "__main__":
    main()
