"""
Web dashboard for monitoring experiment progress.

Provides a lightweight HTTP server that serves a single-page dashboard
and a JSON API endpoint.  Completely read-only — never writes to any
experiment files.

Usage:
    python -m evaluator monitor <experiment_dir> [--port 8080] [--host 0.0.0.0]
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data collection helpers (all read-only)
# ---------------------------------------------------------------------------

_ITER_RE = re.compile(r"\[agentic\] iteration (\d+)/(\d+)")
_TOKEN_RE = re.compile(r"\[agentic\] iter (\d+) \| [\d.]+s \| in=(\d+) out=(\d+)")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tail_bytes(path: Path, nbytes: int = 8192) -> str:
    """Read the last *nbytes* of a file without loading it all."""
    try:
        size = path.stat().st_size
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if size > nbytes:
                f.seek(size - nbytes)
                f.readline()  # skip partial line
            return f.read()
    except Exception:
        return ""


def _parse_iteration(run_dir: Path) -> dict[str, Any]:
    """Extract latest iteration progress and last-iter input tokens from log tail.

    The log pattern is:
      [agentic] iteration 7/70          <- printed when iter starts
      ... (LLM call, may take minutes)
      [agentic] iter 7 | 32.1s | in=12345 out=678 | stop=tool_calls
      ... (tool execution)
      [agentic] iteration 8/70          <- next iter starts

    When LLM is in-flight, the tail only has "iteration N/M" but no
    "iter N | ..." yet, so we read a larger tail to capture the previous
    iter's token line.

    Returns dict with keys: iteration (str|None), iter_input_tokens (int|None).
    """
    log = run_dir / ".designforge" / "logs" / "pipeline.log"
    result: dict[str, Any] = {"iteration": None, "iter_input_tokens": None}
    if not log.is_file():
        return result
    tail = _tail_bytes(log, 32768)

    iter_matches = _ITER_RE.findall(tail)
    if iter_matches:
        cur, total = iter_matches[-1]
        result["iteration"] = f"{cur}/{total}"

    token_matches = _TOKEN_RE.findall(tail)
    if token_matches:
        _iter_num, last_in, _last_out = token_matches[-1]
        result["iter_input_tokens"] = int(last_in)

    return result


def _parse_live_tokens(run_dir: Path) -> tuple[int, int]:
    """Accumulate token counts from pipeline.log for a running task.

    Reads the full file because token lines are spread throughout.
    For very large logs this is still fast (regex scan, no JSON parsing).
    """
    log = run_dir / ".designforge" / "logs" / "pipeline.log"
    if not log.is_file():
        return 0, 0
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            total_in = total_out = 0
            for line in f:
                m = _TOKEN_RE.search(line)
                if m:
                    total_in += int(m.group(2))
                    total_out += int(m.group(3))
            return total_in, total_out
    except Exception:
        return 0, 0


def _read_metrics(run_dir: Path) -> dict[str, Any] | None:
    """Read metrics.json for a completed run."""
    return _read_json(run_dir / ".designforge" / "metrics.json")


def _read_trace_tokens(run_dir: Path) -> tuple[int, int]:
    """Fallback: sum tokens from trace.json files."""
    traces_dir = run_dir / ".designforge" / "traces"
    if not traces_dir.is_dir():
        return 0, 0
    total_in = total_out = 0
    for p in traces_dir.rglob("*.trace.json"):
        data = _read_json(p)
        if data and "tokens" in data:
            total_in += data["tokens"].get("input", 0)
            total_out += data["tokens"].get("output", 0)
    return total_in, total_out


def _elapsed_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return (datetime.now() - dt).total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main data collection
# ---------------------------------------------------------------------------

# Cache completed run metrics to avoid re-reading on every poll
_completed_cache: dict[str, dict[str, Any]] = {}


def _invert_ts(iso_str: str | None) -> str:
    """Return a string that sorts in reverse chronological order.

    ISO timestamps sort lexicographically oldest-first; we want newest-first
    within each status group.  Empty/None sorts last.
    """
    if not iso_str:
        return "\xff"
    return "".join(chr(ord("9") - ord(c) + ord("0")) if c.isdigit() else c for c in iso_str)


def collect_status(experiment_dir: Path) -> dict[str, Any]:
    """Build the full status snapshot consumed by the frontend."""
    status_path = experiment_dir / "status.json"
    status = _read_json(status_path) or {}
    runs_dir = experiment_dir / "runs"

    started_at = status.get("started_at")
    experiment_elapsed = _elapsed_since(started_at)

    runs_raw: dict[str, dict] = status.get("runs", {})
    run_list: list[dict[str, Any]] = []

    for run_id, info in runs_raw.items():
        st = info.get("status", "pending")
        parts = run_id.split("__")
        app = parts[0] if parts else run_id
        model = parts[1] if len(parts) > 1 else ""

        row: dict[str, Any] = {
            "run_id": run_id,
            "app": app,
            "model": model,
            "status": st,
            "iteration": None,
            "iter_input_tokens": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "elapsed_sec": None,
            "cost_usd": None,
            "finished_at": info.get("finished_at"),
        }

        run_path = runs_dir / run_id

        if st == "generating":
            iter_info = _parse_iteration(run_path)
            row["iteration"] = iter_info["iteration"]
            row["iter_input_tokens"] = iter_info["iter_input_tokens"]
            t_in, t_out = _parse_live_tokens(run_path)
            if t_in == 0 and t_out == 0:
                t_in, t_out = _read_trace_tokens(run_path)
            row["tokens_in"] = t_in
            row["tokens_out"] = t_out
            row["elapsed_sec"] = _elapsed_since(info.get("started_at"))

        elif st in ("generated", "evaluated", "gen_failed", "eval_failed"):
            if run_id in _completed_cache:
                cached = _completed_cache[run_id]
                row.update(cached)
                row["status"] = st
            else:
                gen_time = info.get("generation_time_sec")
                row["elapsed_sec"] = gen_time

                metrics = _read_metrics(run_path) if run_path.is_dir() else None
                if metrics and "totals" in metrics:
                    totals = metrics["totals"]
                    row["tokens_in"] = totals.get("total_input_tokens", 0)
                    row["tokens_out"] = totals.get("total_output_tokens", 0)
                    row["cost_usd"] = totals.get("estimated_cost_usd")
                else:
                    t_in, t_out = _read_trace_tokens(run_path) if run_path.is_dir() else (0, 0)
                    row["tokens_in"] = t_in
                    row["tokens_out"] = t_out

                if st in ("generated", "evaluated"):
                    _completed_cache[run_id] = {
                        "tokens_in": row["tokens_in"],
                        "tokens_out": row["tokens_out"],
                        "elapsed_sec": row["elapsed_sec"],
                        "cost_usd": row["cost_usd"],
                    }

        run_list.append(row)

    status_order = {
        "generating": 0,
        "evaluating": 1,
        "pending": 2,
        "gen_failed": 3,
        "eval_failed": 4,
        "generated": 5,
        "evaluated": 6,
    }
    run_list.sort(key=lambda r: (
        status_order.get(r["status"], 99),
        _invert_ts(r.get("finished_at") or r.get("started_at")),
    ))

    return {
        "experiment_id": status.get("experiment_id", experiment_dir.name),
        "started_at": started_at,
        "elapsed_sec": round(experiment_elapsed, 1) if experiment_elapsed else None,
        "total_runs": status.get("total_runs", len(runs_raw)),
        "counts": status.get("counts", {}),
        "runs": run_list,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard HTML and the /api/status JSON endpoint."""

    experiment_dir: Path  # set on class before creating server

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self._serve_api()
        elif self.path in ("/", "/index.html", "/dashboard.html"):
            self._serve_dashboard()
        else:
            self.send_error(404)

    def _serve_api(self) -> None:
        data = collect_status(self.experiment_dir)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self) -> None:
        html_path = Path(__file__).parent / "static" / "dashboard.html"
        if not html_path.is_file():
            self.send_error(500, "dashboard.html not found")
            return
        body = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # silence per-request logs


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def start_dashboard(
    experiment_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    experiment_dir = Path(experiment_dir).resolve()
    if not (experiment_dir / "status.json").is_file():
        raise FileNotFoundError(
            f"No status.json in {experiment_dir} — is this an experiment directory?"
        )

    DashboardHandler.experiment_dir = experiment_dir

    server = HTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}" if host != "0.0.0.0" else f"http://localhost:{port}"
    print(f"\n  Dashboard: {url}")
    print(f"  Experiment: {experiment_dir.name}")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
