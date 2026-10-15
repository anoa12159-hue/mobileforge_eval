"""
Failure-observation labelling tool (v2)
========================================

Builds an HTML labelling page that uses the *user-perception* taxonomy
defined in `experiments/failures/FAILURE_OBSERVATIONS.md`:

  C1   起始页空白
  C2   非预期的起始页（路由映射错误）
  C3   起始页目标交互区域不可达（缺失或被遮挡）
  C4   起始页有目标交互区域，但点击无反应
  C5   点击有反应，但跳转非预期页面
       C5.1  跳转或弹出不相关的页面
       C5.2  跳转空白页 / 报错页
       C5.3  跳转页面视觉差距过大
  OK   实际符合预期（自动评估假阳性）
  SKIP 看不准

Reuses the sample pool from `experiments/failures/b_bucket_sample.json`
(no re-sampling needed). Reads any pre-existing v1 (B1/B2/B3/B4) labels
from localStorage and migrates them automatically:

  B1 → C4, B2 → C1, B4 → C2  (unambiguous)
  B3 → kept as B3-PENDING and the UI highlights these for manual
       re-classification into OK or C5.1 (depending on whether the inline
       content matched the to_page reference).

The page also auto-rewrites any pre-existing labels left over from an
earlier ordering of the v2 schema (where C2/C3/C4/C5 had different
positions) so previously-labelled samples are not lost.

Outputs:
  experiments/failures/failure_label.html  (open in browser)

Storage key: `mobileforge_failure_labels_v2`
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


def materialize_before_screenshots(samples: list[dict[str, Any]], image_dir: Path) -> int:
    """Symlink each sample's `screenshot_before` (an absolute path captured at
    sample-time) into the image_dir co-located with the HTML, and rewrite the
    field in-place to a relative path so the browser can load it. Returns the
    number of newly resolved before-screenshots."""
    image_dir.mkdir(parents=True, exist_ok=True)
    n_resolved = 0
    for s in samples:
        before_abs = s.get("screenshot_before")
        if not before_abs or not Path(before_abs).exists():
            s["screenshot_before"] = None
            continue
        tag = f"{s['app']}__{s['model']}__{s['test_id']}__before"
        dst = image_dir / f"{tag}.png"
        if not (dst.exists() or dst.is_symlink()):
            try:
                os.symlink(os.path.abspath(before_abs), dst)
            except OSError:
                s["screenshot_before"] = None
                continue
        s["screenshot_before"] = f"{image_dir.name}/{dst.name}"
        n_resolved += 1
    return n_resolved


def build_html(samples: list[dict[str, Any]], out_path: Path) -> None:
    payload = json.dumps(samples, ensure_ascii=False)
    title = "MobileForge — Failure Observation labeller (v2)"
    css = """
