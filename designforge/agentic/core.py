"""
AgenticPipeline — single autonomous agent with full tool access.

A single ToolAgent receives all design screenshots and iteratively builds
a complete React + TypeScript + Tailwind project using all available tools:
write_file, read_file, str_replace, batch_str_replace, list_files,
delete_file, run_command, grep, and task_done.

Flow:
  1. scaffold_project() → npm install
  2. Agent loop: LLM sees all screenshots + instructions → writes code with tools
  3. task_done / no-tools → verify_completion (tsc + vite build)
  4. If build fails → error feedback → agent fixes → rebuild (loop)
  5. enforce_scaffold() safety net after completion
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from designforge import config
from designforge.agents.base_agent import encode_image_for_message
from designforge.agents.tool_agent import TOOL_DEFS, ToolAgent, _todo_output_dir
from designforge.agentic.prompts import SYSTEM_PROMPT, USER_TEMPLATE
from designforge.llm_client import LLMCallError
from designforge.scaffold import enforce_scaffold, get_protected_paths, scaffold_project
from designforge.tools.command import run_command

logger = logging.getLogger(__name__)

_pipeline_cfg: dict = config._pipeline


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MAX_TSC_ERRORS = 30
_MAX_ERROR_CHARS = 8000

DEFAULT_MAX_ITERATIONS = 50
"""Default iteration cap for the agentic agent loop.

Override via experiment.yaml:
    pipeline:
      agent_max_iterations:
        agentic: 60
