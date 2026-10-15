"""HTTP server for the human-eval / VLM-consistency review platform.

Two server roles share this module:

* The *dashboard* server (single instance) serves the UI + REST API.
* The *preview* servers (one per active run) serve each run's built
  ``dist/`` directory with SPA-style fallback to ``index.html``. They run on
  ephemeral ports inside background threads so the iframe in the browser can
  point at them directly.

The implementation is deliberately dependency-free (stdlib only) so it can run
in the same environment as the rest of the evaluator without extra installs.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Filename prefix marking the *evaluation target* of a human review.
# Files saved by this dashboard look like:
#   <run>/human-eval/<CLICK_TARGET_PREFIX><vlm>.json
#   <run>/human-eval/<CLICK_TARGET_PREFIX><vlm>_test_report.json
#   <run>/human-eval/<VISUAL_TARGET_PREFIX><judge>.json
#
# We use the filename itself (rather than a JSON metadata field) to discriminate
# different evaluation targets so a click-model review and a judge-model review
# (which may even share a VLM model name) never collide.
CLICK_TARGET = "vlm_click_model"
CLICK_TARGET_PREFIX = f"{CLICK_TARGET}__"

VISUAL_TARGET = "visual_judge_model"
VISUAL_TARGET_PREFIX = f"{VISUAL_TARGET}__"

# Backwards-compat aliases (older code/tests may import the old names).
EVAL_TARGET = CLICK_TARGET
EVAL_TARGET_PREFIX = CLICK_TARGET_PREFIX

# 4-dimension visual judge scoring (mirrors testgen.visual_judge.DIMENSION_WEIGHTS).
# Kept inline so this module has zero import-time dependency on testgen.
VISUAL_DIMENSIONS = ("layout", "style", "content", "consistency")
VISUAL_DIMENSION_WEIGHTS = {
    "layout": 0.30,
    "style": 0.25,
    "content": 0.25,
    "consistency": 0.20,
}


def _weighted_visual_score(scores: dict[str, Any] | None) -> float | None:
    """Return weighted 1-5 score from a dict of {dim: int}, or None if invalid."""
    if not isinstance(scores, dict):
        return None
    total = 0.0
    seen = 0
    for dim, weight in VISUAL_DIMENSION_WEIGHTS.items():
        v = scores.get(dim)
        if isinstance(v, (int, float)) and 1 <= v <= 5:
            total += float(v) * weight
            seen += 1
    if seen == 0:
        return None
    return round(total, 2)


# ---------------------------------------------------------------------------
# Preview server (per-run, serves the built SPA from dist/)
# ---------------------------------------------------------------------------


class _ReusableThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer with SO_REUSEADDR explicitly forced on."""

    allow_reuse_address = True
    if hasattr(socket, "SO_REUSEPORT"):
        allow_reuse_port = True


_NO_APP_HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>No app</title>"
    b"<style>body{font-family:sans-serif;color:#666;text-align:center;padding-top:80px;}"
    b"h2{color:#999;font-weight:400;}</style></head>"
    b"<body><h2>(no app currently selected)</h2>"
    b"<p>Click <b>\u25b6 \u542f\u52a8 App</b> in the dashboard to load a run.</p>"
    b"</body></html>"
)