* { box-sizing: border-box; }
body { margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #1a1a1a; color: #ddd; }
header { padding: 12px 20px; background: #2a2a2a; display: flex; align-items: center; gap: 12px;
         position: sticky; top: 0; z-index: 10; box-shadow: 0 2px 6px rgba(0,0,0,0.5); flex-wrap: wrap; }
header h1 { font-size: 16px; margin: 0; }
header .progress { font-size: 13px; opacity: 0.85; }
header button { background: #444; color: #fff; border: 0; padding: 6px 12px; border-radius: 4px;
                cursor: pointer; font-size: 12px; }
header button:hover { background: #555; }
.btn-export { background: #2c7a4d !important; }
.btn-import { background: #2a5d8a !important; }
.btn-clear  { background: #8a2a2a !important; }
.btn-migrate{ background: #b8860b !important; color: #111 !important; }
main { padding: 16px 20px 200px; }
.meta { background: #232323; padding: 10px 14px; border-radius: 6px;
        font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; line-height: 1.55;
        margin-bottom: 12px; }
.meta b { color: #ffc26d; }
.meta .test_id { color: #91d3ff; }
.meta .pending { color: #ffb74d; font-weight: bold; }
.cols { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; margin-bottom: 14px; }
.col { background: #232323; border-radius: 8px; padding: 10px; min-width: 0; }
.col h3 { margin: 0 0 6px 0; font-size: 11.5px; color: #ffc26d; text-transform: uppercase; letter-spacing: 0.04em; }
.col h3.agent { color: #91d3ff; }
.col .sub { font-size: 10.5px; opacity: 0.7; margin-bottom: 6px; line-height: 1.4; }
.col img { width: 100%; max-height: 720px; object-fit: contain; background: #111; border-radius: 4px; display: block; }
.col .missing { color: #888; font-style: italic; padding: 12px 0; text-align: center; }

/* Sticky action bar */
.actions { display: flex; gap: 8px; justify-content: center; padding: 12px;
           background: #2a2a2a; border-radius: 8px; flex-wrap: wrap;
           position: fixed; bottom: 12px; left: 12px; right: 12px; z-index: 5;
           box-shadow: 0 4px 12px rgba(0,0,0,0.6); }
.actions button { font-size: 13px; padding: 10px 14px; border: 0; border-radius: 6px; cursor: pointer;
                  color: #111; font-weight: 600; min-width: 130px; }
.actions kbd { background: #111; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-left: 6px; }
.btn-ok    { background: #66bb6a; }
.btn-c1    { background: #b39ddb; }
.btn-c2    { background: #ef9a9a; }
.btn-c3    { background: #ffe082; }
.btn-c4    { background: #80cbc4; }
.btn-c5-1  { background: #ff8a65; }
.btn-c5-2  { background: #ce93d8; }
.btn-c5-3  { background: #f48fb1; }
.btn-skip  { background: #999; }

.help { font-size: 11.5px; opacity: 0.7; margin-top: 10px; padding: 0 4px; line-height: 1.55; }
.help b { color: #ffc26d; }
.nav-btn { background: #444 !important; color: #fff !important; }
"""
    js = r"""
const SAMPLES = __PAYLOAD__;
const STORAGE_KEY = 'mobileforge_failure_labels_v2';
const V1_KEY = 'mobileforge_b_labels_v1';

const DEFINITIONS = {
  OK:    '实际符合预期 — 画面变化展示了 to_page reference 的内容（不论是 inline 弹出还是新页面），视觉也接近 reference。这是自动评估的假阳性失败。',
  C1:    '起始页空白 — from_page 渲染整页空白，没什么可点的。',
  C2:    '非预期的起始页（路由映射错误） — from_page 这个 route 上画的根本不是 reference 里的 from_page，是别的 page。',
  C3:    '起始页目标交互区域不可达 — from_page 渲染了，但 reference 里要点的元素 (a) 根本不在画面上 (agent 没画出来) 或 (b) 被其他元素 (modal / overlay / scrim / 固定 header) 遮挡，用户无法到达。注意：遮挡指的是 before-click 截图里就已有遮挡 (列 2)；如果是点击后才弹出的 modal，那是 C5.1。',
  C4:    '起始页有目标交互区域，但点击无反应 — 元素在画面上，但点了没出现任何新内容（无 modal、无新 page、无展开）。',
  C5:    '点击有反应，但跳转非预期页面 — 画面出现了新内容，但跟 to_page reference 不符。',
  'C5.1':'跳转或弹出不相关的页面 — 跳到了别的 page / 弹了不相关的 modal / 跳回了首页。',
  'C5.2':'跳转空白页、报错页 — 目标页面是空白 / stub / 占位 / 报错。',
  'C5.3':'跳转页面视觉差距过大 — 跳到了对的 to_page，内容也对得上，但视觉跟 reference 差距明显。',
  SKIP:  '看不准 — 关键元素被截图遮挡 / 不在视野内 / 信息不足。',
  'B3-PENDING': '从 v1 (B3 same-page inline) 迁移过来 — 需要重新审核：如果 inline 显示了 to_page 的内容选 OK，否则选 C5.1。',
};

const V1_TO_V2 = { B1: 'C4', B2: 'C1', B4: 'C2', SKIP: 'SKIP' };
// B3 is intentionally not auto-migrated; left as B3-PENDING.

// Compatibility shim: an earlier cut of v2 used a different ordering
// (C2 ↔ C3 ↔ C4 ↔ C5, with C4.x sub-tags). If any labels left in
// localStorage match the old ordering, rewrite them to the new one
// at load time so previously-labelled samples are not lost.
const OLD_V2_TO_NEW = {
  // C1 stayed as C1 (blank); OK / SKIP / B3-PENDING also unchanged.
  // Old C2 (no trigger)        -> new C3
  // Old C3 (no response)       -> new C4
  // Old C4 (response wrong)    -> new C5
  // Old C4.1/.2/.3             -> new C5.1/.2/.3
  // Old C5 (wrong start page)  -> new C2
  // To make this idempotent we only rewrite if the storage was clearly
  // produced by the old schema (detected by the presence of any "C4."
  // sub-tag, since the new schema uses "C5.").
};

function migrateOldV2InPlace() {
  const keys = Object.keys(annotations);
  const hasOldSubtag = keys.some(k => /^C4\.[123]$/.test(annotations[k] || ''));
  if (!hasOldSubtag) return 0;  // Already on new schema (or never used old)
  const remap = {
    'C2': 'C3',
    'C3': 'C4',
    'C4': 'C5',
    'C4.1': 'C5.1',
    'C4.2': 'C5.2',
    'C4.3': 'C5.3',
    'C5': 'C2',
  };
  let n = 0;
  for (const k of keys) {
    const v = annotations[k];
    if (remap[v]) { annotations[k] = remap[v]; n++; }
  }
  if (n) saveAnnotations(annotations);
  return n;
}

function loadAnnotations() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveAnnotations(a) { localStorage.setItem(STORAGE_KEY, JSON.stringify(a)); }
function annotationKey(s) { return `${s.app}::${s.model}::${s.test_id}`; }

let annotations = loadAnnotations();
let cursor = 0;

function migrateFromV1() {
  let v1raw;
  try { v1raw = JSON.parse(localStorage.getItem(V1_KEY) || '{}'); }
  catch (e) { v1raw = {}; }
  let migrated = 0, pending = 0, skipped = 0;
  for (const k of Object.keys(v1raw)) {
    if (annotations[k]) { skipped++; continue; }
    const v1 = v1raw[k];
    if (V1_TO_V2[v1]) {
      annotations[k] = V1_TO_V2[v1];
      migrated++;
    } else if (v1 === 'B3') {
      annotations[k] = 'B3-PENDING';
      pending++;
    } else {
      skipped++;
    }
  }
  saveAnnotations(annotations);
  alert(`Migration done.\n  ${migrated} v1 labels mapped (B1→C4, B2→C1, B4→C2, SKIP→SKIP).\n  ${pending} B3 records flagged as B3-PENDING for manual re-review (choose OK or C5.1).\n  ${skipped} v1 labels skipped (already labelled in v2 or unknown).`);
  cursor = findFirstUnlabelledOrPending();
  render();
}

function findFirstUnlabelledOrPending() {
  for (let i = 0; i < SAMPLES.length; i++) {
    const v = annotations[annotationKey(SAMPLES[i])];
    if (!v || v === 'B3-PENDING') return i;
  }
  return 0;
}

function jumpToNextPending() {
  for (let off = 1; off <= SAMPLES.length; off++) {
    const i = (cursor + off) % SAMPLES.length;
    const v = annotations[annotationKey(SAMPLES[i])];
    if (!v || v === 'B3-PENDING') { cursor = i; render(); return; }
  }
  alert('No more unlabelled or pending samples.');
}

function renderProgress() {
  const total = SAMPLES.length;
  const counts = { OK:0, C1:0, C2:0, C3:0, C4:0, 'C5.1':0, 'C5.2':0, 'C5.3':0, SKIP:0, 'B3-PENDING':0 };
  let labelled = 0;
  for (const s of SAMPLES) {
    const v = annotations[annotationKey(s)];
    if (!v) continue;
    if (counts[v] !== undefined) counts[v]++;
    if (v !== 'B3-PENDING') labelled++;
  }
  document.getElementById('progress').innerHTML = `
    <span><b>${labelled}</b> / ${total} done</span>
    &nbsp;|&nbsp;
    <span style="color:#66bb6a">OK ${counts.OK}</span>
    <span style="color:#b39ddb">C1 ${counts.C1}</span>
    <span style="color:#ef9a9a">C2 ${counts.C2}</span>
    <span style="color:#ffe082">C3 ${counts.C3}</span>
    <span style="color:#80cbc4">C4 ${counts.C4}</span>
    <span style="color:#ff8a65">C5.1 ${counts['C5.1']}</span>
    <span style="color:#ce93d8">C5.2 ${counts['C5.2']}</span>
    <span style="color:#f48fb1">C5.3 ${counts['C5.3']}</span>
    <span style="color:#999">SKIP ${counts.SKIP}</span>
    ${counts['B3-PENDING'] ? `&nbsp;|&nbsp;<span style="color:#ffb74d">B3-PENDING ${counts['B3-PENDING']}</span>` : ''}
  `;
}

function img(src, alt) {
  if (!src) return `<div class="missing">[no screenshot]</div>`;
  return `<img src="${src}" alt="${alt}" />`;
}

function render() {
  const s = SAMPLES[cursor];
  const k = annotationKey(s);
  const cur = annotations[k];
  const curDisplay = cur ? `<span style="color:#ffc26d">${cur}</span>` : '<span style="opacity:0.5">—</span>';
  const pendingTag = cur === 'B3-PENDING'
    ? '<div class="pending">⚠ B3-PENDING — choose OK (inline showed to_page content) or C5.1 (inline showed unrelated content)</div>'
    : '';
  document.getElementById('main').innerHTML = `
    <div class="meta">
      <div>[${cursor + 1} / ${SAMPLES.length}] <span class="test_id">${s.test_id}</span></div>
      <div><b>app</b>: ${s.app} &nbsp;|&nbsp; <b>model</b>: ${s.model} &nbsp;|&nbsp; <b>type</b>: ${s.type}</div>
      <div><b>from</b>: ${s.from_page_name || '?'} (${s.from_route_hint || '?'}) &nbsp;|&nbsp; <b>to</b>: ${s.to_page_name || '?'} (${s.to_route_hint || '?'})</div>
      <div><b>expected_route</b>: ${s.expected_route || '?'} &nbsp;→&nbsp; <b>final_url</b>: ${s.final_url || '?'}</div>
      <div><b>current label</b>: ${curDisplay}</div>
      ${pendingTag}
    </div>
    <div class="cols">
      <div class="col">
        <h3>1. Reference: from-page</h3>
        <div class="sub">What the agent was supposed to render.</div>
        ${img(s.from_reference_path, 'from reference')}
      </div>
      <div class="col">
        <h3 class="agent">2. Agent: from-page (before click)</h3>
        <div class="sub">What the agent actually rendered for from_page. Use to judge C1 / C2 / C3.</div>
        ${img(s.screenshot_before, 'agent before-click')}
      </div>
      <div class="col">
        <h3 class="agent">3. Agent: from-page (after click)</h3>
        <div class="sub">Same URL after the click. Compare to col 2 to see what (if anything) changed.</div>
        ${img(s.screenshot_after, 'agent after-click')}
      </div>
      <div class="col">
        <h3>4. Reference: to-page</h3>
        <div class="sub">Where the click should have gone.</div>
        ${img(s.to_reference_path, 'to reference')}
      </div>
    </div>
    <div class="actions">
      <button class="btn-ok"   onclick="label('OK')">OK <kbd>0</kbd></button>
      <button class="btn-c1"   onclick="label('C1')">C1 blank <kbd>1</kbd></button>
      <button class="btn-c2"   onclick="label('C2')">C2 wrong page <kbd>2</kbd></button>
      <button class="btn-c3"   onclick="label('C3')">C3 no/blocked trigger <kbd>3</kbd></button>
      <button class="btn-c4"   onclick="label('C4')">C4 no response <kbd>4</kbd></button>
      <button class="btn-c5-1" onclick="label('C5.1')">C5.1 wrong dest <kbd>5</kbd></button>
      <button class="btn-c5-2" onclick="label('C5.2')">C5.2 blank dest <kbd>6</kbd></button>
      <button class="btn-c5-3" onclick="label('C5.3')">C5.3 low fidelity <kbd>7</kbd></button>
      <button class="btn-skip" onclick="label('SKIP')">Skip <kbd>9</kbd></button>
    </div>
    <div class="help">
      <b>OK</b>: ${DEFINITIONS.OK}<br>
      <b>C1</b>: ${DEFINITIONS.C1}<br>
      <b>C2</b>: ${DEFINITIONS.C2}<br>
      <b>C3</b>: ${DEFINITIONS.C3}<br>
      <b>C4</b>: ${DEFINITIONS.C4}<br>
      <b>C5.1</b>: ${DEFINITIONS['C5.1']}<br>
      <b>C5.2</b>: ${DEFINITIONS['C5.2']}<br>
      <b>C5.3</b>: ${DEFINITIONS['C5.3']}<br>
      <b>SKIP</b>: ${DEFINITIONS.SKIP}<br>
      Use <kbd>←</kbd> / <kbd>→</kbd> to navigate without labelling. Press <kbd>p</kbd> to jump to the next pending/unlabelled item.
    </div>
  `;
  renderProgress();
}

function label(tag) {
  const s = SAMPLES[cursor];
  annotations[annotationKey(s)] = tag;
  saveAnnotations(annotations);
  if (cursor < SAMPLES.length - 1) cursor++;
  render();
}

function go(delta) {
  cursor = Math.max(0, Math.min(SAMPLES.length - 1, cursor + delta));
  render();
}

function exportAnnotations() {
  const records = SAMPLES.map(s => ({
    app: s.app, model: s.model, test_id: s.test_id, type: s.type,
    label: annotations[annotationKey(s)] || null,
  }));
  const data = JSON.stringify({
    schema: 'failure_observation_v2',
    exported_at: new Date().toISOString(),
    n_samples: SAMPLES.length,
    n_labelled: records.filter(r => r.label && r.label !== 'B3-PENDING').length,
    n_pending:  records.filter(r => r.label === 'B3-PENDING').length,
    records,
  }, null, 2);
  const blob = new Blob([data], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `failure_labels_v2_${new Date().toISOString().split('T')[0]}.json`;
  a.click();
}

function importAnnotations() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/json';
  input.onchange = e => {
    const file = e.target.files[0];
    const reader = new FileReader();
    reader.onload = ev => {
      try {
        const data = JSON.parse(ev.target.result);
        let n = 0;
        for (const r of data.records || []) {
          if (!r.label) continue;
          annotations[`${r.app}::${r.model}::${r.test_id}`] = r.label;
          n++;
        }
        saveAnnotations(annotations);
        render();
        alert(`Imported ${n} annotations.`);
      } catch (e) { alert('Failed to import: ' + e.message); }
    };
    reader.readAsText(file);
  };
  input.click();
}

function clearAnnotations() {
  if (!confirm('Erase all annotations from v2 storage?')) return;
  annotations = {};
  saveAnnotations(annotations);
  render();
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '0') label('OK');
  else if (e.key === '1') label('C1');
  else if (e.key === '2') label('C2');
  else if (e.key === '3') label('C3');
  else if (e.key === '4') label('C4');
  else if (e.key === '5') label('C5.1');
  else if (e.key === '6') label('C5.2');
  else if (e.key === '7') label('C5.3');
  else if (e.key === '9' || e.key.toLowerCase() === 's') label('SKIP');
  else if (e.key.toLowerCase() === 'p') jumpToNextPending();
  else if (e.key === 'ArrowLeft') go(-1);
  else if (e.key === 'ArrowRight') go(1);
});

window.addEventListener('load', () => {
  const remapped = migrateOldV2InPlace();
  if (remapped) {
    console.log(`[v2 schema upgrade] Rewrote ${remapped} labels from old ordering to new ordering.`);
  }
  cursor = findFirstUnlabelledOrPending();
  render();
});
"""
    js = js.replace("__PAYLOAD__", payload)

    out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div id="progress" class="progress">…</div>
  <div style="flex:1"></div>
  <button class="btn-migrate" onclick="migrateFromV1()" title="Read v1 (B1/B2/B3/B4) labels from localStorage and map them to v2.">Migrate v1 → v2</button>
  <button class="btn-export" onclick="exportAnnotations()">Export JSON</button>
  <button class="btn-import" onclick="importAnnotations()">Import JSON</button>
  <button class="nav-btn" onclick="go(-1)">← Prev</button>
  <button class="nav-btn" onclick="go(1)">Next →</button>
  <button class="nav-btn" onclick="jumpToNextPending()">Next pending (p)</button>
  <button class="btn-clear" onclick="clearAnnotations()">Clear v2</button>
</header>
<main id="main"></main>
<script>{js}</script>
</body>
</html>
"""
    out_path.write_text(out, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("experiments/failures/b_bucket_sample.json"),
        help="Sample pool produced by build_b_bucket_labeling.py (re-used as-is).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("experiments/failures/failure_label.html"),
    )
    args = parser.parse_args()

    if not args.samples.exists():
        raise SystemExit(
            f"Sample file not found: {args.samples}. "
            f"Run `python -m scripts.build_b_bucket_labeling` first to materialise the sample pool."
        )

    payload = json.loads(args.samples.read_text(encoding="utf-8"))
    samples = payload["samples"]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    image_dir = args.out.parent / "b_label_images"
    n_before = materialize_before_screenshots(samples, image_dir)
    print(f"Resolved {n_before}/{len(samples)} before-click screenshots.")

    build_html(samples, args.out)
    print(f"Wrote {args.out} ({len(samples)} samples).")
    print(f"\nTwo ways to open:")
    print(f"  1. Direct: file://{args.out.resolve()}")
    print(f"  2. Via local server (recommended for Chrome):")
    print(f"       cd {args.out.parent.resolve()} && python -m http.server 8765")
    print(f"     then visit http://localhost:8765/{args.out.name}")
    print(f"\nIn the page, click 'Migrate v1 → v2' to import any existing B1/B2/B3/B4 labels.")


if __name__ == "__main__":
    main()