"""

_NPM_MAX_RETRIES = 3
_NPM_BACKOFF_BASE = 5
_NPM_FALLBACK_REGISTRY = "https://registry.npmmirror.com/"
_NPM_TRANSIENT_RE = re.compile(
    r"(E404|ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|FETCH_ERROR|ERR_SOCKET_TIMEOUT)",
    re.IGNORECASE,
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _truncate_errors(
    text: str, max_errors: int = _MAX_TSC_ERRORS, max_chars: int = _MAX_ERROR_CHARS
) -> str:
    lines = text.splitlines()
    if len(lines) > max_errors:
        kept = "\n".join(lines[:max_errors])
        text = kept + f"\n\n... ({len(lines) - max_errors} more error lines omitted)"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... (output truncated)"
    return text


def _npm_install(cwd: str) -> tuple[bool, str]:
    """npm install with retries and registry fallback."""
    last_output = ""
    for attempt in range(1, _NPM_MAX_RETRIES + 1):
        result = run_command("npm install", cwd=cwd, timeout=180)
        if result.success:
            return True, ""
        last_output = result.output
        if not _NPM_TRANSIENT_RE.search(last_output):
            return False, last_output[:3000]
        time.sleep(_NPM_BACKOFF_BASE * attempt)

    result = run_command(
        f"npm install --registry {_NPM_FALLBACK_REGISTRY}", cwd=cwd, timeout=180
    )
    if result.success:
        return True, ""
    return False, f"npm install failed after retries:\n{result.output[:3000]}"


class _AgenticToolAgent(ToolAgent):
    """Single agent with all tools for autonomous project construction."""

    name = "agentic"

    def __init__(self, output_dir: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._output_dir = output_dir
        self._protected_paths = get_protected_paths(output_dir)

    def get_tools(self) -> list[dict]:
        tools = [
            TOOL_DEFS["write_file"],
            TOOL_DEFS["read_file"],
            TOOL_DEFS["list_files"],
            # TOOL_DEFS["delete_file"],
            TOOL_DEFS["run_command"],
            TOOL_DEFS["grep"],
            TOOL_DEFS["batch_str_replace"],
            TOOL_DEFS["task_done"],
        ]
        if _pipeline_cfg.get("todo_tool", False):
            tools.append(TOOL_DEFS["update_tasks"])
        return tools

    def verify_completion(self) -> tuple[bool, list[str]]:
        """Run tsc + vite build as the completion check."""
        errors: list[str] = []

        src_dir = Path(self._output_dir) / "src"
        if not src_dir.exists():
            errors.append("src/ directory does not exist")
            return False, errors

        pages_dir = src_dir / "pages"
        if not pages_dir.exists() or not list(pages_dir.glob("*.tsx")):
            errors.append("No page components found in src/pages/")
            return False, errors

        router_file = src_dir / "router.tsx"
        if not router_file.exists():
            errors.append("src/router.tsx does not exist")
            return False, errors

        app_file = src_dir / "App.tsx"
        if not app_file.exists():
            errors.append("src/App.tsx does not exist")
            return False, errors

        passed, report = self._run_build()
        if not passed:
            errors.append(report)
        return passed, errors

    def _run_build(self) -> tuple[bool, str]:
        """Run tsc + vite build. Returns (success, error_report)."""
        cwd = self._output_dir

        tsc = run_command("npx tsc --noEmit", cwd=cwd, timeout=120)
        if not tsc.success:
            raw = _strip_ansi(tsc.output)
            report = _truncate_errors(raw)
            return False, f"## tsc --noEmit FAILED (exit {tsc.return_code})\n\n{report}"

        vite = run_command("npx vite build", cwd=cwd, timeout=120)
        if not vite.success:
            raw = _strip_ansi(vite.output)
            report = _truncate_errors(raw, max_errors=50)
            return False, f"## vite build FAILED (exit {vite.return_code})\n\n{report}"

        dist = Path(cwd) / "dist"
        if not dist.exists():
            return False, "Both tsc and vite reported success, but dist/ was not created."

        return True, ""


def _build_page_name_map(metadata: dict) -> dict[str, str]:
    """Build {screenshot_filename_stem: unique_label} mapping from metadata.

    Unique names stay as-is. Duplicates become: bookshelf (1/2), bookshelf (2/2).
    """
    counts: dict[str, int] = {}
    for page in metadata.get("pages", []):
        name = page.get("page_name", "")
        if name:
            counts[name] = counts.get(name, 0) + 1

    name_map: dict[str, str] = {}
    seen: dict[str, int] = {}
    for page in metadata.get("pages", []):
        screenshot = page.get("screenshot", "")
        page_name = page.get("page_name", "")
        if screenshot and page_name:
            stem = Path(screenshot).stem
            if counts.get(page_name, 1) == 1:
                name_map[stem] = page_name
            else:
                seen[page_name] = seen.get(page_name, 0) + 1
                total = counts[page_name]
                name_map[stem] = f"{page_name} ({seen[page_name]}/{total})"
    return name_map


def _build_route_hints(metadata: dict, page_name_map: dict[str, str] | None = None) -> str:
    """Build descriptive page info from metadata. No implementation suggestions.

    Uses page_name_map for deduplicated labels when available.
    """
    pages = metadata.get("pages", [])
    tab_structure = metadata.get("tab_structure", [])
    if not pages:
        return ""

    screenshot_to_label: dict[str, str] = {}
    if page_name_map:
        for stem, label in page_name_map.items():
            screenshot_to_label[stem] = label

    def _label(page: dict) -> str:
        screenshot = page.get("screenshot", "")
        if screenshot and screenshot_to_label:
            stem = Path(screenshot).stem
            if stem in screenshot_to_label:
                return screenshot_to_label[stem]
        return page.get("page_name", "")

    lines = ["\n## Page info\n"]
    if tab_structure:
        lines.append(f"Main tabs: {', '.join(tab_structure)}\n")

    rels: list[str] = []
    for page in pages:
        parent = page.get("parent_page", "")
        if not parent:
            continue
        label = _label(page)
        ptype = page.get("page_type", "")
        trigger = page.get("trigger", "")
        article = "an" if ptype[0:1] in "aeiou" else "a"
        rel = f"- {label} is {article} {ptype} of {parent}"
        if trigger:
            rel += f", triggered by: {trigger}"
        rels.append(rel)

    if rels:
        lines.append("\nPage relationships:")
        lines.extend(rels)

    return "\n".join(lines) + "\n"


def _build_anchor_routes(metadata: dict) -> dict[str, str]:
    """Build anchor route map {route_path: page_name} for evaluation."""
    pages = metadata.get("pages", [])
    tab_structure = metadata.get("tab_structure", [])
    anchors: dict[str, str] = {}

    for page in pages:
        name = page.get("page_name", "")
        parent = page.get("parent_page", "")

        if tab_structure and name == tab_structure[0]:
            route = "/"
        elif parent:
            short = name.replace(f"{parent}_", "").replace(parent, "").strip("_")
            route = f"/{parent}/{short}" if short else f"/{name}"
        else:
            route = f"/{name}"

        anchors[route] = name

    return anchors


class AgenticPipeline:
    """Single autonomous agent pipeline with full tool access and build loop.

    This is the primary entry point for design-to-code generation.
    """

    name = "agentic"

    def run(
        self,
        image_paths: list[str],
        output_dir: str,
        desc: str = "",
        framework: str = "react",
        css_strategy: str = "tailwind",
        input_dir: str = "",
        dry_run: bool = False,
    ) -> bool:
        model = config.AGENT_MODEL

        if not dry_run:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            logger.info("[agentic] scaffolding project in %s", output_dir)
            scaffold_project(output_dir)

            logger.info("[agentic] running npm install")
            install_ok, install_err = _npm_install(output_dir)
            if not install_ok:
                logger.error("[agentic] npm install failed: %s", install_err)
                return False
            logger.info("[agentic] npm install succeeded")

        route_hints = ""
        page_name_map: dict[str, str] = {}
        if _pipeline_cfg.get("route_hints", False) and input_dir:
            from designforge.agentic.runner import discover_metadata
            metadata = discover_metadata(Path(input_dir))
            if metadata:
                page_name_map = _build_page_name_map(metadata)
                route_hints = _build_route_hints(metadata, page_name_map)
                if not dry_run:
                    anchor_routes = _build_anchor_routes(metadata)
                    anchor_path = Path(output_dir) / ".designforge" / "anchor_routes.json"
                    anchor_path.parent.mkdir(parents=True, exist_ok=True)
                    anchor_path.write_text(json.dumps(anchor_routes, ensure_ascii=False, indent=2))
                logger.info("[agentic] page info injected")

        content: list[dict[str, Any]] = []
        for img_path in image_paths:
            if not dry_run:
                content.append(encode_image_for_message(img_path))
            label = page_name_map.get(Path(img_path).stem, Path(img_path).stem)
            content.append({
                "type": "text",
                "text": f"[Page: {label}]",
            })

        desc_section = f"\nApp description: {desc}\n" if desc else ""
        user_text = USER_TEMPLATE.format(
            desc_section=desc_section,
            route_hints=route_hints,
        )
        content.append({"type": "text", "text": user_text})

        if dry_run:
            print("=" * 60)
            print("SYSTEM PROMPT")
            print("=" * 60)
            print(SYSTEM_PROMPT)
            print()
            print("=" * 60)
            print("USER MESSAGE")
            print("=" * 60)
            for block in content:
                if block.get("type") == "text":
                    print(block["text"])
                elif block.get("type") == "image_url":
                    print("[IMAGE]")
            return True

        agent = _AgenticToolAgent(output_dir=output_dir, model=model)

        _todo_output_dir.set(output_dir)
        if _pipeline_cfg.get("todo_tool", False):
            from designforge.tools.todo import init_plan
            plan_display = init_plan(output_dir)
            content.append({
                "type": "text",
                "text": f"\n## Current plan\n{plan_display}",
            })
            logger.info("[agentic] todo plan initialized with fixed stages")

        logger.info(
            "[agentic] starting agent loop with %d images, model=%s",
            len(image_paths), model,
        )

        max_iters = config.AGENT_MAX_ITERATIONS_OVERRIDES.get(
            "agentic", DEFAULT_MAX_ITERATIONS
        )
        logger.info("[agentic] max iterations: %d", max_iters)

        try:
            agent.run_agent_loop(
                SYSTEM_PROMPT,
                content,
                max_iterations=max_iters,
            )
        except LLMCallError:
            raise
        except Exception as e:
            logger.exception("[agentic] agent loop failed: %s", e)

        enforce_scaffold(output_dir)

        passed, errors = agent.verify_completion()
        if passed:
            logger.info("[agentic] build succeeded")
            return True

        logger.warning("[agentic] build failed after agent loop: %s", errors)
        return False
