"""Test runner — executes generated test cases against a built project.

Two-phase architecture:
  Phase 1 (Automation): Playwright navigates pages, clicks elements, takes
      screenshots, judges navigation success. Pure browser automation, no API calls.
  Phase 2 (Visual Evaluation): After all navigation tests finish, batch-scores
      every screenshot pair against the reference design images via VLM-as-Judge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .actions import execute_action, set_vlm_model
from .route_resolver import get_tabbar_paths, resolve_routes

logger = logging.getLogger(__name__)


def _ensure_ld_library_path() -> None:
    """Add conda lib dirs to LD_LIBRARY_PATH so Playwright can find native libs."""
    import os
    import sys
    conda_prefix = os.environ.get("CONDA_PREFIX") or str(Path(sys.executable).parent.parent)
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


_VIEWPORT_WIDTH = 390
_VIEWPORT_HEIGHT = 844
_PREVIEW_STARTUP_TIMEOUT = 20.0
_PREVIEW_POLL_INTERVAL = 0.2
_PREVIEW_PORT_ENV_VAR = "TESTGEN_PREVIEW_PORT"
_PREVIEW_SERVER_ENV_VAR = "TESTGEN_SERVER"  # values: "auto" (default) | "vite" | "python"
_PAGE_LOAD_TIMEOUT = 10_000
_POST_ACTION_WAIT = 1500

_DIMENSION_WEIGHTS = {
    "layout": 0.30,
    "style": 0.25,
    "content": 0.25,
    "consistency": 0.20,
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def run_tests(
    cases_path: str | Path,
    run_dir: str | Path,
    input_dir: str | Path,
    *,
    report_path: str | Path | None = None,
    screenshots_dir: str | Path | None = None,
    vlm_model: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Phase 1: execute navigation tests and produce a report.

    Args:
        cases_path: Path to test_cases.json (output of generator).
        run_dir: Path to the generated project directory.
        input_dir: Path to the input directory with design screenshots.
        report_path: Where to write the report JSON.
        screenshots_dir: Where to write screenshots. Defaults to
            ``run_dir/eval/test_screenshots`` (legacy path) if not given.
        vlm_model: VLM model name for click-fallback (no visual scoring here).
        resume: If True, reuse already-passed cases from an existing
            ``report_path`` and only run the missing ones. Each completed case
            is flushed to disk incrementally so a crash loses at most one case.
    """
    run_dir = Path(run_dir)
    input_dir = Path(input_dir)
    cases_path = Path(cases_path)

    cases_data = json.loads(cases_path.read_text("utf-8"))
    cases: list[dict] = cases_data.get("cases", [])
    if not cases:
        logger.warning("No test cases found in %s", cases_path)
        return {"error": "no test cases"}

    # Resolve page_name → actual routes
    page_names = set()
    for c in cases:
        page_names.add(c["from_page"]["page_name"])
        page_names.add(c["to_page"]["page_name"])
    tab_structure = _load_tab_structure(input_dir)
    route_map = resolve_routes(
        run_dir, list(page_names), tab_structure=tab_structure,
    )
    # Visual tab order from TabBar.tsx source; used at click time to (a)
    # inject a ``target_route`` hint for ``a[href]``-based lookups and (b)
    # override the ``tab_position`` baked into the test case. Safe to be
    # empty — the click strategies all gracefully degrade.
    tabbar_paths = get_tabbar_paths(run_dir)

    set_vlm_model(vlm_model)

    if not _ensure_build(run_dir):
        return {"error": "build failed", "total_cases": len(cases), "passed": 0, "pass_rate": 0.0}

    if report_path is None:
        report_path = run_dir / "eval" / "test_report.json"
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if screenshots_dir is None:
        screenshots_dir = run_dir / "eval" / "test_screenshots"
    screenshots_dir = Path(screenshots_dir)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Resume: load prior per-case results and skip the passed ones
    prior_results: dict[str, dict[str, Any]] = {}
    if resume and report_path.is_file():
        try:
            prior_report = json.loads(report_path.read_text("utf-8"))
            for r in prior_report.get("details", []):
                tid = r.get("test_id")
                if tid and r.get("passed"):
                    prior_results[tid] = r
        except Exception as exc:
            logger.warning("Failed to load prior report for resume: %s", exc)

    pending_cases = [c for c in cases if c["test_id"] not in prior_results]
    if resume and prior_results:
        logger.info(
            "Resume: %d/%d cases already passed, running remaining %d",
            len(prior_results), len(cases), len(pending_cases),
        )

    def _flush(partial_results: list[dict[str, Any]]) -> None:
        report = _build_report(cases, partial_results, vlm_click_model=vlm_model)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    logger.info("═══ Phase 1: Navigation Testing ═══")
    new_results: list[dict[str, Any]] = []
    if pending_cases:
        new_results = _run_with_server(
            run_dir, pending_cases, route_map, screenshots_dir,
            tabbar_paths=tabbar_paths,
            on_progress=lambda r: _flush(
                list(prior_results.values()) + new_results + [r]
            ),
        )

    all_results = list(prior_results.values()) + new_results
    by_id = {r["test_id"]: r for r in all_results}
    ordered = [by_id[c["test_id"]] for c in cases if c["test_id"] in by_id]

    nav_passed = sum(1 for r in ordered if r.get("passed"))
    logger.info(
        "Phase 1 complete: %d/%d navigation tests passed", nav_passed, len(ordered),
    )

    report = _build_report(cases, ordered, vlm_click_model=vlm_model)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.info("Report written to %s", report_path)

    return report


