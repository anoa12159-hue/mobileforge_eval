"""
Shared utilities for all baselines.

Provides image encoding, response parsing, deterministic project assembly
(router / App / types), scaffolding, and build verification.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from designforge import config
from designforge.agents.base_agent import encode_image_for_message, _wrap_usage
from designforge.llm_client import completion
from designforge.metrics import get_metrics
from designforge.scaffold import scaffold_project
from designforge.tools.command import run_command

logger = logging.getLogger(__name__)


# ======================================================================
# Image & message helpers
# ======================================================================


def derive_page_id(image_path: str) -> str:
    """Derive a kebab-case page ID from an image filename."""
    stem = Path(image_path).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "page"


def normalize_page_id(raw: str) -> str:
    """Normalize a raw string to a safe kebab-case page ID."""
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "page"


def build_multi_image_content(
    image_paths: list[str], text: str
) -> list[dict[str, Any]]:
    """Build a VLM content array with multiple images + a text prompt."""
    content: list[dict[str, Any]] = []
    for i, img in enumerate(image_paths, 1):
        content.append(encode_image_for_message(img))
        content.append({"type": "text", "text": f"[Page {i}]"})
    content.append({"type": "text", "text": text})
    return content


def build_single_image_content(
    image_path: str, text: str
) -> list[dict[str, Any]]:
    """Build a VLM content array with one image + text prompt."""
    return [
        encode_image_for_message(image_path),
        {"type": "text", "text": text},
    ]


# ======================================================================
# LLM call wrapper with metrics
# ======================================================================


def call_vlm(
    *,
    model: str,
    system: str,
    content: str | list[dict[str, Any]],
    agent_name: str = "baseline",
    page_id: str = "",
) -> str:
    """Single VLM/LLM call with metrics recording."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    t0 = time.time()
    resp = completion(model=model, messages=messages)
    elapsed = time.time() - t0

    text = resp.choices[0].message.content or ""
    mc = get_metrics()
    if mc:
        mc.record_call(
            agent_name=agent_name,
            call_type="vlm",
            model=model,
            usage=_wrap_usage(resp.usage),
            elapsed_sec=elapsed,
            raw_output=text[:2000],
            page_id=page_id,
            system_prompt=system,
            user_prompt=str(content)[:500] if isinstance(content, str) else "(multi-image)",
        )

    logger.info(
        "[%s] VLM responded in %.1fs | in=%d out=%d | page=%s",
        agent_name, elapsed,
        resp.usage.prompt_tokens, resp.usage.completion_tokens,
        page_id or "-",
    )
    return text


# ======================================================================
# Response parsing
# ======================================================================

_FILE_TAG_RE = re.compile(
    r'<file\s+path="([^"]+)">\s*\n(.*?)\n\s*</file>',
    re.DOTALL,
)

_MARKDOWN_FILE_RE = re.compile(
    r'###\s+FILE:\s*(\S+)\s*\n```\w*\n(.*?)\n```',
    re.DOTALL,
)


def parse_files_from_response(text: str) -> dict[str, str]:
    """Extract file path → content mapping from LLM response.

    Supports multiple formats:
      1. <file path="...">code</file> tags
      2. ### FILE: path \\n ```code``` markdown blocks
      3. JSON object {"path": "content", ...}
    """
    files: dict[str, str] = {}

    for m in _FILE_TAG_RE.finditer(text):
        files[m.group(1)] = m.group(2).strip()
    if files:
        return files

    for m in _MARKDOWN_FILE_RE.finditer(text):
        files[m.group(1)] = m.group(2).strip()
    if files:
        return files

    try:
        data = _extract_json_object(text)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 10:
                    files[k] = v
    except (json.JSONDecodeError, ValueError):
        pass

    return files


