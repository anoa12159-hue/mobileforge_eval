"""
Generate a self-contained HTML trace viewer from structured trace files.

The viewer reads the run_manifest.json and all trace JSON files to render:
- A timeline/Gantt bar chart of stages
- Expandable agent cards with iteration details
- Tool call inspection with arguments and results
- Token usage and cost summary
- Search/filter capabilities

All data is embedded inline — no external dependencies or server needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_viewer(trace_dir: Path) -> Path:
    """Read all trace files under *trace_dir* and emit viewer.html alongside them."""
    manifest_path = trace_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"run_manifest.json not found in {trace_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    traces: list[dict] = []
    for stage_info in manifest.get("stages", []):
        for rel_path in stage_info.get("traces", []):
            trace_file = trace_dir / rel_path
            if trace_file.exists():
                trace_data = json.loads(trace_file.read_text(encoding="utf-8"))
                trace_data["_rel_path"] = rel_path
                traces.append(trace_data)

    embedded_data = json.dumps(
        {"manifest": manifest, "traces": traces},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    html = _build_html(embedded_data)
    out_path = trace_dir / "viewer.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("Trace viewer written to %s", out_path)
    return out_path


def _build_html(embedded_json: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DesignForge Trace Viewer</title>
<style>
:root {{
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface-hover: #222633;
  --border: #2a2e3d;
  --text: #e4e6ed;
  --text-dim: #8b8fa3;
  --accent: #6c8cff;
  --accent-dim: #4a62b3;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
  --orange: #fb923c;
  --purple: #a78bfa;
  --cyan: #22d3ee;
  --mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  --sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: var(--sans);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}}
h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
.subtitle {{ color: var(--text-dim); font-size: 0.85rem; margin-bottom: 24px; }}

/* Summary cards */
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}}
.summary-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}}
.summary-card .value {{
  font-size: 1.6rem;
  font-weight: 700;
  font-family: var(--mono);
  color: var(--accent);
}}
.summary-card .label {{
  font-size: 0.75rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 4px;
}}

/* Timeline */
.timeline-section {{ margin-bottom: 28px; }}
.timeline-section h2 {{ font-size: 1.1rem; margin-bottom: 12px; }}
.timeline-bar-wrap {{
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: 0.82rem;
}}
.timeline-label {{
  width: 100px;
  text-align: right;
  padding-right: 12px;
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.78rem;
  flex-shrink: 0;
}}
.timeline-track {{
  flex: 1;
  height: 28px;
  background: var(--surface);
  border-radius: 6px;
  position: relative;
  overflow: hidden;
}}
.timeline-fill {{
  height: 100%;
  border-radius: 6px;
  display: flex;
  align-items: center;
  padding-left: 8px;
  font-family: var(--mono);
  font-size: 0.72rem;
  color: #fff;
  white-space: nowrap;
  min-width: 40px;
}}
.gate-badge {{
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 600;
  margin-left: 10px;
  font-family: var(--mono);
}}
.gate-pass {{ background: rgba(74,222,128,0.15); color: var(--green); }}
.gate-fail {{ background: rgba(248,113,113,0.15); color: var(--red); }}

/* Search/filter */
.filter-bar {{
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  flex-wrap: wrap;
  align-items: center;
}}
.filter-bar input {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 14px;
  color: var(--text);
  font-size: 0.85rem;
  outline: none;
  width: 280px;
}}
.filter-bar input:focus {{ border-color: var(--accent); }}
.filter-bar select {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  color: var(--text);
  font-size: 0.85rem;
  outline: none;
}}

/* Trace cards */
.trace-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 12px;
  overflow: hidden;
  transition: border-color 0.15s;
}}
.trace-card:hover {{ border-color: var(--accent-dim); }}
.trace-header {{
  padding: 14px 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  user-select: none;
}}
.trace-header:hover {{ background: var(--surface-hover); }}
.trace-chevron {{
  transition: transform 0.2s;
  color: var(--text-dim);
  font-size: 0.8rem;
  flex-shrink: 0;
}}
.trace-card.open .trace-chevron {{ transform: rotate(90deg); }}
.trace-agent {{
  font-weight: 600;
  font-family: var(--mono);
  font-size: 0.88rem;
  color: var(--cyan);
}}
.trace-stage-badge {{
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(108,140,255,0.12);
  color: var(--accent);
  font-family: var(--mono);
}}
.trace-page-badge {{
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(167,139,250,0.12);
  color: var(--purple);
  font-family: var(--mono);
}}
.trace-meta {{
  margin-left: auto;
  display: flex;
  gap: 14px;
  font-size: 0.78rem;
  color: var(--text-dim);
  font-family: var(--mono);
  flex-shrink: 0;
}}
.trace-body {{ display: none; padding: 0 18px 18px; }}
.trace-card.open .trace-body {{ display: block; }}

/* Tabs inside trace */
.tab-bar {{
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
}}
.tab-btn {{
  padding: 8px 16px;
  font-size: 0.8rem;
  color: var(--text-dim);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
  font-family: var(--sans);
}}
.tab-btn:hover {{ color: var(--text); }}
.tab-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}

/* Iteration list */
.iter-item {{
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 8px;
  overflow: hidden;
}}
.iter-header {{
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  font-size: 0.82rem;
}}
.iter-header:hover {{ background: var(--surface-hover); }}
.iter-num {{
  font-family: var(--mono);
  font-weight: 700;
  color: var(--accent);
  min-width: 28px;
}}
.iter-tool-count {{
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--orange);
}}
.iter-tokens {{
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--text-dim);
  margin-left: auto;
}}
.iter-body {{ display: none; padding: 10px 14px; border-top: 1px solid var(--border); }}
.iter-item.open .iter-body {{ display: block; }}

/* Tool call display */
.tool-call {{
  background: rgba(0,0,0,0.2);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
  font-size: 0.8rem;
}}
.tool-name {{
  font-family: var(--mono);
  font-weight: 600;
  color: var(--orange);
}}
.tool-detail-label {{
  color: var(--text-dim);
  font-size: 0.72rem;
  text-transform: uppercase;
  margin-top: 6px;
  margin-bottom: 2px;
}}

/* Verification badge */
.verify-pass {{ color: var(--green); font-weight: 600; }}
.verify-fail {{ color: var(--red); font-weight: 600; }}

/* Code / pre */
pre {{
  background: rgba(0,0,0,0.3);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  font-family: var(--mono);
  font-size: 0.78rem;
  line-height: 1.5;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
}}
.prompt-section {{
  margin-bottom: 12px;
}}
.prompt-label {{
  font-size: 0.72rem;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-bottom: 4px;
  font-weight: 600;
  letter-spacing: 0.03em;
}}

.hidden {{ display: none !important; }}
</style>
</head>
<body>

<h1>DesignForge Trace Viewer</h1>
<div class="subtitle" id="runInfo"></div>

<div class="summary-grid" id="summaryGrid"></div>

<div class="timeline-section">
  <h2>Stage Timeline</h2>
  <div id="timeline"></div>
</div>

<div class="filter-bar">
  <input type="text" id="searchInput" placeholder="Search agent, page, tool...">
  <select id="stageFilter"><option value="">All Stages</option></select>
  <select id="typeFilter">
    <option value="">All Types</option>
    <option value="single">Single Call (LLM/VLM)</option>
    <option value="loop">Agent Loop (ToolAgent)</option>
  </select>
</div>

<div id="traceList"></div>

<script>
const DATA = {embedded_json};
const manifest = DATA.manifest;
const traces = DATA.traces;

const STAGE_COLORS = {{
  map: '#6c8cff', reduce: '#a78bfa', contract: '#22d3ee',
  generate: '#4ade80', merge: '#fbbf24', build: '#fb923c', verify: '#f87171',
}};

// --- Summary ---
const totals = manifest.totals || {{}};
const grid = document.getElementById('summaryGrid');
const cards = [
  ['Total Calls', totals.total_calls || 0],
  ['Input Tokens', (totals.total_input_tokens || 0).toLocaleString()],
  ['Output Tokens', (totals.total_output_tokens || 0).toLocaleString()],
  ['LLM Time', (totals.total_llm_time_sec || 0).toFixed(1) + 's'],
  ['Pipeline Time', (totals.pipeline_time_sec || 0).toFixed(1) + 's'],
  ['Est. Cost', '$' + (totals.estimated_cost_usd || 0).toFixed(4)],
];
grid.innerHTML = cards.map(([l,v]) =>
  `<div class="summary-card"><div class="value">${{v}}</div><div class="label">${{l}}</div></div>`
).join('');

document.getElementById('runInfo').textContent =
  `Run ${{manifest.run_id || '?'}} | Finished ${{manifest.finished_at || '?'}} | Config: ${{
    [manifest.config?.vision_model, manifest.config?.text_model, manifest.config?.agent_model].filter(Boolean).join(' / ')
  }}`;

// --- Timeline ---
const stages = manifest.stages || [];
const maxTime = Math.max(...stages.map(s => s.elapsed_sec || 0), 1);
const timeline = document.getElementById('timeline');
timeline.innerHTML = stages.map(s => {{
  const pct = Math.max(((s.elapsed_sec || 0) / maxTime) * 100, 3);
  const color = STAGE_COLORS[s.name] || '#6c8cff';
  const gate = s.gate;
  let gateBadge = '';
  if (gate) {{
    gateBadge = gate.passed
      ? '<span class="gate-badge gate-pass">GATE PASS</span>'
      : `<span class="gate-badge gate-fail">GATE FAIL (${{gate.errors?.length || 0}})</span>`;
  }}
  return `<div class="timeline-bar-wrap">
    <div class="timeline-label">${{s.name}}</div>
    <div class="timeline-track">
      <div class="timeline-fill" style="width:${{pct}}%;background:${{color}}">${{(s.elapsed_sec||0).toFixed(1)}}s</div>
    </div>
    ${{gateBadge}}
  </div>`;
}}).join('');

// --- Stage filter options ---
const stageFilter = document.getElementById('stageFilter');
const stageNames = [...new Set(traces.map(t => t.stage).filter(Boolean))];
stageNames.forEach(s => {{
  const opt = document.createElement('option');
  opt.value = s; opt.textContent = s;
  stageFilter.appendChild(opt);
}});

// --- Render traces ---
const traceList = document.getElementById('traceList');

function escapeHtml(s) {{
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2) || '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

function truncate(s, max) {{
  if (!s || s.length <= max) return s || '';
  return s.slice(0, max) + '...';
}}

function renderTrace(t, idx) {{
  const isLoop = !!t.iterations;
  const tokens = t.tokens || {{}};
  const totalTokens = (tokens.input || 0) + (tokens.output || 0);
  const iters = isLoop ? (t.total_iterations || t.iterations?.length || 0) : '';

  const card = document.createElement('div');
  card.className = 'trace-card';
  card.dataset.agent = t.agent || '';
  card.dataset.stage = t.stage || '';
  card.dataset.page = t.page_id || '';
  card.dataset.type = isLoop ? 'loop' : 'single';
  card.dataset.searchable = [t.agent, t.stage, t.page_id, t._rel_path].join(' ').toLowerCase();

  let headerMeta = `<span>${{(t.elapsed_sec || t.total_elapsed_sec || 0).toFixed(1)}}s</span>`;
  headerMeta += `<span>${{totalTokens.toLocaleString()}} tok</span>`;
  if (isLoop) headerMeta += `<span>${{iters}} iters</span>`;

  card.innerHTML = `
    <div class="trace-header" onclick="this.parentElement.classList.toggle('open')">
      <span class="trace-chevron">&#9654;</span>
      <span class="trace-agent">${{escapeHtml(t.agent)}}</span>
      <span class="trace-stage-badge">${{escapeHtml(t.stage)}}</span>
      ${{t.page_id ? `<span class="trace-page-badge">${{escapeHtml(t.page_id)}}</span>` : ''}}
      <div class="trace-meta">${{headerMeta}}</div>
    </div>
    <div class="trace-body" id="tbody-${{idx}}"></div>
  `;

  traceList.appendChild(card);

  // Lazy-render body on first open
  let rendered = false;
  card.querySelector('.trace-header').addEventListener('click', () => {{
    if (rendered) return;
    rendered = true;
    const body = card.querySelector('.trace-body');
    body.innerHTML = isLoop ? renderLoopBody(t) : renderSingleBody(t);
    initTabs(body);
  }});
}}

function renderSingleBody(t) {{
  const inp = t.input || {{}};
  return `
    <div class="tab-bar">
      <button class="tab-btn active" data-tab="prompts">Prompts</button>
      <button class="tab-btn" data-tab="output">Output</button>
      <button class="tab-btn" data-tab="meta">Metadata</button>
    </div>
    <div class="tab-panel active" data-tab="prompts">
      <div class="prompt-section">
        <div class="prompt-label">System Prompt</div>
        <pre>${{escapeHtml(truncate(inp.system, 5000))}}</pre>
      </div>
      <div class="prompt-section">
        <div class="prompt-label">User Prompt${{inp.image_path ? ' (+ image: ' + escapeHtml(inp.image_path) + ')' : ''}}</div>
        <pre>${{escapeHtml(truncate(inp.user, 8000))}}</pre>
      </div>
    </div>
    <div class="tab-panel" data-tab="output">
      <pre>${{escapeHtml(truncate(t.output?.raw_text, 10000))}}</pre>
    </div>
    <div class="tab-panel" data-tab="meta">
      <pre>${{escapeHtml(JSON.stringify({{
        trace_id: t.trace_id, model: t.model, call_type: t.call_type,
        elapsed_sec: t.elapsed_sec, tokens: t.tokens,
        retry_count: t.retry_count, error: t.error,
        started_at: t.started_at, path: t._rel_path,
      }}, null, 2))}}</pre>
    </div>
  `;
}}

function renderLoopBody(t) {{
  const inp = t.input || {{}};
  const iterations = t.iterations || [];
  let itersHtml = iterations.map((it, i) => {{
    const tc = it.tool_calls || [];
    const v = it.verification;
    let toolsHtml = tc.map(c => `
      <div class="tool-call">
        <span class="tool-name">${{escapeHtml(c.name)}}</span>
        <span style="color:var(--text-dim);font-size:0.72rem;margin-left:6px">${{(c.elapsed_sec||0).toFixed(3)}}s</span>
        <div class="tool-detail-label">Arguments</div>
        <pre>${{escapeHtml(JSON.stringify(c.arguments, null, 2))}}</pre>
        <div class="tool-detail-label">Result</div>
        <pre>${{escapeHtml(truncate(c.result, 3000))}}</pre>
      </div>
    `).join('');

    let verifyHtml = '';
    if (v) {{
      if (v.passed) {{
        verifyHtml = '<div class="verify-pass">Verification PASSED</div>';
      }} else {{
        verifyHtml = `<div class="verify-fail">Verification FAILED</div>
          <ul style="color:var(--red);font-size:0.8rem;padding-left:18px;margin-top:4px">
            ${{(v.errors||[]).map(e => `<li>${{escapeHtml(e)}}</li>`).join('')}}
          </ul>`;
      }}
    }}

    const itTokens = it.tokens || {{}};
    return `
      <div class="iter-item" onclick="this.classList.toggle('open')">
        <div class="iter-header">
          <span class="iter-num">#${{it.iteration}}</span>
          <span style="color:var(--text-dim);font-size:0.75rem">${{it.finish_reason || '?'}}</span>
          ${{tc.length ? `<span class="iter-tool-count">${{tc.length}} tool call${{tc.length>1?'s':''}}</span>` : ''}}
          ${{v ? (v.passed ? '<span class="verify-pass" style="font-size:0.75rem">PASS</span>' : '<span class="verify-fail" style="font-size:0.75rem">FAIL</span>') : ''}}
          <span class="iter-tokens">${{(itTokens.input||0)+(itTokens.output||0)}} tok | ${{(it.elapsed_sec||0).toFixed(1)}}s</span>
        </div>
        <div class="iter-body">
          ${{it.assistant_text ? `<div class="prompt-section"><div class="prompt-label">Assistant Text</div><pre>${{escapeHtml(truncate(it.assistant_text, 5000))}}</pre></div>` : ''}}
          ${{toolsHtml}}
          ${{verifyHtml}}
        </div>
      </div>
    `;
  }}).join('');

  return `
    <div class="tab-bar">
      <button class="tab-btn active" data-tab="iterations">Iterations (${{iterations.length}})</button>
      <button class="tab-btn" data-tab="prompts">Initial Prompts</button>
      <button class="tab-btn" data-tab="meta">Metadata</button>
    </div>
    <div class="tab-panel active" data-tab="iterations">${{itersHtml}}</div>
    <div class="tab-panel" data-tab="prompts">
      <div class="prompt-section">
        <div class="prompt-label">System Prompt</div>
        <pre>${{escapeHtml(truncate(inp.system, 5000))}}</pre>
      </div>
      <div class="prompt-section">
        <div class="prompt-label">Initial User Content</div>
        <pre>${{escapeHtml(truncate(typeof inp.user === 'string' ? inp.user : JSON.stringify(inp.user, null, 2), 8000))}}</pre>
      </div>
    </div>
    <div class="tab-panel" data-tab="meta">
      <pre>${{escapeHtml(JSON.stringify({{
        trace_id: t.trace_id, model: t.model,
        total_elapsed_sec: t.total_elapsed_sec, total_iterations: t.total_iterations,
        tokens: t.tokens, started_at: t.started_at, path: t._rel_path,
      }}, null, 2))}}</pre>
    </div>
  `;
}}

function initTabs(container) {{
  container.querySelectorAll('.tab-btn').forEach(btn => {{
    btn.addEventListener('click', (e) => {{
      e.stopPropagation();
      const tabBar = btn.parentElement;
      const parent = tabBar.parentElement;
      tabBar.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      parent.querySelectorAll(':scope > .tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      parent.querySelector(`.tab-panel[data-tab="${{btn.dataset.tab}}"]`)?.classList.add('active');
    }});
  }});
}}

// Render all traces
traces.forEach((t, i) => renderTrace(t, i));

// --- Filter/search ---
const searchInput = document.getElementById('searchInput');
const typeFilter = document.getElementById('typeFilter');

function applyFilters() {{
  const q = searchInput.value.toLowerCase();
  const stage = stageFilter.value;
  const type = typeFilter.value;
  document.querySelectorAll('.trace-card').forEach(card => {{
    let show = true;
    if (q && !card.dataset.searchable.includes(q)) show = false;
    if (stage && card.dataset.stage !== stage) show = false;
    if (type && card.dataset.type !== type) show = false;
    card.classList.toggle('hidden', !show);
  }});
}}
searchInput.addEventListener('input', applyFilters);
stageFilter.addEventListener('change', applyFilters);
typeFilter.addEventListener('change', applyFilters);
</script>
</body>
</html>"""
