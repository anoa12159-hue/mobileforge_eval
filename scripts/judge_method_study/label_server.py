"""Minimal browser-based labeling UI for Phase 2 human ranking.

Design constraints (must not leak model identity to the human labeler):

* Candidates are shown with neutral labels ``A``, ``B``, ``C`` … (never the
  model tag). The display_label -> model_tag mapping lives server-side.
* No model-identifying metadata (borda scores, missing-model names,
  shuffle stability, anchor pass rate) is sent to the browser.
* Labeler can mark candidates as "unrankable" (completely off-reference);
  those are treated as tied at the bottom of the ranking during analysis.

Usage::

    python -m scripts.judge_method_study.label_server \\
        --tasks experiments/judge_method_study/phase2/tasks.json \\
        --labels-out experiments/judge_method_study/phase2/human_labels.json \\
        --port 8094

Open http://localhost:8094 in your browser.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import mimetypes
import random
import socket
import socketserver
import string
import sys
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("label_server")


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


class Store:
    """Task + labels persistence. Thread-safe (uses a single lock)."""

    def __init__(self, tasks_file: Path, labels_file: Path, allowed_roots: list[Path]):
        self.tasks_file = tasks_file
        self.labels_file = labels_file
        self.allowed_roots = [p.resolve() for p in allowed_roots]
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        data = json.loads(self.tasks_file.read_text())
        self.meta = {k: v for k, v in data.items() if k != "tasks"}
        self.tasks: list[dict[str, Any]] = data["tasks"]

        if self.labels_file.is_file():
            self.labels = json.loads(self.labels_file.read_text())
        else:
            self.labels = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "tasks_file": str(self.tasks_file),
                "labels": {},
                "presentations": {},
            }
        self.labels.setdefault("labels", {})
        self.labels.setdefault("presentations", {})

    def _save(self) -> None:
        self.labels_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.labels_file.with_suffix(self.labels_file.suffix + ".tmp")
        tmp.write_text(json.dumps(self.labels, indent=2, ensure_ascii=False))
        tmp.replace(self.labels_file)

    def presentation(self, task: dict[str, Any]) -> dict[str, str]:
        """Return ``{display_label: model_tag}`` for this task.

        The mapping is stable across reloads of the same task, so the labeler
        doesn't get a free "re-shuffle" just by refreshing. Different tasks
        use independent mappings — the letter ``A`` in task_001 is NOT the
        same model as ``A`` in task_002.
        """
        with self._lock:
            key = task["task_id"]
            pres = self.labels["presentations"].get(key)
            if pres and set(pres.values()) == set(task["present_candidates"]):
                return pres
            # Build a new mapping
            n = len(task["present_candidates"])
            letters = list(string.ascii_uppercase[:n])
            shuffled = list(task["present_candidates"])
            random.Random(hash(key) & 0xFFFFFFFF).shuffle(shuffled)
            mapping = dict(zip(letters, shuffled))
            self.labels["presentations"][key] = mapping
            self._save()
            return mapping

    def record_label(
        self,
        task_id: str,
        ranking_tags: list[str],
        unrankable_tags: list[str],
        note: str,
        presentation_snapshot: dict[str, str],
    ) -> None:
        with self._lock:
            self.labels["labels"][task_id] = {
                "ranking": ranking_tags,
                "unrankable": unrankable_tags,
                "note": note,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "presentation_snapshot": presentation_snapshot,
            }
            self._save()

    def task_summary(self) -> list[dict[str, Any]]:
        labeled = self.labels["labels"]
        return [
            {
                "task_id": t["task_id"],
                "labeled": t["task_id"] in labeled,
            }
            for t in self.tasks
        ]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        for t in self.tasks:
            if t["task_id"] == task_id:
                return t
        return None

    def next_unlabeled(self) -> dict[str, Any] | None:
        done = set(self.labels["labels"].keys())
        for t in self.tasks:
            if t["task_id"] not in done:
                return t
        return None

    def is_allowed_path(self, p: Path) -> bool:
        try:
            resolved = p.resolve()
        except Exception:
            return False
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    # ------------------------------------------------------------------
    # Anonymized task view for the client
    # ------------------------------------------------------------------

    def anonymize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Return a view of the task with all model-identifying info stripped.

        Image URLs use an opaque per-task token (task_id + label) rather than
        the absolute file path, so the browser's network panel never leaks
        model tags embedded in run-directory names.
        """
        mapping = self.presentation(task)
        cand_paths = task.get("candidate_paths", {})
        candidates = []
        for label in sorted(mapping.keys()):
            model_tag = mapping[label]
            p = cand_paths.get(model_tag)
            if not p:
                continue
            candidates.append({
                "label": label,
                "url": f"/image?t={task['task_id']}&l={label}",
            })
        return {
            "task_id": task["task_id"],
            "reference_url": f"/image?t={task['task_id']}&l=__ref__",
            "candidates": candidates,
            "n_candidates": len(candidates),
            "n_missing": len(task.get("missing_models") or []),
            "test_type": task.get("type"),
        }

    def resolve_image(self, task_id: str, label: str) -> Path | None:
        """Resolve an anonymized (task_id, label) to an absolute image path."""
        task = self.get_task(task_id)
        if task is None:
            return None
        if label == "__ref__":
            return Path(task["reference_path"])
        mapping = self.presentation(task)
        model_tag = mapping.get(label)
        if model_tag is None:
            return None
        p = task.get("candidate_paths", {}).get(model_tag)
        return Path(p) if p else None