def run_visual_evaluation(
    *,
    nav_report_path: str | Path,
    visual_report_path: str | Path,
    cases_path: str | Path,
    input_dir: str | Path,
    vlm_model: str,
    judge_tag: str | None = None,
    resume: bool = False,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """Phase 2: score navigation screenshots via VLM.

    Reads ``nav_report_path`` (read-only) for the list of test cases and
    screenshot paths, runs VLM scoring, then stores results under
    ``visual_report_path`` with shape:

        {
          "evaluated_at": ...,
          "visual_scores_by_judge": {
             "<judge_tag>": {
                "avg_before": ..., "avg_after": ...,
                "scored_pairs_before": ..., "scored_pairs_after": ...,
                "details": {
                   "<test_id>": {
                      "visual_similarity_before": 2.75,
                      "visual_detail_before": {...},
                      "visual_similarity_after": 2.9,
                      "visual_detail_after": {...}
                   }
                }
             },
             ...
          }
        }

    Multiple judges coexist in one file under different keys.

    If ``resume`` is True, any (test_id, field, judge) triple that already
    has a score is skipped.
    """
    nav_report_path = Path(nav_report_path)
    visual_report_path = Path(visual_report_path)
    cases_path = Path(cases_path)
    input_dir = Path(input_dir)

    if judge_tag is None:
        from evaluator.utils import sanitize_model_tag
        judge_tag = sanitize_model_tag(vlm_model)

    nav_report = json.loads(nav_report_path.read_text("utf-8"))
    cases_data = json.loads(cases_path.read_text("utf-8"))
    cases: list[dict] = cases_data.get("cases", [])
    nav_details: list[dict] = nav_report.get("details", [])

    if not nav_details:
        logger.warning("Navigation report %s has no details", nav_report_path)
        return {"visual_scores_by_judge": {}}

    visual_report_path.parent.mkdir(parents=True, exist_ok=True)
    visual_report: dict[str, Any] = {}
    if visual_report_path.is_file():
        try:
            visual_report = json.loads(visual_report_path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("Existing visual report unreadable, overwriting: %s", exc)
            visual_report = {}
    scores_by_judge = visual_report.setdefault("visual_scores_by_judge", {})
    judge_entry = scores_by_judge.setdefault(judge_tag, {})
    judge_details = judge_entry.setdefault("details", {})

    # Wrap per-test-case details so _run_visual_evaluation_batch can populate
    # them in-place via the existing field-based flow.
    scoring_rows: list[dict[str, Any]] = []
    for nd in nav_details:
        tid = nd.get("test_id")
        if not tid:
            continue
        row: dict[str, Any] = {
            "test_id": tid,
            "type": nd.get("type", ""),
            "screenshot_before": nd.get("screenshot_before"),
            "screenshot_after": nd.get("screenshot_after"),
        }
        # Pre-fill from existing judge_details so resume can skip them
        if resume:
            prior = judge_details.get(tid, {})
            for k in (
                "visual_similarity_before",
                "visual_similarity_after",
                "visual_detail_before",
                "visual_detail_after",
            ):
                if k in prior:
                    row[k] = prior[k]
        scoring_rows.append(row)

    logger.info(
        "═══ Visual Evaluation (model=%s, judge_tag=%s, resume=%s) ═══",
        vlm_model, judge_tag, resume,
    )
    _run_visual_evaluation_batch(
        scoring_rows, cases, input_dir, vlm_model,
        max_workers=max_workers, resume=resume,
    )

    # Write per-test-case scores back into judge_details
    for row in scoring_rows:
        tid = row["test_id"]
        entry = judge_details.setdefault(tid, {})
        for k in (
            "visual_similarity_before",
            "visual_similarity_after",
            "visual_detail_before",
            "visual_detail_after",
        ):
            if k in row:
                entry[k] = row[k]

    before_scores = [
        d.get("visual_similarity_before")
        for d in judge_details.values()
        if d.get("visual_similarity_before") is not None
    ]
    after_scores = [
        d.get("visual_similarity_after")
        for d in judge_details.values()
        if d.get("visual_similarity_after") is not None
    ]
    judge_entry.update({
        "model": vlm_model,
        "scored_pairs_before": len(before_scores),
        "scored_pairs_after": len(after_scores),
        "avg_before": round(sum(before_scores) / len(before_scores), 2) if before_scores else None,
        "avg_after": round(sum(after_scores) / len(after_scores), 2) if after_scores else None,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    })
    visual_report["evaluated_at"] = datetime.now().isoformat(timespec="seconds")

    visual_report_path.write_text(
        json.dumps(visual_report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.info("Visual report written to %s (judge=%s)", visual_report_path, judge_tag)

    return visual_report


# ═══════════════════════════════════════════════════════════════════════════
# Phase 0: Build
# ═══════════════════════════════════════════════════════════════════════════


def _load_tab_structure(input_dir: Path) -> list[str]:
    """Return the ordered ``tab_structure`` from ``metadata.reviewed.json``
    (preferred) or ``metadata.json``. Empty list when neither exists.

    ``tab_structure`` is the ordered list of tab page_names from the
    annotator; route_resolver uses it to position-align against the
    tabbar source, which is dramatically more reliable than matching
    against the full router. Silent-fallback to ``[]`` keeps the
    resolver in pure Tier 2 mode when the metadata is missing.
    """
    for name in ("metadata.reviewed.json", "metadata.json"):
        mdp = input_dir / name
        if not mdp.is_file():
            continue
        try:
            md = json.loads(mdp.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to parse %s: %s", mdp, exc)
            continue
        tabs = md.get("tab_structure") or []
        if isinstance(tabs, list):
            return [str(t) for t in tabs]
    return []


def _ensure_build(run_dir: Path) -> bool:
    """Ensure ``run_dir/dist/`` exists, building it on the fly if needed.

    Three branches:
      1. ``dist/`` already exists → use it as-is.
      2. ``dist/`` missing + local ``node_modules/.bin/vite`` available →
         run ``npx vite build`` like the legacy flow.
      3. ``dist/`` missing + no local vite → cannot build; emit an
         actionable error pointing at ``server_migrate/restore_node_modules.sh``
         (or to packing ``dist/`` into the migration tarball).
    """
    if (run_dir / "dist").is_dir():
        return True
    if not _has_local_vite(run_dir):
        logger.error(
            "dist/ missing AND no node_modules/.bin/vite in %s. "
            "Either restore node_modules (bash server_migrate/restore_node_modules.sh "
            "%s) or include dist/ in the migration tarball next time.",
            run_dir, run_dir,
        )
        return False
    logger.info("dist/ not found, running vite build...")
    try:
        proc = subprocess.run(
            ["npx", "vite", "build"],
            cwd=str(run_dir), capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            logger.warning("vite build failed: %s", proc.stderr[:500])
            return False
        return (run_dir / "dist").is_dir()
    except Exception as exc:
        logger.warning("vite build error: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1: Navigation Testing (pure browser automation)
# ═══════════════════════════════════════════════════════════════════════════


def _pick_free_port() -> int:
    """Return a localhost port that's currently free.

    ``TESTGEN_PREVIEW_PORT`` overrides this for debugging / fixed-port
    setups. Otherwise we ask the kernel for an ephemeral port via
    ``bind(('127.0.0.1', 0))`` and immediately release it. There's a
    small TOCTOU window between release and ``vite preview`` binding,
    but we guard against it with ``--strictPort`` + an active readiness
    probe, so a lost race fails fast with a clear error instead of
    hanging the way a squatting process does.
    """
    override = os.environ.get(_PREVIEW_PORT_ENV_VAR)
    if override:
        return int(override)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_preview(port: int, server: "_StaticPreview", timeout: float) -> bool:
    """Block until the static preview server is reachable, dies, or timeout.

    Generic over the two server backends (``_ViteSubprocessPreview`` and
    ``_PythonSPAPreview``): both expose ``poll()`` returning ``None`` while
    healthy. Healthy starts are confirmed with a TCP connect, since for the
    subprocess backend ``--strictPort`` makes port conflicts surface fast.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(_PREVIEW_POLL_INTERVAL)
    return False


# ───────────────────────────────────────────────────────────────────────────
# Static preview server abstraction
#
# Two backends with a uniform interface:
#   - ``_ViteSubprocessPreview``: spawns ``npx vite preview`` (legacy path,
#     used when local ``node_modules/.bin/vite`` is present).
#   - ``_PythonSPAPreview``: in-process stdlib HTTP server with SPA fallback
#     (used when node_modules is absent but ``dist/`` is intact — typical
#     after restoring a slim migration tarball that excluded node_modules).
#
# Selection logic lives in ``_start_preview_server``. Override with the
# ``TESTGEN_SERVER`` env var (``auto`` | ``vite`` | ``python``).
# ───────────────────────────────────────────────────────────────────────────


def _has_local_vite(run_dir: Path) -> bool:
    """Whether ``npx vite ...`` will resolve to the project's local install."""
    return (run_dir / "node_modules" / ".bin" / "vite").is_file()


class _StaticPreview:
    """Common interface for the two preview-server backends."""

    backend: str = "?"

    def poll(self) -> int | None:
        """Return None while serving, an int when stopped (mirrors Popen)."""
        raise NotImplementedError

    def stderr_tail(self) -> str:
        """Return up to ~500 chars of recent stderr for diagnostics."""
        return ""

    def stop(self) -> None:
        """Shut the server down (idempotent)."""
        raise NotImplementedError


class _ViteSubprocessPreview(_StaticPreview):
    """Wraps ``npx vite preview`` subprocess. Legacy / paper-reproduction path."""

    backend = "vite"

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    def poll(self) -> int | None:
        return self._proc.poll()

    def stderr_tail(self) -> str:
        try:
            _, err = self._proc.communicate(timeout=1)
            return (err or b"").decode("utf-8", errors="replace")[-500:]
        except Exception:
            return ""

    def stop(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()


class _SPARequestHandler(SimpleHTTPRequestHandler):
    """Static handler that mirrors ``vite preview``'s SPA fallback.

    Behaviour parity is the goal: vite preview unconditionally serves
    ``index.html`` for any path that has no matching file on disk (its
    ``historyApiFallback``-style middleware is on by default and applies
    even to asset paths like ``/assets/missing.js``). We do the same so
    Playwright sees the exact same response codes/bodies regardless of
    which backend is serving.
    """

    server_version = "TestgenSPA/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress per-request stderr noise; we have higher-level run logs.
        pass

    def send_head(self):
        fs_path = self.translate_path(self.path)
        if os.path.isfile(fs_path):
            return super().send_head()
        if os.path.isdir(fs_path) and os.path.isfile(os.path.join(fs_path, "index.html")):
            return super().send_head()
        # Missing path → SPA fallback (matches vite preview default).
        self.path = "/index.html"
        return super().send_head()


def _make_spa_handler(dist_dir: str):
    """Bind ``directory=dist_dir`` into a handler subclass for ThreadingHTTPServer."""

    class _Bound(_SPARequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=dist_dir, **kwargs)

    return _Bound


class _PythonSPAPreview(_StaticPreview):
    """In-process SPA static server. Zero npm/Node dependency."""

    backend = "python"

    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread):
        self._server = server
        self._thread = thread

    def poll(self) -> int | None:
        return None if self._thread.is_alive() else 1

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass


def _start_python_spa_preview(run_dir: Path, port: int) -> _PythonSPAPreview:
    dist_dir = run_dir / "dist"
    if not dist_dir.is_dir():
        raise RuntimeError(
            f"Cannot serve {run_dir}: dist/ missing and no local vite to rebuild. "
            f"Either include dist/ in the migration tarball or run "
            f"`bash server_migrate/restore_node_modules.sh {run_dir}`."
        )
    handler_cls = _make_spa_handler(str(dist_dir))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return _PythonSPAPreview(server, thread)


def _start_vite_subprocess_preview(run_dir: Path, port: int) -> _ViteSubprocessPreview:
    proc = subprocess.Popen(
        ["npx", "vite", "preview", "--port", str(port), "--strictPort"],
        cwd=str(run_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return _ViteSubprocessPreview(proc)


def _start_preview_server(run_dir: Path, port: int) -> _StaticPreview:
    """Factory: pick the right backend based on env + filesystem state.

    Override order:
      1. ``TESTGEN_SERVER=vite`` → force subprocess vite preview
      2. ``TESTGEN_SERVER=python`` → force Python SPA server
      3. ``TESTGEN_SERVER=auto`` (default, or unset) → local vite wins if present,
         otherwise fall back to Python.
    """
    mode = (os.environ.get(_PREVIEW_SERVER_ENV_VAR) or "auto").strip().lower()
    if mode == "vite":
        return _start_vite_subprocess_preview(run_dir, port)
    if mode == "python":
        return _start_python_spa_preview(run_dir, port)
    if mode not in ("auto", ""):
        logger.warning(
            "Unknown %s=%r, falling back to auto", _PREVIEW_SERVER_ENV_VAR, mode,
        )
    if _has_local_vite(run_dir):
        return _start_vite_subprocess_preview(run_dir, port)
    logger.info(
        "No local vite in %s/node_modules — serving dist/ via stdlib SPA server",
        run_dir,
    )
    return _start_python_spa_preview(run_dir, port)


def _run_with_server(
    run_dir: Path,
    cases: list[dict],
    route_map: dict[str, str],
    screenshots_dir: Path,
    *,
    tabbar_paths: list[str] | None = None,
    on_progress: "Any" = None,
) -> list[dict[str, Any]]:
    """Start vite preview, launch Playwright, execute each case.

    If ``on_progress`` is supplied it is invoked with each per-case result
    immediately after it's produced, so the caller can persist partial
    progress (for resume-ability) between cases.
    """
    _ensure_ld_library_path()
    preview_server: _StaticPreview | None = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright is not installed — cannot run tests")
        return [{"test_id": c["test_id"], "passed": False, "error": "playwright not installed"}
                for c in cases]

    try:
        port = _pick_free_port()
        try:
            preview_server = _start_preview_server(run_dir, port)
        except RuntimeError as exc:
            logger.error("%s", exc)
            return [{"test_id": c["test_id"], "passed": False, "error": str(exc)}
                    for c in cases]
        if not _wait_for_preview(port, preview_server, _PREVIEW_STARTUP_TIMEOUT):
            stderr_tail = preview_server.stderr_tail()
            logger.error(
                "preview server (%s backend) on port %d did not become reachable; "
                "stderr tail: %s",
                preview_server.backend, port, stderr_tail,
            )
            return [{"test_id": c["test_id"], "passed": False, "error": "preview server failed"}
                    for c in cases]

        base_url = f"http://localhost:{port}"
        logger.info("preview (%s) serving at %s", preview_server.backend, base_url)
        results: list[dict[str, Any]] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            for i, case in enumerate(cases, 1):
                result = _execute_single_case(
                    browser, base_url, case, route_map, screenshots_dir,
                    tabbar_paths=tabbar_paths or [],
                )
                results.append(result)
                status = "PASS" if result["passed"] else "FAIL"
                logger.info("[%d/%d] [%s] %s", i, len(cases), status, case["test_id"])
                if on_progress is not None:
                    try:
                        on_progress(result)
                    except Exception as exc:
                        logger.warning("on_progress callback failed: %s", exc)

            browser.close()

        return results

    except Exception as exc:
        logger.error("Test execution failed: %s", exc, exc_info=True)
        return [{"test_id": c["test_id"], "passed": False, "error": str(exc)} for c in cases]
    finally:
        if preview_server is not None:
            preview_server.stop()


def _execute_single_case(
    browser: Any,
    base_url: str,
    case: dict,
    route_map: dict[str, str],
    screenshots_dir: Path,
    *,
    tabbar_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Execute one test case in a fresh browser context.

    Only handles navigation + screenshots. No VLM visual scoring here.

    For ``tab_navigation`` cases we rewrite the action at runtime to
    inject two resolver-derived hints:

    - ``target_route``: the route we expect to land on. Enables the
      ``a[href=route]`` lookup in ``_execute_tab_click``, which is the
      most precise strategy for ``<Link>``-based tabbars.
    - ``tab_position`` override: we recompute the position from
      ``tabbar_paths.index(target_route) + 1``. This pins the position
      to the *visual* tab order (i.e. the TabBar source order) rather
      than the annotator's ``tab_structure`` order — they agree in the
      common ``N==M`` case but can differ when the Tier 1 resolver has
      to run Ordered-Subset DP.
    """
    test_id = case["test_id"]
    test_type = case["type"]

    result: dict[str, Any] = {
        "test_id": test_id,
        "type": test_type,
        "passed": False,
        "navigation_success": False,
        "screenshot_before": None,
        "screenshot_after": None,
        "error": None,
        "expected_route": None,
        "final_url": None,
    }

    context = browser.new_context(
        viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
        device_scale_factor=2,
    )
    page = context.new_page()
    js_errors: list[str] = []
    page.on("pageerror", lambda err: js_errors.append(str(err)))

    try:
        from_name = case["from_page"]["page_name"]
        to_name = case["to_page"]["page_name"]
        from_route = route_map.get(from_name, case["from_page"]["route_hint"])
        if not from_route.startswith("/"):
            from_route = f"/{from_route}"
        result["expected_route"] = from_route

        is_page_exists = test_type == "page_exists"

        # For page_exists we must NOT silently fall back: the whole point
        # of the test is "does this page actually exist?". For click-flow
        # tests we still fall back because the initial page is just a
        # starting point and the click target is what matters.
        effective_route = _goto_with_fallback(
            page, base_url, from_route, allow_fallback=not is_page_exists,
        )
        if effective_route != from_route:
            logger.info(
                "from_page '%s': route %s blank, using %s",
                from_name, from_route, effective_route,
            )

        if _is_blank_page(page):
            result["error"] = f"from_page '{from_name}' loaded as blank"
            result["final_url"] = page.url
            return result

        # Screenshot before
        before_path = screenshots_dir / f"{test_id}__before.png"
        page.screenshot(path=str(before_path), full_page=False)
        result["screenshot_before"] = str(before_path)
        result["final_url"] = page.url

        # page_exists: validate we actually landed on the expected route.
        # The old logic trivially returned passed=True here, which — combined
        # with the silent fallback above — caused many "tests" to pass by
        # screenshotting the wrong page (typically the site root). Now we
        # compare the final URL's path segment against the expected route.
        if is_page_exists:
            if not _path_matches_route(page.url, from_route):
                result["error"] = (
                    f"page_exists: expected {from_route}, "
                    f"landed on {_url_path(page.url)}"
                )
                return result
            result["navigation_success"] = True
            result["passed"] = True
            result["screenshot_after"] = result["screenshot_before"]
            return result

        # Record pre-action state
        url_before = page.url
        content_hash_before = _content_hash(page)

        # Rewrite the action for tab_navigation with resolver-derived hints.
        action_dict = case["action"]
        if test_type == "tab_navigation":
            to_route = route_map.get(to_name)
            action_dict = {**action_dict}
            if to_route:
                action_dict["target_route"] = to_route
                if tabbar_paths and to_route in tabbar_paths:
                    action_dict["tab_position"] = tabbar_paths.index(to_route) + 1

        # Execute action (with VLM fallback if configured)
        action_ok = execute_action(page, action_dict)
        if not action_ok:
            result["error"] = f"action failed: {case['action']['description']}"
            return result

        page.wait_for_timeout(_POST_ACTION_WAIT)

        # Screenshot after
        after_path = screenshots_dir / f"{test_id}__after.png"
        page.screenshot(path=str(after_path), full_page=False)
        result["screenshot_after"] = str(after_path)

        # Judge navigation success
        url_after = page.url
        content_hash_after = _content_hash(page)
        nav_success = (url_before != url_after) or (content_hash_before != content_hash_after)
        result["navigation_success"] = nav_success
        result["passed"] = nav_success

        if js_errors:
            result["js_errors"] = js_errors[:5]

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        context.close()

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: Visual Evaluation (batch VLM scoring, runs after all navigation)
# ═══════════════════════════════════════════════════════════════════════════


def _run_visual_evaluation_batch(
    results: list[dict[str, Any]],
    cases: list[dict],
    input_dir: Path,
    judge_model: str,
    max_workers: int | None = None,
    *,
    resume: bool = False,
) -> None:
    """Batch-score all collected screenshots against reference design images.

    Modifies results in-place, adding visual_similarity_before/after fields.
    If ``resume`` is True, (result, field) pairs that already have a score
    in the incoming ``results`` entries are skipped.
    """
    try:
        from .visual_judge import judge_pair
    except ImportError:
        logger.warning("testgen.visual_judge not importable, skipping Phase 2")
        return

    case_index = {c["test_id"]: c for c in cases}

    # Collect all (ref, impl) pairs to score
    score_tasks: list[dict] = []
    for result in results:
        test_id = result["test_id"]
        case = case_index.get(test_id)
        if not case:
            continue

        before_impl = result.get("screenshot_before")
        after_impl = result.get("screenshot_after")
        test_type = result.get("type", "")

        def _maybe_queue(field: str, detail_field: str, ref_path: Path, impl: str | None) -> None:
            if not (ref_path.is_file() and impl):
                return
            if resume and result.get(field) is not None:
                return
            score_tasks.append({
                "result": result,
                "field": field,
                "detail_field": detail_field,
                "ref": str(ref_path),
                "impl": impl,
            })

        if test_type == "page_exists":
            ref = case["from_page"]["reference_screenshot"]
            _maybe_queue(
                "visual_similarity_before", "visual_detail_before",
                input_dir / ref, before_impl,
            )
        else:
            from_ref = case["from_page"]["reference_screenshot"]
            _maybe_queue(
                "visual_similarity_before", "visual_detail_before",
                input_dir / from_ref, before_impl,
            )
            to_ref = case["to_page"]["reference_screenshot"]
            _maybe_queue(
                "visual_similarity_after", "visual_detail_after",
                input_dir / to_ref, after_impl,
            )

    if not score_tasks:
        logger.info("No screenshot pairs to score")
        return

    if max_workers is None:
        try:
            from designforge.config import _cfg
            max_workers = (_cfg.get("testgen") or {}).get("eval_workers")
        except Exception:
            pass
    max_workers = min(int(max_workers or 8), len(score_tasks))
    logger.info("Scoring %d screenshot pairs via VLM (%d workers)...", len(score_tasks), max_workers)

    import concurrent.futures

    def _score_one(task: dict) -> tuple[dict, dict | None]:
        ref_path = task["ref"]
        impl_path = task["impl"]
        if not Path(ref_path).is_file() or not Path(impl_path).is_file():
            return task, None
        return task, judge_pair(ref_path, impl_path, judge_model)

    scored = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_score_one, t): idx for idx, t in enumerate(score_tasks, 1)}

        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            task, scores = future.result()

            if scores is None:
                logger.warning("[%d/%d] VLM scoring failed for %s", idx, len(score_tasks), task["impl"])
                continue

            weighted = sum(
                scores.get(dim, 3) * weight
                for dim, weight in _DIMENSION_WEIGHTS.items()
            )

            result_dict = task["result"]
            result_dict[task["field"]] = round(weighted, 2)
            result_dict[task["detail_field"]] = {
                "layout": scores.get("layout", 0),
                "style": scores.get("style", 0),
                "content": scores.get("content", 0),
                "consistency": scores.get("consistency", 0),
                "issues": scores.get("issues", []),
            }

            scored += 1
            if scored % 5 == 0 or scored == len(score_tasks):
                logger.info("Visual scoring progress: %d/%d done", scored, len(score_tasks))

    # For page_exists, copy before → after
    for result in results:
        if result.get("type") == "page_exists":
            if result.get("visual_similarity_before") is not None:
                result.setdefault("visual_similarity_after", result["visual_similarity_before"])
            if result.get("visual_detail_before") is not None:
                result.setdefault("visual_detail_after", result["visual_detail_before"])

    logger.info("Phase 2 complete: scored %d/%d pairs", scored, len(score_tasks))


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


_ERROR_PATTERNS = re.compile(
    r"404\s*Not\s*Found|Unexpected\s*Application\s*Error|Cannot\s+GET|Page\s+not\s+found",
    re.IGNORECASE,
)


def _is_blank_page(page: Any) -> bool:
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


def _url_path(url: str) -> str:
    """Extract the pathname from a full URL, normalized to a leading '/'.

    Handles both regular ``/foo`` paths and hash-based SPA routing
    (``#/foo`` or ``/#/foo``). Returns ``/`` for the bare site root.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path or "/"
    # Hash-routed SPAs: the real path lives in the fragment.
    if parsed.fragment.startswith("/"):
        path = parsed.fragment
    path = path.rstrip("/") or "/"
    return path


def _path_matches_route(url: str, expected_route: str) -> bool:
    """True when the browser's final URL path corresponds to ``expected_route``.

    Allows:
      - exact match (``/foo`` == ``/foo``)
      - nested match (``/foo/bar`` counts as being on ``/foo``, which is
        what React-Router's ``Outlet`` pattern produces for tab layouts)
      - trailing-slash and query-string insensitivity

    Does NOT allow the ``_goto_with_fallback``-to-``/`` case: if the
    expected route was ``/audio`` but the browser is at ``/``, that's
    the silent-fallback we're trying to catch, and this returns False.
    """
    actual = _url_path(url)
    expected = expected_route.rstrip("/") or "/"
    if actual == expected:
        return True
    if expected == "/":
        # Expected root, anything non-root means we went deeper: not a match.
        return actual == "/"
    return actual.startswith(expected + "/")


def _goto_with_fallback(
    page: Any,
    base_url: str,
    route: str,
    *,
    allow_fallback: bool = True,
) -> str:
    """Navigate to ``route``; optionally fall back to ``/`` on blank/error.

    History: the fallback existed because the pre-resolver code often
    guessed routes that didn't exist (e.g. ``/explore`` when the project
    uses ``/``). With the tabbar-aware route_resolver in place, the
    guess is almost always correct; keeping the fallback unconditionally
    masks real bugs.

    Semantics now:

      - ``allow_fallback=True`` (default, for ``tab_navigation`` and
        ``parent_child_navigation``): if the resolved route renders
        blank, silently fall back to ``/`` so the click-flow test can
        still proceed. Returns the route that actually loaded.
      - ``allow_fallback=False`` (for ``page_exists``): do not fall
        back. If the route renders blank the caller sees it and fails
        the test honestly. Returns the requested route unchanged so
        the caller can compare against the final URL.
    """
    page.goto(f"{base_url}{route}", wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
    page.wait_for_timeout(500)

    if not _is_blank_page(page):
        return route

    if allow_fallback and route != "/":
        logger.info("Route %s loaded blank, falling back to /", route)
        page.goto(f"{base_url}/", wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
        page.wait_for_timeout(500)
        if not _is_blank_page(page):
            return "/"

    return route


def _content_hash(page: Any) -> str:
    try:
        html = page.inner_html("body", timeout=2000)
        return hashlib.md5(html.encode()).hexdigest()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# Report builder
# ═══════════════════════════════════════════════════════════════════════════


def _build_report(
    cases: list[dict],
    results: list[dict[str, Any]],
    vlm_click_model: str | None = None,
) -> dict[str, Any]:
    """Build the navigation (Phase 1) report. Visual scoring lives in a
    separate report written by :func:`run_visual_evaluation`.
    """
    total = len(cases)
    passed = sum(1 for r in results if r.get("passed"))

    by_type: dict[str, dict[str, Any]] = {}
    for r in results:
        t = r.get("type", "unknown")
        if t not in by_type:
            by_type[t] = {"total": 0, "passed": 0}
        by_type[t]["total"] += 1
        if r.get("passed"):
            by_type[t]["passed"] += 1

    for stats in by_type.values():
        stats["rate"] = round(stats["passed"] / stats["total"], 4) if stats["total"] else 0.0

    return {
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "vlm_click_model": vlm_click_model,
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "by_type": by_type,
        "details": results,
    }
