"""
D-bucket labelling tool builder
================================

D_VISUAL_BOTTOM_NAV_PASSED contains records where:
  - nav_pass = True (the URL navigated correctly to the to_page route)
  - the visual judge (list-wise + reference anchor) ranked the agent's
    render at the *bottom* of all candidate models for that scenario

The default mapping treats every D record as **C5.3** (right page, low
visual fidelity). This labelling pass calibrates that assumption: a
fraction of D records may actually be:
  - **OK**:    visual judge mis-ranked them (the agent's render is fine)
  - **C5.1**:  agent rendered an unrelated page despite URL navigating
  - **C5.2**:  agent rendered a stub / blank / error page
  - **C5.3**:  the default — right page semantically, but visually degraded

This tool only needs two images per case (reference to_page vs agent's
render of to_page); the from-page is irrelevant for the D-bucket judgement.

Outputs:
  experiments/failures/d_bucket_sample.json   (frozen sample list)
  experiments/failures/d_bucket_label.html    (labeller UI)
  experiments/failures/d_label_images/        (symlinks for browser)

Storage key: `mobileforge_d_labels_v2`
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_test_cases(input_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for app_dir in sorted(input_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        tc_path = app_dir / "test_cases.reviewed.json"
        if not tc_path.exists():
            continue
        tc = json.loads(tc_path.read_text(encoding="utf-8"))
        out[app_dir.name] = {c["test_id"]: c for c in tc.get("cases", [])}
    return out


def load_metadata(input_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Load page metadata so we can resolve a to_page reference for
    page_exists tests too (those don't have a `to_page` field — the test
    targets a single page directly)."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for app_dir in sorted(input_dir.iterdir()):
        if not app_dir.is_dir():
            continue
        md_path = app_dir / "metadata.reviewed.json"
        if not md_path.exists():
            continue
        md = json.loads(md_path.read_text(encoding="utf-8"))
        out[app_dir.name] = {p["page_name"]: p for p in md.get("pages", [])}
    return out


def stratified_sample(records: list[dict[str, Any]], per_cell: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        cells[(r["model"], r["type"])].append(r)
    picked: list[dict[str, Any]] = []
    for key in sorted(cells.keys()):
        bucket = cells[key]
        n = min(per_cell, len(bucket))
        picked.extend(rng.sample(bucket, n))
    return picked


def resolve_reference(input_dir: Path, app: str, screenshot: str | None) -> str | None:
    if not screenshot:
        return None
    p = input_dir / app / screenshot
    return str(p.resolve()) if p.exists() else None


def materialize_image(src: str | None, image_dir: Path, tag: str) -> str | None:
    if not src or not Path(src).exists():
        return None
    image_dir.mkdir(parents=True, exist_ok=True)
    safe = tag.replace("/", "_").replace(" ", "_")
    dst = image_dir / f"{safe}.png"
    if not (dst.exists() or dst.is_symlink()):
        try:
            os.symlink(os.path.abspath(src), dst)
        except OSError:
            return None
    return f"{image_dir.name}/{dst.name}"


def parse_test_id(test_id: str, type_: str) -> tuple[str | None, str | None]:
    """Return (from_page_name, to_page_name) for a D-bucket test_id."""
    if type_ == "page_exists":
        # page_exists__<page_name>
        if test_id.startswith("page_exists__"):
            return None, test_id[len("page_exists__"):]
        return None, None
    if type_ == "tab_navigation":
        # tab_nav__<from>__to__<to>
        if "__to__" in test_id and test_id.startswith("tab_nav__"):
            mid = test_id[len("tab_nav__"):]
            from_p, _, to_p = mid.partition("__to__")
            return from_p, to_p
    if type_ == "parent_child_navigation":
        # parent_child__<from>__to__<to>
        if "__to__" in test_id and test_id.startswith("parent_child__"):
            mid = test_id[len("parent_child__"):]
            from_p, _, to_p = mid.partition("__to__")
            return from_p, to_p
    return None, None


def enrich_record(
    r: dict[str, Any],
    cases: dict[str, dict[str, dict[str, Any]]],
    metadata: dict[str, dict[str, dict[str, Any]]],
    input_dir: Path,
) -> dict[str, Any]:
    case = cases.get(r["app"], {}).get(r["test_id"])
    pages = metadata.get(r["app"], {})

    to_page_name: str | None = None
    to_route_hint: str | None = None
    to_screenshot: str | None = None

    if case is not None:
        to = case.get("to_page") or {}
        to_page_name = to.get("page_name")
        to_route_hint = to.get("route_hint")
        to_screenshot = to.get("reference_screenshot")

    if not to_page_name:
        # page_exists fallback — derive from test_id and look up via metadata
        _, parsed_to = parse_test_id(r["test_id"], r["type"])
        if parsed_to and parsed_to in pages:
            page = pages[parsed_to]
            to_page_name = page.get("page_name")
            to_route_hint = page.get("route_hint")
            to_screenshot = page.get("reference_screenshot")

    return {
        **r,
        "to_page_name": to_page_name,
        "to_route_hint": to_route_hint,
        "to_reference_path": resolve_reference(input_dir, r["app"], to_screenshot),
        "borda_score": r.get("borda_score"),
    }


def build_html(samples: list[dict[str, Any]], out_path: Path) -> None:
    payload = json.dumps(samples, ensure_ascii=False)
    title = "MobileForge — D-bucket Visual Fidelity labeller"
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
main { padding: 16px 20px 200px; }
.meta { background: #232323; padding: 10px 14px; border-radius: 6px;
        font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; line-height: 1.55;
        margin-bottom: 12px; }
.meta b { color: #ffc26d; }
.meta .test_id { color: #91d3ff; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; max-width: 1200px; margin-left:auto; margin-right:auto; }
.col { background: #232323; border-radius: 8px; padding: 12px; min-width: 0; }
.col h3 { margin: 0 0 6px 0; font-size: 12.5px; color: #ffc26d; text-transform: uppercase; letter-spacing: 0.04em; }
.col h3.agent { color: #91d3ff; }
.col .sub { font-size: 11.5px; opacity: 0.7; margin-bottom: 6px; line-height: 1.4; }
.col img { width: 100%; max-height: 800px; object-fit: contain; background: #111; border-radius: 4px; display: block; }
.col .missing { color: #888; font-style: italic; padding: 12px 0; text-align: center; }

.actions { display: flex; gap: 8px; justify-content: center; padding: 12px;
           background: #2a2a2a; border-radius: 8px; flex-wrap: wrap;
           position: fixed; bottom: 12px; left: 12px; right: 12px; z-index: 5;
           box-shadow: 0 4px 12px rgba(0,0,0,0.6); }
.actions button { font-size: 13px; padding: 10px 14px; border: 0; border-radius: 6px; cursor: pointer;
                  color: #111; font-weight: 600; min-width: 150px; }
.actions kbd { background: #111; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-left: 6px; }
.btn-ok    { background: #66bb6a; }
.btn-c5-1  { background: #ff8a65; }
.btn-c5-2  { background: #ce93d8; }
.btn-c5-3  { background: #f48fb1; }
.btn-skip  { background: #999; }

.help { font-size: 11.5px; opacity: 0.7; margin-top: 10px; padding: 0 4px; line-height: 1.55; max-width: 1200px; margin-left:auto; margin-right:auto; }
.help b { color: #ffc26d; }
.nav-btn { background: #444 !important; color: #fff !important; }
"""
    js = r"""
const SAMPLES = __PAYLOAD__;
const STORAGE_KEY = 'mobileforge_d_labels_v2';

const DEFINITIONS = {
  OK:    'Visual judge mis-ranked — the agent\'s rendering of to_page is actually close to the reference. Visual judge counted it as bottom but a reasonable user would consider it a match.',
  'C5.1':'Agent rendered the wrong page — the URL navigated, but what was drawn is unrelated to the to_page reference (different content entirely).',
  'C5.2':'Agent rendered a stub / blank / error page — the to_page route resolved to "Coming soon", a near-empty surface, a React error overlay, etc.',
  'C5.3':'Right page semantically, but low visual fidelity — content matches the to_page reference but the rendering is clearly degraded (sparse layout, placeholder text, broken styling, missing structural sections).',
  SKIP:  'Cannot tell from the screenshots alone (e.g. critical content below the fold or unrenderable).',
};

function loadAnnotations() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveAnnotations(a) { localStorage.setItem(STORAGE_KEY, JSON.stringify(a)); }
function annotationKey(s) { return `${s.app}::${s.model}::${s.test_id}`; }

let annotations = loadAnnotations();
let cursor = 0;

function findFirstUnlabelled() {
  for (let i = 0; i < SAMPLES.length; i++) {
    if (!annotations[annotationKey(SAMPLES[i])]) return i;
  }
  return 0;
}

function renderProgress() {
  const total = SAMPLES.length;
  const counts = { OK:0, 'C5.1':0, 'C5.2':0, 'C5.3':0, SKIP:0 };
  let labelled = 0;
  for (const s of SAMPLES) {
    const v = annotations[annotationKey(s)];
    if (!v) continue;
    if (counts[v] !== undefined) counts[v]++;
    labelled++;
  }
  document.getElementById('progress').innerHTML = `
    <span><b>${labelled}</b> / ${total} done</span>
    &nbsp;|&nbsp;
    <span style="color:#66bb6a">OK ${counts.OK}</span>
    <span style="color:#ff8a65">C5.1 ${counts['C5.1']}</span>
    <span style="color:#ce93d8">C5.2 ${counts['C5.2']}</span>
    <span style="color:#f48fb1">C5.3 ${counts['C5.3']}</span>
    <span style="color:#999">SKIP ${counts.SKIP}</span>
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
  document.getElementById('main').innerHTML = `
    <div class="meta">
      <div>[${cursor + 1} / ${SAMPLES.length}] <span class="test_id">${s.test_id}</span></div>
      <div><b>app</b>: ${s.app} &nbsp;|&nbsp; <b>model</b>: ${s.model} &nbsp;|&nbsp; <b>type</b>: ${s.type}</div>
      <div><b>to_page</b>: ${s.to_page_name || '?'} (${s.to_route_hint || '?'}) &nbsp;|&nbsp; <b>borda_score (visual)</b>: ${s.borda_score != null ? s.borda_score.toFixed(2) : '?'}</div>
      <div><b>current label</b>: ${curDisplay}</div>
    </div>
    <div class="cols">
      <div class="col">
        <h3>Reference: to_page</h3>
        <div class="sub">What the agent's to_page render should look like.</div>
        ${img(s.to_reference_path, 'to reference')}
      </div>
      <div class="col">
        <h3 class="agent">Agent: to_page render</h3>
        <div class="sub">What the agent actually rendered after navigating to the to_page route. Visual judge ranked this at the bottom.</div>
        ${img(s.screenshot_after, 'agent render')}
      </div>
    </div>
    <div class="actions">
      <button class="btn-ok"   onclick="label('OK')">OK (judge wrong) <kbd>0</kbd></button>
      <button class="btn-c5-1" onclick="label('C5.1')">C5.1 wrong page <kbd>1</kbd></button>
      <button class="btn-c5-2" onclick="label('C5.2')">C5.2 blank/stub <kbd>2</kbd></button>
      <button class="btn-c5-3" onclick="label('C5.3')">C5.3 low fidelity <kbd>3</kbd></button>
      <button class="btn-skip" onclick="label('SKIP')">Skip <kbd>9</kbd></button>
    </div>
    <div class="help">
      <b>OK</b>: ${DEFINITIONS.OK}<br>
      <b>C5.1</b>: ${DEFINITIONS['C5.1']}<br>
      <b>C5.2</b>: ${DEFINITIONS['C5.2']}<br>
      <b>C5.3</b>: ${DEFINITIONS['C5.3']}<br>
      <b>SKIP</b>: ${DEFINITIONS.SKIP}<br>
      Use <kbd>←</kbd> / <kbd>→</kbd> to navigate without labelling.
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
    borda_score: s.borda_score,
    label: annotations[annotationKey(s)] || null,
  }));
  const data = JSON.stringify({
    schema: 'd_bucket_failure_v2',
    exported_at: new Date().toISOString(),
    n_samples: SAMPLES.length,
    n_labelled: records.filter(r => r.label).length,
    records,
  }, null, 2);
  const blob = new Blob([data], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `d_labels_v2_${new Date().toISOString().split('T')[0]}.json`;
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
  if (!confirm('Erase all D-bucket annotations?')) return;
  annotations = {};
  saveAnnotations(annotations);
  render();
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '0') label('OK');
  else if (e.key === '1') label('C5.1');
  else if (e.key === '2') label('C5.2');
  else if (e.key === '3') label('C5.3');
  else if (e.key === '9' || e.key.toLowerCase() === 's') label('SKIP');
  else if (e.key === 'ArrowLeft') go(-1);
  else if (e.key === 'ArrowRight') go(1);
});

window.addEventListener('load', () => {
  cursor = findFirstUnlabelled();
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
  <button class="btn-export" onclick="exportAnnotations()">Export JSON</button>
  <button class="btn-import" onclick="importAnnotations()">Import JSON</button>
  <button class="nav-btn" onclick="go(-1)">← Prev</button>
  <button class="nav-btn" onclick="go(1)">Next →</button>
  <button class="btn-clear" onclick="clearAnnotations()">Clear</button>
</header>
<main id="main"></main>
<script>{js}</script>
</body>
</html>
"""
    out_path.write_text(out, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path,
                        default=Path("experiments/failures/taxonomy.json"))
    parser.add_argument("--input-dir", type=Path,
                        default=Path("input/mobileforge"))
    parser.add_argument("--out-dir", type=Path,
                        default=Path("experiments/failures"))
    parser.add_argument("--per-cell", type=int, default=3,
                        help="Stratified samples per (model × type) cell.")
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()

    tax = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    d_records = [r for r in tax["records"] if r["bucket"] == "D_VISUAL_BOTTOM_NAV_PASSED"]

    cases = load_test_cases(args.input_dir)
    metadata = load_metadata(args.input_dir)

    enriched: list[dict[str, Any]] = []
    skipped_no_screenshot = 0
    skipped_no_to_ref = 0
    for r in d_records:
        after = r.get("screenshot_after")
        if not after or not Path(after).exists():
            skipped_no_screenshot += 1
            continue
        e = enrich_record(r, cases, metadata, args.input_dir)
        if not e["to_reference_path"]:
            skipped_no_to_ref += 1
            continue
        enriched.append(e)

    samples = stratified_sample(enriched, args.per_cell, args.seed)
    if skipped_no_screenshot or skipped_no_to_ref:
        print(f"[note] skipped {skipped_no_screenshot} D records without after-screenshot, "
              f"{skipped_no_to_ref} without resolvable to_page reference. "
              f"Eligible pool: {len(enriched)}.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    image_dir = args.out_dir / "d_label_images"
    if image_dir.exists():
        for old in image_dir.iterdir():
            if old.is_symlink() or old.is_file():
                old.unlink()
    for s in samples:
        tag_base = f"{s['app']}__{s['model']}__{s['test_id']}"
        s["screenshot_after"] = materialize_image(s["screenshot_after"], image_dir, f"{tag_base}__after")
        s["to_reference_path"] = materialize_image(s["to_reference_path"], image_dir, f"{tag_base}__to_ref")

    sample_path = args.out_dir / "d_bucket_sample.json"
    sample_path.write_text(
        json.dumps({"n_samples": len(samples), "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path = args.out_dir / "d_bucket_label.html"
    build_html(samples, html_path)
    print(f"Sampled {len(samples)} of {len(enriched)} eligible D records "
          f"(per_cell={args.per_cell}).")
    print(f"\nOpen the labeller via local server (recommended for Chrome):")
    print(f"  cd {args.out_dir.resolve()} && python -m http.server 8765")
    print(f"  then visit http://localhost:8765/d_bucket_label.html")


if __name__ == "__main__":
    main()