class _PreviewServer:
    """A single, long-lived SPA preview server.

    Rather than stop+restart the server every time the user switches runs
    (which would briefly free the port and confuse SSH/Cursor port-forwarding
    tunnels), we keep one HTTP listener running for the lifetime of the
    process and just swap which ``dist/`` directory it serves. The user-facing
    URL stays stable, the kernel never re-binds the port, and the tunnel
    proxy keeps its upstream connection alive across switches.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 0) -> None:
        self.host = host
        # Active state — mutated by ``serve_run`` / ``clear`` from any thread,
        # read by every request handler. ``str`` assignment is atomic enough
        # for our purposes; we don't need a lock here.
        self.current_dist_dir: str | None = None
        self.current_run_id: str | None = None

        srv = self  # closure for the handler

        class _Handler(http.server.SimpleHTTPRequestHandler):
            # SimpleHTTPRequestHandler.__init__ takes ``directory``, but it
            # captures it once and uses ``self.directory`` for translate_path.
            # We override translate_path entirely to read from ``srv`` on every
            # request, so directory swaps take effect immediately.

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                # Provide a placeholder dir; translate_path doesn't actually use it.
                super().__init__(*args, directory=str(Path.cwd()), **kwargs)

            def translate_path(self, path: str) -> str:
                root = srv.current_dist_dir or str(Path.cwd())
                self.directory = root
                return super().translate_path(path)

            def end_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                super().end_headers()

            def send_head(self):  # type: ignore[override]
                if srv.current_dist_dir is None:
                    body = _NO_APP_HTML
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return None  # caller skips body
                parsed = urllib.parse.urlparse(self.path)
                file_path = self.translate_path(parsed.path)
                if (not os.path.exists(file_path)
                        and not parsed.path.startswith(("/assets/", "/favicon"))):
                    self.path = "/index.html"
                return super().send_head()

            def do_GET(self) -> None:  # noqa: N802
                if srv.current_dist_dir is None:
                    # Custom no-app screen instead of routing through send_head's
                    # SPA fallback (which would 404 with no dist/ to fall back to).
                    body = _NO_APP_HTML
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.httpd = _ReusableThreadingHTTPServer((host, port), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="preview-server",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()
        # Probe the socket so callers know the server is actually accepting
        # connections by the time start() returns.
        deadline = time.monotonic() + 2.0
        probe_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((probe_host, self.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.05)

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def serve_run(self, run_id: str, dist_dir: Path) -> None:
        """Switch which dist/ directory the server serves (no restart)."""
        self.current_run_id = run_id
        self.current_dist_dir = str(dist_dir)

    def clear(self) -> None:
        self.current_run_id = None
        self.current_dist_dir = None


# ---------------------------------------------------------------------------
# Dashboard state — owns experiment dir + active previews
# ---------------------------------------------------------------------------


class DashboardState:
    """Owns the experiment dir, active preview servers, and persistence I/O.

    The web handler is stateless; everything goes through this object so we
    can exit cleanly (stop all previews) on Ctrl-C.
    """

    def __init__(
        self,
        experiment_dir: Path,
        *,
        preview_host: str = "0.0.0.0",
        preview_port: int = 0,
    ) -> None:
        self.experiment_dir = experiment_dir.resolve()
        self.runs_dir = self.experiment_dir / "runs"
        self.preview_host = preview_host
        self.preview_port = preview_port  # 0 = ephemeral, otherwise fixed
        # Single, long-lived preview server. Lazily created on first launch
        # so that nothing is listening on the preview port until the user
        # actually wants a preview (also keeps `--port + 1` collision-free
        # for users who never use the preview feature).
        self._preview: _PreviewServer | None = None
        self._lock = threading.Lock()
        # Cache: (app_name) -> { test_id -> action_dict | None }
        # action lookup is read-only and identical across runs for a given app,
        # so we read each test_cases.reviewed.json (or .json fallback) at most
        # once per process.
        self._action_cache: dict[str, dict[str, Any]] = {}
        # Cache: (app_name) -> raw cases list (for visual-judge mode which
        # needs reference_screenshot paths).
        self._cases_cache: dict[str, list[dict[str, Any]]] = {}
        # Allowed roots for /screenshot. Always includes the experiment dir;
        # input/ roots get appended lazily as we resolve each app so the UI
        # can also serve design reference images for the visual-judge mode.
        self._image_roots: list[str] = [str(self.experiment_dir)]
        # Cache: (app_name) -> resolved input_dir | None
        self._input_dir_cache: dict[str, Path | None] = {}

    # ----- run discovery ---------------------------------------------------

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for run_dir in sorted(self.runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            has_dist = (run_dir / "dist").is_dir()
            nav_dir = run_dir / "eval" / "navigation_test"
            visual_dir = run_dir / "eval" / "navigation_visual"
            vlms: list[str] = []
            if nav_dir.is_dir():
                for vlm_dir in sorted(nav_dir.iterdir()):
                    if vlm_dir.is_dir() and (vlm_dir / "test_report.json").is_file():
                        vlms.append(vlm_dir.name)
            # judges_by_click[click_tag] = list of judge_tags found in
            # navigation_visual/<click_tag>/visual_report.json. Empty when the
            # visual evaluator hasn't been run yet for this run.
            judges_by_click: dict[str, list[str]] = {}
            if visual_dir.is_dir():
                for click_dir in sorted(visual_dir.iterdir()):
                    if not click_dir.is_dir():
                        continue
                    vrep = click_dir / "visual_report.json"
                    if not vrep.is_file():
                        continue
                    try:
                        rep = json.loads(vrep.read_text("utf-8"))
                    except Exception:
                        continue
                    judges = list((rep.get("visual_scores_by_judge") or {}).keys())
                    if judges:
                        judges_by_click[click_dir.name] = sorted(judges)
            # Skip runs that have neither a click-model report nor a visual
            # report — there's nothing to review.
            if not vlms and not judges_by_click:
                continue
            parts = run_dir.name.split("__")
            out.append({
                "run_id": run_dir.name,
                "app": parts[0] if parts else run_dir.name,
                "model": parts[1] if len(parts) > 1 else "",
                "method": parts[2] if len(parts) > 2 else "",
                "vlms": vlms,
                "judges_by_click": judges_by_click,
                "has_dist": has_dist,
            })
        return out

    # ----- preview lifecycle ----------------------------------------------

    def _ensure_preview_server(self) -> _PreviewServer:
        """Create the single preview server on first use, then reuse forever.

        The server lives on a fixed port for the rest of the dashboard's
        lifetime. Switching runs only swaps the served directory, so any
        SSH/Cursor port-forwarding tunnel keeps a single, stable upstream.
        """
        if self._preview is not None:
            return self._preview
        try:
            srv = _PreviewServer(host=self.preview_host, port=self.preview_port)
        except OSError as exc:
            if getattr(exc, "errno", None) == 98 and self.preview_port:
                holder = _find_port_holder(self.preview_port)
                raise RuntimeError(
                    f"预览端口 {self.preview_port} 被占用，无法启动 app 预览。"
                    + (f" 占用者: {holder}" if holder else "")
                ) from exc
            raise
        srv.start()
        self._preview = srv
        logger.info("Preview server bound on port %d (single, long-lived)", srv.port)
        return srv

    def launch_preview(self, run_id: str, *, exclusive: bool = True) -> int:
        """Point the preview server at ``run_id`` and return its port.

        Replaces any currently-served run (only one at a time). The HTTP
        listener itself is not restarted, so callers (and any port-forwarding
        proxy) see a continuous TCP service on the preview port.
        """
        with self._lock:
            run_dir = self.runs_dir / run_id
            dist_dir = run_dir / "dist"
            if not dist_dir.is_dir():
                raise FileNotFoundError(f"dist/ not found for run '{run_id}' at {dist_dir}")
            srv = self._ensure_preview_server()
            srv.serve_run(run_id, dist_dir)
            logger.info("Preview now serving %s (port %d)", run_id, srv.port)
            return srv.port

    def stop_preview(self, run_id: str | None = None) -> bool:
        """Detach the current run from the preview (server keeps running)."""
        with self._lock:
            if self._preview is None or self._preview.current_run_id is None:
                return False
            logger.info("Preview detached from %s", self._preview.current_run_id)
            self._preview.clear()
            return True

    def stop_all(self) -> None:
        with self._lock:
            if self._preview is not None:
                self._preview.stop()
                self._preview = None

    def previews_status(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._preview is None or self._preview.current_run_id is None:
                return []
            return [{
                "run_id": self._preview.current_run_id,
                "port": self._preview.port,
            }]

    # ----- cases I/O ------------------------------------------------------

    def _human_eval_path(self, run_id: str, vlm: str) -> Path:
        return self.runs_dir / run_id / "human-eval" / f"{CLICK_TARGET_PREFIX}{vlm}.json"

    def _visual_human_path(self, run_id: str, judge_tag: str) -> Path:
        """Where the human visual-judge ratings for a (run, judge) live."""
        return (
            self.runs_dir / run_id / "human-eval"
            / f"{VISUAL_TARGET_PREFIX}{judge_tag}.json"
        )

    def _visual_human_report_path(self, run_id: str, judge_tag: str) -> Path:
        return (
            self.runs_dir / run_id / "human-eval"
            / f"{VISUAL_TARGET_PREFIX}{judge_tag}_visual_report.json"
        )

    def _find_input_dir(self, app_name: str) -> Path | None:
        """Locate the app's input directory.

        Layout convention: ``<project_root>/input/<dataset>/<app>/`` (e.g.
        ``input/mobileforge/coinbase``). We try a few standard places without
        assuming a specific dataset folder name, so the platform works for any
        dataset that follows the AgenticForge layout.
        """
        if app_name in self._input_dir_cache:
            return self._input_dir_cache[app_name]
        # project root = parent of the experiments dir we were given
        candidates: list[Path] = []
        proj_root = self.experiment_dir.parent
        for input_root in (proj_root / "input", proj_root.parent / "input"):
            if not input_root.is_dir():
                continue
            candidates.append(input_root / app_name)
            for sub in input_root.iterdir():
                if sub.is_dir():
                    candidates.append(sub / app_name)
        found: Path | None = None
        for c in candidates:
            if (c / "test_cases.json").is_file() or (c / "test_cases.reviewed.json").is_file():
                found = c.resolve()
                break
        self._input_dir_cache[app_name] = found
        if found is not None:
            # Register the app's input dir as an allowed image root so the
            # visual-judge UI can render its reference_screenshot files via
            # the same /screenshot endpoint we use for impl screenshots.
            root = str(found)
            if root not in self._image_roots:
                self._image_roots.append(root)
        return found

    def _load_actions(self, app_name: str) -> dict[str, Any]:
        """Return ``{test_id: action_dict}`` for an app (cached, lazy).

        As a side effect, also caches the raw cases list under
        ``self._cases_cache[app_name]`` so the visual-judge mode can pick up
        ``reference_screenshot`` paths without re-reading the file.
        """
        if app_name in self._action_cache:
            return self._action_cache[app_name]

        actions: dict[str, Any] = {}
        raw_cases: list[dict[str, Any]] = []
        input_dir = self._find_input_dir(app_name)
        if input_dir is not None:
            for fname in ("test_cases.reviewed.json", "test_cases.json"):
                path = input_dir / fname
                if not path.is_file():
                    continue
                try:
                    data = json.loads(path.read_text("utf-8"))
                    raw_cases = data.get("cases", []) or []
                    for c in raw_cases:
                        tid = c.get("test_id")
                        if tid is None:
                            continue
                        actions[tid] = {
                            "description": (c.get("action") or {}).get("description"),
                            "strategy": (c.get("action") or {}).get("strategy"),
                            "tab_position": (c.get("action") or {}).get("tab_position"),
                            "text_hints": (c.get("action") or {}).get("text_hints") or [],
                            "dom_selector_hints": (c.get("action") or {}).get("dom_selector_hints") or [],
                            "from_page": c.get("from_page"),
                            "to_page": c.get("to_page"),
                        }
                    break  # prefer .reviewed if both exist
                except Exception as exc:
                    logger.warning("Failed to parse %s: %s", path, exc)

        self._action_cache[app_name] = actions
        self._cases_cache[app_name] = raw_cases
        return actions

    def load_cases(self, run_id: str, vlm: str) -> dict[str, Any] | None:
        report_path = (
            self.runs_dir / run_id / "eval" / "navigation_test" / vlm / "test_report.json"
        )
        if not report_path.is_file():
            return None
        report = json.loads(report_path.read_text("utf-8"))

        existing_human: dict[str, Any] = {}
        existing_notes: dict[str, str] = {}
        he_path = self._human_eval_path(run_id, vlm)
        if he_path.is_file():
            try:
                hdata = json.loads(he_path.read_text("utf-8"))
                for c in hdata.get("cases", []):
                    tid = c.get("test_id")
                    if tid is None:
                        continue
                    # Read the new English keys, falling back to the legacy
                    # Chinese ones so files written before the rename still
                    # load. Writes always use the new keys (see save_cases).
                    if "human_verdict" in c:
                        existing_human[tid] = c.get("human_verdict")
                    else:
                        existing_human[tid] = c.get("人工评价")
                    note = c.get("human_note") or c.get("人工备注")
                    if note:
                        existing_notes[tid] = note
            except Exception as exc:
                logger.warning("Failed to parse existing human-eval: %s", exc)

        # Look up actions from the app's test_cases.reviewed.json (preferred)
        # or test_cases.json (fallback), lazily cached per app.
        app_name = run_id.split("__", 1)[0]
        actions = self._load_actions(app_name)

        cases: list[dict[str, Any]] = []
        for d in report.get("details", []):
            tid = d.get("test_id")
            vlm_passed = bool(d.get("passed", False))
            if tid in existing_human:
                he = existing_human[tid]
            else:
                # default rule: VLM-passed cases default to True (人工默认沿用),
                # VLM-failed cases default to None (待人工评估).
                he = True if vlm_passed else None
            action_info = actions.get(tid) or {}
            cases.append({
                "test_id": tid,
                "type": d.get("type"),
                "expected_route": d.get("expected_route"),
                "final_url": d.get("final_url"),
                "navigation_success": d.get("navigation_success"),
                "error": d.get("error"),
                "screenshot_before": d.get("screenshot_before"),
                "screenshot_after": d.get("screenshot_after"),
                "vlm_passed": vlm_passed,
                "action": {
                    "description": action_info.get("description"),
                    "strategy": action_info.get("strategy"),
                    "tab_position": action_info.get("tab_position"),
                    "text_hints": action_info.get("text_hints") or [],
                    "dom_selector_hints": action_info.get("dom_selector_hints") or [],
                },
                "from_page": action_info.get("from_page"),
                "to_page": action_info.get("to_page"),
                "human_verdict": he,
                "human_note": existing_notes.get(tid, ""),
            })

        return {
            "run_id": run_id,
            "vlm_click_model": vlm,
            "report_summary": {
                "total": report.get("total_cases", len(cases)),
                "vlm_passed": report.get("passed", 0),
                "vlm_failed": report.get("failed", 0),
                "vlm_pass_rate": report.get("pass_rate", 0),
                "by_type": report.get("by_type", {}),
            },
            "cases": cases,
            "human_eval_file": str(self._human_eval_path(run_id, vlm)),
            # Tells the frontend whether anything has ever been written to disk
            # for this (run, vlm) pair. Needed so the Save button can stay
            # enabled for clean runs (where no case needs human edits and
            # therefore "dirty" never flips to true) — otherwise such runs
            # could never be persisted to human-eval/.
            "human_eval_exists": he_path.is_file(),
        }

    def save_cases(
        self,
        run_id: str,
        vlm: str,
        cases: list[dict[str, Any]],
    ) -> dict[str, Path]:
        """Persist per-case + per-run-summary JSON files.

        Returns a dict mapping logical name → file path so the frontend can
        report exactly what was written.
        """
        cases_path = self._human_eval_path(run_id, vlm)
        cases_path.parent.mkdir(parents=True, exist_ok=True)

        # ---- per-case file (full details, what we already had) ----
        judged = 0
        agree = 0
        human_pass = 0
        human_fail = 0
        for c in cases:
            he = c.get("human_verdict")
            vlm_p = c.get("vlm_passed")
            if he is None:
                continue
            judged += 1
            if bool(he) == bool(vlm_p):
                agree += 1
            if he:
                human_pass += 1
            else:
                human_fail += 1

        # NOTE: we deliberately do NOT add an ``evaluation_target`` field here —
        # the filename prefix (``vlm_click_model__``) already identifies the
        # evaluation target, and duplicating it inside the JSON would just
        # introduce a way for the two to disagree.
        cases_payload = {
            "run_id": run_id,
            "evaluated_by": "human",
            "vlm_click_model": vlm,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "total_cases": len(cases),
                "judged": judged,
                "human_pass": human_pass,
                "human_fail": human_fail,
                "agree_with_vlm_click_model": agree,
                "agreement_rate": round(agree / judged, 4) if judged else None,
            },
            "cases": cases,
        }
        cases_path.write_text(
            json.dumps(cases_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        # ---- summary report file (mirrors test_report.json structure) ----
        report_path = self._human_report_path(run_id, vlm)
        report_payload = self._build_human_report(run_id, vlm, cases)
        report_path.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        return {"cases": cases_path, "report": report_path}

    def _human_report_path(self, run_id: str, vlm: str) -> Path:
        return (
            self.runs_dir / run_id / "human-eval"
            / f"{EVAL_TARGET_PREFIX}{vlm}_test_report.json"
        )

    @staticmethod
    def _build_human_report(
        run_id: str,
        vlm: str,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute a `test_report.json`-shaped summary based on ``human_verdict``.

        Keeps the same top-level keys as the VLM `test_report.json` so the two
        files can be diffed / diffed-by-tooling, and adds a
        ``vs_vlm_click_model`` block that captures the human ↔ click-model
        consistency for this run. The ``vs_vlm_click_model`` (rather than
        ``vs_vlm``) naming is intentional: it leaves room for a future
        ``vs_vlm_judge_model`` block when human review is also used to audit
        the judge model.
        """
        total = len(cases)
        passed = sum(1 for c in cases if c.get("human_verdict") is True)
        failed = sum(1 for c in cases if c.get("human_verdict") is False)
        pending = sum(1 for c in cases if c.get("human_verdict") is None)
        judged = passed + failed

        # ---- per-type breakdown ----
        by_type: dict[str, dict[str, Any]] = {}
        for c in cases:
            ttype = c.get("type") or "unknown"
            slot = by_type.setdefault(ttype, {
                "total": 0, "passed": 0, "failed": 0, "pending": 0,
            })
            slot["total"] += 1
            he = c.get("human_verdict")
            if he is True:
                slot["passed"] += 1
            elif he is False:
                slot["failed"] += 1
            else:
                slot["pending"] += 1
        for slot in by_type.values():
            slot["rate"] = (
                round(slot["passed"] / slot["total"], 4) if slot["total"] else None
            )

        # ---- VLM-vs-human consistency ----
        agree = 0
        disagree = 0
        vlm_pass = 0
        for c in cases:
            if c.get("vlm_passed"):
                vlm_pass += 1
            he = c.get("human_verdict")
            if he is None:
                continue
            if bool(he) == bool(c.get("vlm_passed")):
                agree += 1
            else:
                disagree += 1

        # As in save_cases(), no ``evaluation_target`` field — the filename
        # prefix (``vlm_click_model__``) is the single source of truth for
        # which VLM role this review targets.
        return {
            "run_id": run_id,
            "evaluated_by": "human",
            "vlm_click_model": vlm,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "pass_rate": round(passed / total, 4) if total else None,
            "pass_rate_judged_only": round(passed / judged, 4) if judged else None,
            "by_type": by_type,
            "vs_vlm_click_model": {
                "compared_against": "vlm_click_model",
                "model": vlm,
                "judged": judged,
                "agree": agree,
                "disagree": disagree,
                "agreement_rate": round(agree / judged, 4) if judged else None,
                "vlm_passed": vlm_pass,
                "vlm_pass_rate": round(vlm_pass / total, 4) if total else None,
            },
        }

    # ----- visual judge cases I/O -----------------------------------------

    def _load_visual_report(
        self,
        run_id: str,
        click_tag: str,
    ) -> dict[str, Any] | None:
        """Read the auto visual_report.json for a (run, click_tag), or None."""
        path = (
            self.runs_dir / run_id / "eval" / "navigation_visual"
            / click_tag / "visual_report.json"
        )
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return None

    @staticmethod
    def _normalise_judge_scores(scores: Any) -> dict[str, Any] | None:
        """Coerce a raw judge dict into ``{layout,style,content,consistency,issues,weighted}``.

        Returns ``None`` if no usable dim scores are present.
        """
        if not isinstance(scores, dict):
            return None
        out: dict[str, Any] = {}
        any_dim = False
        for dim in VISUAL_DIMENSIONS:
            v = scores.get(dim)
            if isinstance(v, (int, float)) and 1 <= v <= 5:
                out[dim] = int(v)
                any_dim = True
            else:
                out[dim] = None
        if not any_dim:
            return None
        out["issues"] = list(scores.get("issues") or [])
        out["weighted"] = _weighted_visual_score(out)
        return out

    def load_visual_cases(
        self,
        run_id: str,
        judge_tag: str,
        click_tag: str,
    ) -> dict[str, Any] | None:
        """Build the per-case payload for the visual-judge review UI.

        Combines:
          * navigation_test/<click_tag>/test_report.json       — impl screenshots
          * navigation_visual/<click_tag>/visual_report.json   — auto judge scores
            (may be missing — UI still works, model_* will be null)
          * input/.../<app>/test_cases(.reviewed).json         — design refs
          * human-eval/visual_judge_model__<judge>.json        — prior human ratings
        """
        nav_path = (
            self.runs_dir / run_id / "eval" / "navigation_test"
            / click_tag / "test_report.json"
        )
        if not nav_path.is_file():
            return None
        nav_report = json.loads(nav_path.read_text("utf-8"))

        visual_report = self._load_visual_report(run_id, click_tag) or {}
        judge_entry = (
            (visual_report.get("visual_scores_by_judge") or {}).get(judge_tag) or {}
        )
        judge_details = judge_entry.get("details") or {}

        # Load app-level data (actions, raw cases for reference_screenshot).
        app_name = run_id.split("__", 1)[0]
        self._load_actions(app_name)  # populates _cases_cache as a side-effect
        raw_cases = self._cases_cache.get(app_name, [])
        case_index = {c.get("test_id"): c for c in raw_cases if c.get("test_id")}

        input_dir = self._find_input_dir(app_name)

        # Existing human ratings for this (run, judge_tag).
        existing_human: dict[str, dict[str, Any]] = {}
        existing_notes: dict[str, str] = {}
        h_path = self._visual_human_path(run_id, judge_tag)
        if h_path.is_file():
            try:
                hdata = json.loads(h_path.read_text("utf-8"))
                for c in hdata.get("cases", []):
                    tid = c.get("test_id")
                    if tid is None:
                        continue
                    existing_human[tid] = {
                        "human_before": c.get("human_before"),
                        "human_after": c.get("human_after"),
                    }
                    note = c.get("human_note")
                    if note:
                        existing_notes[tid] = note
            except Exception as exc:
                logger.warning("Failed to parse existing visual human-eval: %s", exc)

        cases_out: list[dict[str, Any]] = []
        for d in nav_report.get("details", []):
            tid = d.get("test_id")
            if not tid:
                continue
            ttype = d.get("type") or "unknown"
            raw = case_index.get(tid) or {}
            from_page = raw.get("from_page") or {}
            to_page = raw.get("to_page") or {}

            from_ref_name = from_page.get("reference_screenshot")
            to_ref_name = to_page.get("reference_screenshot")
            from_ref = (
                str((input_dir / from_ref_name).resolve())
                if (input_dir and from_ref_name) else None
            )
            to_ref = (
                str((input_dir / to_ref_name).resolve())
                if (input_dir and to_ref_name) else None
            )

            jd = judge_details.get(tid) or {}
            model_before = self._normalise_judge_scores(jd.get("visual_detail_before"))
            model_after = self._normalise_judge_scores(jd.get("visual_detail_after"))

            human_state = existing_human.get(tid) or {}
            human_before = self._normalise_judge_scores(human_state.get("human_before"))
            human_after = self._normalise_judge_scores(human_state.get("human_after"))

            # page_exists has only one pair (before == after); we still write
            # human_after = None and let the summary collapse them.
            cases_out.append({
                "test_id": tid,
                "type": ttype,
                "from_page": {
                    "page_name": from_page.get("page_name"),
                    "route_hint": from_page.get("route_hint"),
                    "reference_screenshot": from_ref_name,
                },
                "to_page": {
                    "page_name": to_page.get("page_name"),
                    "route_hint": to_page.get("route_hint"),
                    "reference_screenshot": to_ref_name,
                },
                "design_before_path": from_ref,
                "design_after_path": to_ref if ttype != "page_exists" else None,
                "screenshot_before": d.get("screenshot_before"),
                "screenshot_after": d.get("screenshot_after") if ttype != "page_exists" else None,
                "model_before": model_before,
                "model_after": model_after if ttype != "page_exists" else None,
                "human_before": human_before,
                "human_after": human_after if ttype != "page_exists" else None,
                "human_note": existing_notes.get(tid, ""),
                "vlm_passed": bool(d.get("passed", False)),
                "expected_route": d.get("expected_route"),
            })

        return {
            "run_id": run_id,
            "vlm_click_model": click_tag,
            "visual_judge_model": judge_tag,
            "judge_summary": {
                "model": judge_entry.get("model"),
                "avg_before": judge_entry.get("avg_before"),
                "avg_after": judge_entry.get("avg_after"),
                "scored_pairs_before": judge_entry.get("scored_pairs_before"),
                "scored_pairs_after": judge_entry.get("scored_pairs_after"),
                "evaluated_at": judge_entry.get("evaluated_at"),
                "available": bool(judge_entry),
            },
            "cases": cases_out,
            "human_eval_file": str(h_path),
            "human_eval_exists": h_path.is_file(),
        }

    def save_visual_cases(
        self,
        run_id: str,
        judge_tag: str,
        click_tag: str,
        cases: list[dict[str, Any]],
    ) -> dict[str, Path]:
        """Persist visual-judge human ratings + a summary report file."""
        cases_path = self._visual_human_path(run_id, judge_tag)
        cases_path.parent.mkdir(parents=True, exist_ok=True)

        # Re-normalise on save so we don't trust whatever shape the client sent.
        normalised: list[dict[str, Any]] = []
        for c in cases:
            tid = c.get("test_id")
            if not tid:
                continue
            ttype = c.get("type") or "unknown"
            hb = self._normalise_judge_scores(c.get("human_before"))
            ha = self._normalise_judge_scores(c.get("human_after"))
            if ttype == "page_exists":
                ha = None  # collapse: only one image per page_exists case
            normalised.append({
                "test_id": tid,
                "type": ttype,
                "from_page": c.get("from_page") or {},
                "to_page": c.get("to_page") or {},
                "human_before": hb,
                "human_after": ha,
                "human_note": c.get("human_note") or "",
            })

        payload = {
            "run_id": run_id,
            "evaluated_by": "human",
            "visual_judge_model": judge_tag,
            "vlm_click_model": click_tag,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "summary": self._build_visual_summary(
                run_id, judge_tag, click_tag, normalised,
            ),
            "cases": normalised,
        }
        cases_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        report_path = self._visual_human_report_path(run_id, judge_tag)
        report_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "evaluated_by": "human",
                    "visual_judge_model": judge_tag,
                    "vlm_click_model": click_tag,
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    **payload["summary"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"cases": cases_path, "report": report_path}

    def _build_visual_summary(
        self,
        run_id: str,
        judge_tag: str,
        click_tag: str,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate human visual ratings + agreement vs the auto judge."""
        # Gather auto-judge details for comparison (may be missing).
        visual_report = self._load_visual_report(run_id, click_tag) or {}
        judge_entry = (
            (visual_report.get("visual_scores_by_judge") or {}).get(judge_tag) or {}
        )
        judge_details = judge_entry.get("details") or {}

        total_cases = len(cases)

        # Per-pair stats. A "pair" is one of (before, after) for non-page_exists
        # cases, or just (before) for page_exists.
        total_pairs = 0
        judged_pairs = 0
        per_dim_human_sum: dict[str, int] = {d: 0 for d in VISUAL_DIMENSIONS}
        per_dim_human_count: dict[str, int] = {d: 0 for d in VISUAL_DIMENSIONS}
        weighted_before: list[float] = []
        weighted_after: list[float] = []

        # Agreement counters
        dim_compared = 0
        dim_exact = 0
        dim_within_one = 0
        dim_abs_err_sum = 0
        weighted_diff_sum = 0.0
        weighted_diff_count = 0

        for c in cases:
            tid = c["test_id"]
            ttype = c["type"]
            jd = judge_details.get(tid) or {}

            for slot, model_key in (
                ("human_before", "visual_detail_before"),
                ("human_after", "visual_detail_after"),
            ):
                if ttype == "page_exists" and slot == "human_after":
                    continue
                total_pairs += 1
                hs = c.get(slot)
                if not isinstance(hs, dict):
                    continue
                # Count any pair with at least one dim filled as "judged".
                dim_present = [
                    d for d in VISUAL_DIMENSIONS
                    if isinstance(hs.get(d), (int, float)) and 1 <= hs[d] <= 5
                ]
                if not dim_present:
                    continue
                judged_pairs += 1
                for d in dim_present:
                    per_dim_human_sum[d] += int(hs[d])
                    per_dim_human_count[d] += 1
                hw = _weighted_visual_score(hs)
                if hw is not None:
                    if slot == "human_before":
                        weighted_before.append(hw)
                    else:
                        weighted_after.append(hw)

                # Agreement vs model — only when model has a score for this pair.
                ms = jd.get(model_key) or {}
                model_w = _weighted_visual_score(ms)
                if model_w is not None and hw is not None:
                    weighted_diff_sum += abs(hw - model_w)
                    weighted_diff_count += 1
                for d in dim_present:
                    mv = ms.get(d)
                    if isinstance(mv, (int, float)) and 1 <= mv <= 5:
                        dim_compared += 1
                        diff = abs(int(hs[d]) - int(mv))
                        dim_abs_err_sum += diff
                        if diff == 0:
                            dim_exact += 1
                        if diff <= 1:
                            dim_within_one += 1

        avg_per_dim = {
            d: (round(per_dim_human_sum[d] / per_dim_human_count[d], 2)
                if per_dim_human_count[d] else None)
            for d in VISUAL_DIMENSIONS
        }
        all_weighted = weighted_before + weighted_after
        avg_weighted = (
            round(sum(all_weighted) / len(all_weighted), 2) if all_weighted else None
        )
        avg_weighted_before = (
            round(sum(weighted_before) / len(weighted_before), 2)
            if weighted_before else None
        )
        avg_weighted_after = (
            round(sum(weighted_after) / len(weighted_after), 2)
            if weighted_after else None
        )

        return {
            "total_cases": total_cases,
            "total_pairs": total_pairs,
            "judged_pairs": judged_pairs,
            "judging_progress": (
                round(judged_pairs / total_pairs, 4) if total_pairs else None
            ),
            "avg_per_dim_human": avg_per_dim,
            "avg_weighted_human": avg_weighted,
            "avg_weighted_before_human": avg_weighted_before,
            "avg_weighted_after_human": avg_weighted_after,
            "vs_visual_judge_model": {
                "compared_against": "visual_judge_model",
                "model": judge_tag,
                "model_avg_before": judge_entry.get("avg_before"),
                "model_avg_after": judge_entry.get("avg_after"),
                "compared_dims": dim_compared,
                "exact_match_dims": dim_exact,
                "within_1_dims": dim_within_one,
                "exact_match_rate": (
                    round(dim_exact / dim_compared, 4) if dim_compared else None
                ),
                "within_1_rate": (
                    round(dim_within_one / dim_compared, 4) if dim_compared else None
                ),
                "mae_per_dim": (
                    round(dim_abs_err_sum / dim_compared, 3)
                    if dim_compared else None
                ),
                "mae_weighted": (
                    round(weighted_diff_sum / weighted_diff_count, 3)
                    if weighted_diff_count else None
                ),
            },
        }


# ---------------------------------------------------------------------------
# Dashboard HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """Serves the SPA UI plus a small JSON API."""

    state: DashboardState  # injected on the class before binding the server

    # ---- helpers ----------------------------------------------------------

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _serve_html(self, name: str = "index.html") -> None:
        path = Path(__file__).parent / "static" / name
        if not path.is_file():
            self.send_error(500, f"{name} missing")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_image(self, raw_path: str) -> None:
        if not raw_path:
            self.send_error(400, "missing path")
            return
        try:
            target = Path(raw_path).resolve()
        except Exception as exc:
            self.send_error(400, f"bad path: {exc}")
            return
        # Sandbox: only files under one of the registered roots may be served.
        # Roots include the experiment dir (always) and any input/<dataset>/<app>
        # directories we resolved while loading the visual-judge data so the
        # design reference screenshots can be displayed too.
        target_str = str(target)
        if not any(
            target_str == root or target_str.startswith(root + os.sep)
            for root in self.state._image_roots
        ):
            self.send_error(403, "path outside allowed image roots")
            return
        if not target.is_file():
            self.send_error(404)
            return
        suffix = target.suffix.lower()
        ctype = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    # ---- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                self._serve_html("index.html")
            elif parsed.path == "/api/experiment":
                self._json(200, {
                    "name": self.state.experiment_dir.name,
                    "path": str(self.state.experiment_dir),
                })
            elif parsed.path == "/api/runs":
                self._json(200, {"runs": self.state.list_runs()})
            elif parsed.path == "/api/cases":
                run_id = params.get("run_id", [""])[0]
                vlm = params.get("vlm", [""])[0]
                if not run_id or not vlm:
                    self._json(400, {"error": "missing run_id or vlm"})
                    return
                data = self.state.load_cases(run_id, vlm)
                if data is None:
                    self._json(404, {"error": "report not found"})
                    return
                self._json(200, data)
            elif parsed.path == "/api/visual_cases":
                run_id = params.get("run_id", [""])[0]
                judge = params.get("judge", [""])[0]
                click = params.get("click", [""])[0]
                if not run_id or not judge or not click:
                    self._json(
                        400,
                        {"error": "missing run_id / judge / click"},
                    )
                    return
                data = self.state.load_visual_cases(run_id, judge, click)
                if data is None:
                    self._json(404, {"error": "navigation_test report not found"})
                    return
                self._json(200, data)
            elif parsed.path == "/api/status":
                self._json(200, {"previews": self.state.previews_status()})
            elif parsed.path == "/screenshot":
                self._serve_image(params.get("path", [""])[0])
            else:
                self.send_error(404)
        except Exception as exc:  # pragma: no cover — never want the server to die
            logger.exception("GET %s failed", parsed.path)
            try:
                self._json(500, {"error": str(exc)})
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/launch":
                run_id = params.get("run_id", [""])[0]
                if not run_id:
                    self._json(400, {"error": "missing run_id"})
                    return
                try:
                    port = self.state.launch_preview(run_id)
                    self._json(200, {"run_id": run_id, "port": port})
                except FileNotFoundError as exc:
                    self._json(404, {"error": str(exc)})
                except Exception as exc:
                    logger.exception("launch failed")
                    self._json(500, {"error": str(exc)})
            elif parsed.path == "/api/stop":
                run_id = params.get("run_id", [""])[0]
                if run_id:
                    self.state.stop_preview(run_id)
                else:
                    self.state.stop_all()
                self._json(200, {"ok": True})
            elif parsed.path == "/api/save":
                run_id = params.get("run_id", [""])[0]
                vlm = params.get("vlm", [""])[0]
                if not run_id or not vlm:
                    self._json(400, {"error": "missing run_id or vlm"})
                    return
                body = self._read_body().decode("utf-8") or "{}"
                payload = json.loads(body)
                cases = payload.get("cases", [])
                if not isinstance(cases, list):
                    self._json(400, {"error": "cases must be a list"})
                    return
                paths = self.state.save_cases(run_id, vlm, cases)
                summary = self.state._build_human_report(run_id, vlm, cases)
                self._json(200, {
                    "ok": True,
                    "cases_path": str(paths["cases"]),
                    "report_path": str(paths["report"]),
                    "summary": {
                        "total_cases": summary["total_cases"],
                        "passed": summary["passed"],
                        "failed": summary["failed"],
                        "pending": summary["pending"],
                        "pass_rate": summary["pass_rate"],
                        "agreement_rate": summary["vs_vlm_click_model"]["agreement_rate"],
                    },
                })
            elif parsed.path == "/api/visual_save":
                run_id = params.get("run_id", [""])[0]
                judge = params.get("judge", [""])[0]
                click = params.get("click", [""])[0]
                if not run_id or not judge or not click:
                    self._json(
                        400,
                        {"error": "missing run_id / judge / click"},
                    )
                    return
                body = self._read_body().decode("utf-8") or "{}"
                payload = json.loads(body)
                cases = payload.get("cases", [])
                if not isinstance(cases, list):
                    self._json(400, {"error": "cases must be a list"})
                    return
                paths = self.state.save_visual_cases(run_id, judge, click, cases)
                summary = self.state._build_visual_summary(
                    run_id, judge, click, cases,
                )
                self._json(200, {
                    "ok": True,
                    "cases_path": str(paths["cases"]),
                    "report_path": str(paths["report"]),
                    "summary": summary,
                })
            else:
                self.send_error(404)
        except Exception as exc:  # pragma: no cover
            logger.exception("POST %s failed", parsed.path)
            try:
                self._json(500, {"error": str(exc)})
            except Exception:
                pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _find_port_holder(port: int) -> str | None:
    """Best-effort lookup of "pid/cmd" holding ``port`` — purely for error msgs."""
    import shutil
    import subprocess

    if shutil.which("ss"):
        try:
            out = subprocess.check_output(
                ["ss", "-lntp"], text=True, stderr=subprocess.DEVNULL, timeout=2,
            )
            for line in out.splitlines():
                if f":{port} " in line:
                    return line.strip()
        except Exception:
            pass
    return None


def start_server(
    experiment_dir: str | Path,
    *,
    host: str = "0.0.0.0",
    port: int = 8090,
    preview_host: str = "0.0.0.0",
    preview_port: int | None = None,
) -> None:
    experiment_dir = Path(experiment_dir).resolve()
    if not (experiment_dir / "runs").is_dir():
        raise FileNotFoundError(
            f"No runs/ directory under {experiment_dir} — is this an experiment dir?"
        )

    # Default preview port = dashboard port + 1, so users only need to
    # forward two consecutive, predictable ports for remote/SSH-tunnel access.
    if preview_port is None:
        preview_port = port + 1

    state = DashboardState(
        experiment_dir,
        preview_host=preview_host,
        preview_port=preview_port,
    )
    DashboardHandler.state = state

    try:
        server = http.server.ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if getattr(exc, "errno", None) == 98:  # EADDRINUSE
            holder = _find_port_holder(port)
            hint = f"\n  → 占用进程: {holder}" if holder else ""
            raise RuntimeError(
                f"端口 {port} 已被占用，无法启动 dashboard。{hint}\n"
                f"  解决办法之一:\n"
                f"    1) 直接换端口:  python -m human_eval ... --port {port + 1}\n"
                f"    2) 终止占用进程后重试"
            ) from exc
        raise

    display_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    print(f"\n  人工-VLM 一致性评估平台")
    print(f"  Dashboard:    http://{display_host}:{port}")
    print(f"  App preview:  http://{display_host}:{preview_port}    "
          f"(iframe 实际访问 URL，使用浏览器侧 host)")
    print(f"  Experiment:   {experiment_dir}")
    if preview_port == 0:
        print(f"  注意: --preview-port=0 会用临时端口，远程访问场景请改成固定端口。")
    else:
        print(f"\n  ★ 远程访问提示: 请确保两个端口都被转发/映射到本机:")
        print(f"      - {port}   (Dashboard 网页)")
        print(f"      - {preview_port}   (App 预览，iframe 内嵌)")
    print(f"\n  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down ...")
    finally:
        state.stop_all()
        server.server_close()