def extract_code_block(text: str) -> str:
    """Extract code from the first fenced code block, or return text as-is."""
    m = re.search(r"```\w*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_page_from_file_tag(text: str) -> tuple[str, str]:
    """Extract (page_id, code) from a single <file> tagged response.

    Returns ("", code) as fallback if no <file> tag is found.
    """
    m = _FILE_TAG_RE.search(text)
    if not m:
        m = re.search(r'<file\s+path="([^"]+)">(.*?)</file>', text, re.DOTALL)
    if m:
        rel_path = m.group(1).lstrip("./")
        code = m.group(2).strip()
        page_id = normalize_page_id(Path(rel_path).stem)
        return page_id, code
    return "", extract_code_block(text)


def _extract_json_object(text: str) -> Any:
    """Best-effort JSON extraction (same logic as BaseAgent.extract_json)."""
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
        raise


# ======================================================================
# Scaffold conflict filtering
# ======================================================================


_SCAFFOLD_CONFLICT_RE = re.compile(
    r"^(postcss\.config|tailwind\.config|vite\.config|tsconfig)"
    r"\.(js|cjs|mjs|ts|json)$"
)


def filter_scaffold_conflicts(files: dict[str, str]) -> dict[str, str]:
    """Remove LLM-generated files that conflict with scaffold-managed configs.

    LLMs sometimes emit postcss.config.js / tailwind.config.js etc. which
    shadow the .cjs variants created by the scaffold, causing ESM/CJS
    mismatches at build time.
    """
    filtered: dict[str, str] = {}
    for rel_path, content in files.items():
        basename = Path(rel_path).name
        if _SCAFFOLD_CONFLICT_RE.match(basename):
            logger.info("dropping scaffold-conflicting file from LLM output: %s", rel_path)
            continue
        filtered[rel_path] = content
    return filtered


# ======================================================================
# Deterministic project assembly
# ======================================================================


def assemble_router(output_dir: str, page_ids: list[str], entry_point: str = "") -> str:
    """Generate src/router.tsx with routes for all pages."""
    if not entry_point:
        entry_point = page_ids[0] if page_ids else "home"

    imports = []
    routes = []
    for pid in page_ids:
        component_name = _to_component_name(pid)
        imports.append(f"import {component_name} from './pages/{pid}'")
        routes.append(f"    {{ path: '/{pid}', element: <{component_name} /> }},")

    home_component = _to_component_name(entry_point)
    routes.insert(0, f"    {{ path: '/', element: <{home_component} /> }},")

    code = (
        "import { createBrowserRouter, RouterProvider } from 'react-router-dom'\n"
        + "\n".join(imports)
        + "\n\nconst router = createBrowserRouter([\n"
        + "\n".join(routes)
        + "\n])\n\n"
        + "export default function Router() {\n"
        + "  return <RouterProvider router={router} />\n"
        + "}\n"
    )

    path = str(Path(output_dir) / "src" / "router.tsx")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(code, encoding="utf-8")
    logger.info("assembled router.tsx with %d pages", len(page_ids))
    return code


def assemble_app(output_dir: str) -> str:
    """Generate src/App.tsx that renders the router."""
    code = (
        "import Router from './router'\n\n"
        "export default function App() {\n"
        "  return <Router />\n"
        "}\n"
    )
    path = str(Path(output_dir) / "src" / "App.tsx")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(code, encoding="utf-8")
    logger.info("assembled App.tsx")
    return code


def assemble_types(output_dir: str, content: str = "") -> str:
    """Generate src/types.ts with shared type definitions."""
    if not content:
        content = "// Shared types — baseline generated (no cross-page inference)\nexport {}\n"
    path = str(Path(output_dir) / "src" / "types.ts")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")
    return content


def write_page_file(output_dir: str, page_id: str, code: str) -> str:
    """Write a single page component to src/pages/{page_id}.tsx."""
    path = Path(output_dir) / "src" / "pages" / f"{page_id}.tsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return str(path)


def write_source_files(output_dir: str, files: dict[str, str]) -> list[str]:
    """Write multiple source files. Keys are relative paths from output_dir."""
    written = []
    for rel_path, content in files.items():
        p = Path(output_dir) / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(str(p))
    logger.info("wrote %d source files", len(written))
    return written


# ======================================================================
# Scaffold & Build
# ======================================================================

_NPM_MAX_RETRIES = 3
_NPM_BACKOFF_BASE = 5
_NPM_FALLBACK_REGISTRY = "https://registry.npmmirror.com/"
_NPM_TRANSIENT_RE = re.compile(
    r"(E404|ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|FETCH_ERROR|ERR_SOCKET_TIMEOUT)",
    re.IGNORECASE,
)


def scaffold_and_build(output_dir: str) -> tuple[bool, str]:
    """Scaffold project infrastructure, install deps, and attempt build.

    Returns (success, message). No LLM fix loop — just reports the result.
    """
    scaffold_project(output_dir)

    install_ok, install_err = _npm_install(output_dir)
    if not install_ok:
        return False, f"npm install failed: {install_err}"

    tsc = run_command("npx tsc --noEmit", cwd=output_dir, timeout=120)
    if not tsc.success:
        return False, f"tsc failed (exit {tsc.return_code}):\n{tsc.output[:3000]}"

    vite = run_command("npx vite build", cwd=output_dir, timeout=120)
    if not vite.success:
        return False, f"vite build failed (exit {vite.return_code}):\n{vite.output[:3000]}"

    if not (Path(output_dir) / "dist").exists():
        return False, "Build commands passed but dist/ not created"

    return True, "Build succeeded"


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


# ======================================================================
# Helpers
# ======================================================================


def _to_component_name(page_id: str) -> str:
    """Convert kebab-case page ID to PascalCase component name."""
    return "".join(word.capitalize() for word in page_id.split("-")) + "Page"