# ------------------------------------------------------------------
# HTTP Handler
# ------------------------------------------------------------------


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Phase 2 · Human Ranking</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; background: #f4f5f7; color: #222; }
  header { background: #1f2937; color: white; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .meta { font-size: 12px; opacity: 0.75; display: flex; align-items: center; gap: 10px; }
  .progress { background: #374151; border-radius: 4px; height: 6px; width: 260px; overflow: hidden; }
  .progress-bar { background: #10b981; height: 100%; transition: width .2s; }
  main { display: grid; grid-template-columns: 360px 1fr; gap: 16px; padding: 16px; height: calc(100vh - 50px); }
  .ref-pane { background: white; border-radius: 8px; padding: 12px; overflow-y: auto; position: sticky; top: 0; }
  .ref-pane h3 { margin: 0 0 8px; font-size: 13px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
  .ref-pane img { width: 100%; border-radius: 4px; border: 1px solid #e5e7eb; }
  .ref-pane .task-info { font-size: 12px; color: #6b7280; margin-top: 10px; line-height: 1.6; }
  .ref-pane .tag { display: inline-block; background: #dbeafe; color: #1e40af; font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-right: 4px; }
  .candidates { background: white; border-radius: 8px; padding: 12px; overflow-y: auto; }
  .cand-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
  .cand-card { border: 2px solid #e5e7eb; border-radius: 6px; padding: 8px; transition: all .15s; background: #fafafa; position: relative; }
  .cand-card:hover { border-color: #3b82f6; }
  .cand-card.ranked { border-color: #10b981; background: #ecfdf5; }
  .cand-card.unrankable { border-color: #dc2626; background: #fef2f2; opacity: 0.6; }
  .cand-card.unrankable img { filter: grayscale(0.7); }
  .cand-card img { width: 100%; border-radius: 3px; display: block; cursor: pointer; }
  .cand-card .rank-badge { position: absolute; top: 6px; right: 6px; width: 34px; height: 34px; border-radius: 50%; background: #10b981; color: white; font-size: 18px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
  .cand-card .rank-badge.hidden { display: none; }
  .cand-card .cross-badge { position: absolute; top: 6px; right: 6px; width: 34px; height: 34px; border-radius: 50%; background: #dc2626; color: white; font-size: 22px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
  .cand-card .cross-badge.hidden { display: none; }
  .cand-card .label { font-family: monospace; font-size: 14px; color: #111827; margin-top: 6px; text-align: center; font-weight: 700; letter-spacing: 0.5px; }
  .cand-card .btn-na { width: 100%; margin-top: 4px; background: white; border: 1px solid #fca5a5; color: #b91c1c; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }
  .cand-card .btn-na:hover { background: #fee2e2; }
  .cand-card.unrankable .btn-na { background: #dc2626; color: white; border-color: #dc2626; }
  .action-bar { padding: 12px; background: white; border-radius: 8px; margin-top: 12px; display: flex; justify-content: space-between; align-items: center; }
  .action-bar .instr { font-size: 13px; color: #6b7280; flex: 1; }
  .action-bar button { padding: 8px 18px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; margin-left: 8px; }
  .btn-submit { background: #10b981; color: white; }
  .btn-submit:disabled { background: #d1d5db; cursor: not-allowed; }
  .btn-skip { background: #fee2e2; color: #991b1b; }
  .btn-reset { background: #f3f4f6; color: #374151; }
  .all-done { padding: 40px; text-align: center; font-size: 18px; color: #6b7280; background: white; border-radius: 8px; margin: 40px; }
  .hint { background: #fef3c7; border: 1px solid #fcd34d; color: #92400e; padding: 8px 12px; border-radius: 6px; font-size: 12px; margin-bottom: 10px; line-height: 1.6; }
  textarea { width: 100%; margin-top: 8px; padding: 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; resize: vertical; font-family: inherit; }
</style>
</head>
<body>
<header>
  <h1>Phase 2 · Human Ranking (blind)</h1>
  <div class="meta">
    <span id="progress-text">loading…</span>
    <div class="progress"><div class="progress-bar" id="progress-bar"></div></div>
  </div>
</header>
<main id="main">
  <div class="ref-pane" id="ref-pane">loading…</div>
  <div>
    <div class="candidates" id="cand-pane"></div>
    <div class="action-bar" id="action-bar" style="display:none">
      <span class="instr"></span>
      <div>
        <button class="btn-reset" onclick="resetAll()">全部重置</button>
        <button class="btn-skip" onclick="skipTask()">Skip</button>
        <button class="btn-submit" id="btn-submit" disabled onclick="submitRanking()">提交 → 下一个</button>
      </div>
    </div>
    <div id="note-pane" style="display:none; background:white; border-radius:8px; padding:12px; margin-top:12px;">
      <label style="font-size:12px; color:#6b7280;">备注（可选）</label>
      <textarea id="note" rows="2" placeholder="比如：某候选白屏 / 布局完全颠倒 / 等"></textarea>
    </div>
  </div>
</main>

<script>
let state = {
  task: null,
  candidates: [],
  ranks: {},        // {display_label: rank (1-based)}
  unrankable: {},   // {display_label: true}
  total: 0,
  done: 0,
};

function updateProgress() {
  const pct = state.total ? (state.done / state.total * 100) : 0;
  document.getElementById('progress-text').textContent = state.done + ' / ' + state.total;
  document.getElementById('progress-bar').style.width = pct + '%';
}

async function loadNext() {
  const r = await fetch('/api/next');
  const d = await r.json();
  state.total = d.total;
  state.done = d.done;
  updateProgress();

  if (!d.task) {
    document.getElementById('main').innerHTML = '<div class="all-done" style="grid-column: 1 / -1">✓ 所有任务已完成</div>';
    return;
  }

  state.task = d.task;
  state.candidates = d.task.candidates;
  state.ranks = {};
  state.unrankable = {};

  renderRef(d.task);
  renderCandidates(d.task);
  document.getElementById('action-bar').style.display = 'flex';
  document.getElementById('note-pane').style.display = 'block';
  document.getElementById('note').value = '';
  updateInstr();
  updateSubmitButton();
}

function renderRef(task) {
  const pane = document.getElementById('ref-pane');
  pane.innerHTML = `
    <h3>Reference · ${task.task_id}</h3>
    <img src="${task.reference_url}" alt="reference">
    <div class="task-info">
      <div><span class="tag">${task.test_type || 'unknown'}</span></div>
      <div>候选数：${task.n_candidates}${task.n_missing ? ' · <span style="color:#b91c1c">另有 ' + task.n_missing + ' 个候选缺图，已自动排尾</span>' : ''}</div>
    </div>
  `;
}

function renderCandidates(task) {
  const pane = document.getElementById('cand-pane');
  pane.innerHTML = `
    <div class="hint">
      <b>盲标</b>：候选只以 A/B/C... 表示，标完才会揭晓模型身份。<br>
      <b>操作</b>：按贴近 reference 的顺序依次点击图片（最像的先点）；点击已标号的图可清除。<br>
      <b>无法排序</b>：某候选完全偏离 reference 无从比较，点底部「✗ 无法排序」，它会并列最末。
    </div>
    <div class="cand-grid" id="cand-grid"></div>
  `;
  const grid = document.getElementById('cand-grid');
  for (const c of task.candidates) {
    const card = document.createElement('div');
    card.className = 'cand-card';
    card.dataset.label = c.label;
    card.innerHTML = `
      <div class="rank-badge hidden"></div>
      <div class="cross-badge hidden">✗</div>
      <img src="${c.url}" alt="${c.label}">
      <div class="label">${c.label}</div>
      <button class="btn-na" onclick="toggleUnrankable(event, '${c.label}')">✗ 无法排序</button>
    `;
    card.querySelector('img').onclick = () => toggleRank(c.label);
    grid.appendChild(card);
  }
}

function toggleRank(label) {
  if (state.unrankable[label]) return;  // can't rank an unrankable
  if (state.ranks[label]) {
    const cleared = state.ranks[label];
    delete state.ranks[label];
    for (const k of Object.keys(state.ranks)) {
      if (state.ranks[k] > cleared) state.ranks[k] -= 1;
    }
  } else {
    const nextRank = Object.keys(state.ranks).length + 1;
    state.ranks[label] = nextRank;
  }
  refreshBadges();
  updateInstr();
  updateSubmitButton();
}

function toggleUnrankable(ev, label) {
  ev.stopPropagation();
  if (state.unrankable[label]) {
    delete state.unrankable[label];
  } else {
    // if previously ranked, clear rank and reflow
    if (state.ranks[label]) {
      const cleared = state.ranks[label];
      delete state.ranks[label];
      for (const k of Object.keys(state.ranks)) {
        if (state.ranks[k] > cleared) state.ranks[k] -= 1;
      }
    }
    state.unrankable[label] = true;
  }
  refreshBadges();
  updateInstr();
  updateSubmitButton();
}

function refreshBadges() {
  document.querySelectorAll('.cand-card').forEach(card => {
    const label = card.dataset.label;
    const rank = state.ranks[label];
    const na = state.unrankable[label];
    const rbadge = card.querySelector('.rank-badge');
    const cbadge = card.querySelector('.cross-badge');
    card.classList.remove('ranked', 'unrankable');
    rbadge.classList.add('hidden');
    cbadge.classList.add('hidden');
    if (na) {
      card.classList.add('unrankable');
      cbadge.classList.remove('hidden');
    } else if (rank) {
      card.classList.add('ranked');
      rbadge.classList.remove('hidden');
      rbadge.textContent = rank;
    }
  });
}

function updateInstr() {
  const n = state.candidates.length;
  const ranked = Object.keys(state.ranks).length;
  const na = Object.keys(state.unrankable).length;
  const remain = n - ranked - na;
  const el = document.querySelector('.action-bar .instr');
  if (remain > 0) {
    el.textContent = `还剩 ${remain} 张候选待处理（${ranked} 已排名 + ${na} 标为无法排序）`;
  } else {
    el.textContent = `全部处理完毕：${ranked} 已排名 + ${na} 无法排序（并列最末）`;
  }
}

function resetAll() {
  state.ranks = {};
  state.unrankable = {};
  refreshBadges();
  updateInstr();
  updateSubmitButton();
}

function updateSubmitButton() {
  const n = state.candidates.length;
  const ranked = Object.keys(state.ranks).length;
  const na = Object.keys(state.unrankable).length;
  document.getElementById('btn-submit').disabled = (ranked + na !== n);
}

async function submitRanking() {
  const n = state.candidates.length;
  const ranked = Object.keys(state.ranks).length;
  const na = Object.keys(state.unrankable).length;
  if (ranked + na !== n) return;

  const ranking_labels = Array(ranked).fill(null);
  for (const [label, rank] of Object.entries(state.ranks)) {
    ranking_labels[rank - 1] = label;
  }
  const unrankable_labels = Object.keys(state.unrankable);
  const note = document.getElementById('note').value;

  await fetch('/api/submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      task_id: state.task.task_id,
      ranking_labels,
      unrankable_labels,
      note,
    }),
  });
  loadNext();
}

async function skipTask() {
  if (!confirm('Skip this task? 会记为跳过，分析时不计入。')) return;
  await fetch('/api/submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      task_id: state.task.task_id,
      ranking_labels: null,
      unrankable_labels: [],
      note: '[skipped]',
    }),
  });
  loadNext();
}

loadNext();
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    store: Store = None  # type: ignore  # set at runtime

    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_bytes(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/api/next":
            task = self.store.next_unlabeled()
            summary = self.store.task_summary()
            total = len(summary)
            done = sum(1 for s in summary if s["labeled"])
            if task is None:
                self._send_json(200, {"task": None, "total": total, "done": done})
                return
            anon = self.store.anonymize_task(task)
            self._send_json(200, {"task": anon, "total": total, "done": done})
            return

        if path == "/api/tasks":
            self._send_json(200, self.store.task_summary())
            return

        if path == "/image":
            qs = urllib.parse.parse_qs(parsed.query)
            task_id = qs.get("t", [""])[0]
            label = qs.get("l", [""])[0]
            try:
                self._serve_image_token(task_id, label)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/submit":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                self._send_json(400, {"error": "bad json"})
                return

            task_id = data["task_id"]
            task = self.store.get_task(task_id)
            if task is None:
                self._send_json(404, {"error": "unknown task_id"})
                return

            presentation = self.store.presentation(task)
            ranking_labels = data.get("ranking_labels")
            unrankable_labels = data.get("unrankable_labels") or []

            # Translate display labels back to model_tag
            if ranking_labels is None:
                ranking_tags = None
            else:
                ranking_tags = [presentation.get(lbl) for lbl in ranking_labels]
                if any(t is None for t in ranking_tags):
                    self._send_json(400, {"error": "unknown display label in ranking"})
                    return

            unrankable_tags = [presentation.get(lbl) for lbl in unrankable_labels]
            if any(t is None for t in unrankable_tags):
                self._send_json(400, {"error": "unknown display label in unrankable"})
                return

            self.store.record_label(
                task_id=task_id,
                ranking_tags=ranking_tags,
                unrankable_tags=unrankable_tags,
                note=data.get("note", ""),
                presentation_snapshot=dict(presentation),
            )
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def _serve_image_token(self, task_id: str, label: str) -> None:
        """Serve an image by anonymized (task_id, label) token.

        The actual file path is never exposed to the client — all URL
        lookups go through the per-task presentation mapping server-side.
        """
        path = self.store.resolve_image(task_id, label)
        if path is None:
            self._send_json(404, {"error": "unknown token"})
            return
        path = path.resolve()
        if not self.store.is_allowed_path(path):
            self._send_json(403, {"error": "path not allowed"})
            return
        if not path.is_file():
            self._send_json(404, {"error": "file missing"})
            return
        mime, _ = mimetypes.guess_type(str(path))
        self._send_bytes(200, path.read_bytes(), mime or "application/octet-stream")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-2 human labeling server (blind)")
    ap.add_argument("--tasks", required=True, type=Path,
                    help="tasks.json produced by build_phase2_tasks.py")
    ap.add_argument("--labels-out", required=True, type=Path,
                    help="Where human labels are persisted")
    ap.add_argument("--port", default=8094, type=int)
    ap.add_argument("--host", default="0.0.0.0", type=str)
    ap.add_argument("--allowed-root", nargs="*", default=None, type=Path,
                    help="Directories from which images may be served (security). Default: repo root.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    allowed = args.allowed_root or [_REPO_ROOT]
    store = Store(args.tasks, args.labels_out, allowed)

    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{socket.gethostname()}:{args.port}/"
    logger.info("Labeling server started at %s (or http://localhost:%d)", url, args.port)
    logger.info("Tasks: %d loaded from %s", len(store.tasks), args.tasks)
    logger.info("Labels will be saved to %s", args.labels_out)
    logger.info("Allowed image roots: %s", [str(p) for p in allowed])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
