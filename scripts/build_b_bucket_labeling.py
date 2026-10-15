"""
B-bucket labelling tool builder
================================

Stratified-samples B_NO_OP_CLICK records and emits a self-contained HTML
labelling page that the user can open in a browser. The labeller assigns
each sample to one of:

  B1 — Non-interactive element  (the visual element is present but has no
                                 onClick / <Link>; the agent forgot to
                                 wire up the action)
  B2 — Stub page                (the from-page is a "Coming soon"
                                 placeholder with no functional content)
  B3 — Same-page section        (the to-page content is rendered inline
                                 on the from-page; the agent made a
                                 different IA choice, so the test that
                                 expects a route change fails)
  ?  — Unsure / cannot tell

Outputs:
  experiments/failures/b_bucket_sample.json   — sampled records (frozen list)
  experiments/failures/b_bucket_label.html    — labelling UI

The HTML uses absolute `file://` paths to your local screenshots, so you
must open it from this same machine. Annotations live in `localStorage`
under the key `mobileforge_b_labels` and survive page reloads. The
"Export annotations" button downloads a JSON file you can hand back.

Design notes:
  - Skip page_exists records because B1/B2/B3 are only meaningful for
    click-based scenarios.
  - Stratified by (model x type), aiming for `--per-cell` items per cell.
  - The page shows three columns:
      [from-page reference]  [agent's actual from-page render]  [to-page reference]
    so the labeller can see (a) what the agent should have shown,
    (b) what it actually showed, and (c) where the click should have
    gone (helpful for spotting B3 same-page section).
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


def stratified_sample(
    records: list[dict[str, Any]],
    per_cell: int,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        cells[(r["model"], r["type"])].append(r)
    picked: list[dict[str, Any]] = []
    for key in sorted(cells.keys()):
        bucket_records = cells[key]
        n = min(per_cell, len(bucket_records))
        picked.extend(rng.sample(bucket_records, n))
    return picked


def resolve_reference(input_dir: Path, app: str, page_name: str, screenshot: str | None) -> str | None:
    if not screenshot:
        return None
    p = input_dir / app / screenshot
    if p.exists():
        return str(p.resolve())
    return None


RUNS_DIR_DEFAULT = Path("experiments/20260428_181902_mobileforge-compare/runs")
CLICK_MODEL_TAG = "gemini-2.5-flash"


def resolve_after_screenshot(app: str, model: str, test_id: str, runs_dir: Path = RUNS_DIR_DEFAULT) -> str | None:
    """Same lookup as build_failure_taxonomy: prefer __after.png, else __before.png."""
    base = runs_dir / f"{app}__{model}__agentic" / "eval" / "test_screenshots" / CLICK_MODEL_TAG
    for suffix in ("__after.png", "__before.png"):
        p = base / f"{test_id}{suffix}"
        if p.exists():
            return str(p)
    return None


def materialize_image(src: str | None, image_dir: Path, tag: str) -> str | None:
    """Symlink the source image into image_dir under a unique name and return
    the relative path (relative to image_dir's parent, i.e. the html file's dir)."""
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


def build_html(samples: list[dict[str, Any]], out_path: Path) -> None:
    payload = json.dumps(samples, ensure_ascii=False)
    title = "MobileForge B-bucket labeller"
    css = """
* { box-sizing: border-box; }
body { margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #1a1a1a; color: #ddd; }
header { padding: 12px 20px; background: #2a2a2a; display: flex; align-items: center; gap: 16px;
         position: sticky; top: 0; z-index: 10; box-shadow: 0 2px 6px rgba(0,0,0,0.5); }
header h1 { font-size: 18px; margin: 0; }
header .progress { font-size: 14px; opacity: 0.85; }
header button { background: #444; color: #fff; border: 0; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }
header button:hover { background: #555; }
.btn-export { background: #2c7a4d !important; }
.btn-import { background: #2a5d8a !important; }
.btn-clear { background: #8a2a2a !important; }
main { padding: 20px; }
.meta { background: #232323; padding: 12px 16px; border-radius: 6px; font-family: ui-monospace, "SF Mono", monospace; font-size: 12.5px; line-height: 1.55; margin-bottom: 16px; }
.meta b { color: #ffc26d; }
.meta .test_id { color: #91d3ff; }
.cols { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; margin-bottom: 18px; }
.col { background: #232323; border-radius: 8px; padding: 12px; }
.col h3 { margin: 0 0 8px 0; font-size: 13px; color: #ffc26d; text-transform: uppercase; letter-spacing: 0.05em; }
.col .sub { font-size: 11.5px; opacity: 0.7; margin-bottom: 8px; }
.col img { width: 100%; max-height: 720px; object-fit: contain; background: #111; border-radius: 4px; display: block; }
.col .missing { color: #888; font-style: italic; padding: 12px 0; text-align: center; }
.actions { display: flex; gap: 10px; justify-content: center; padding: 14px 0; background: #2a2a2a; border-radius: 8px; position: sticky; bottom: 0; z-index: 5; }
.actions button { font-size: 16px; padding: 12px 28px; border: 0; border-radius: 6px; cursor: pointer; color: #111; font-weight: 600; min-width: 220px; }
.btn-b1 { background: #ffe082; }
.btn-b2 { background: #b39ddb; }
.btn-b3 { background: #80cbc4; }
.btn-b4 { background: #ef9a9a; }
.btn-skip { background: #999; }
.actions button:hover { transform: translateY(-1px); transition: transform 0.08s; }
.actions kbd { background: #111; color: #fff; padding: 2px 7px; border-radius: 3px; font-size: 12px; margin-left: 8px; }
.help { font-size: 12px; opacity: 0.6; margin-top: 12px; padding: 0 4px; line-height: 1.6; }
.nav-btn { background: #444 !important; color: #fff !important; }
.label-history { font-size: 11px; opacity: 0.6; padding: 0 6px 8px; }
"""
    js = r"""
const SAMPLES = __PAYLOAD__;
const STORAGE_KEY = 'mobileforge_b_labels_v1';
const DEFINITIONS = {
  B1: 'Non-interactive element — the visual element is present in the agent render but is not wrapped in a Link / has no onClick handler.',
  B2: 'Stub page — the from-page itself is a "Coming soon" / blank placeholder; there is essentially nothing to click.',
  B3: 'Same-page section — the target content is rendered inline on the from-page (e.g. a section instead of a route). The agent made a different IA choice.',
  B4: 'Wrong page at expected route — the from-page render does not match the from-page reference at all (and is not a stub). Agent put the wrong page at this route or misinterpreted the page semantics.',
  SKIP: 'Cannot tell from the screenshots alone (e.g. the relevant element is below the fold).',
};

function loadAnnotations() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveAnnotations(a) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(a));
}
function annotationKey(s) { return `${s.app}::${s.model}::${s.test_id}`; }

let annotations = loadAnnotations();
let cursor = 0;

function findFirstUnlabelled() {
  for (let i = 0; i < SAMPLES.length; i++) {
    if (!annotations[annotationKey(SAMPLES[i])]) return i;
  }
  return SAMPLES.length - 1;
}

function renderProgress() {
  const labelled = SAMPLES.filter(s => annotations[annotationKey(s)]).length;
  const counts = { B1: 0, B2: 0, B3: 0, B4: 0, SKIP: 0 };
  for (const k of Object.keys(annotations)) {
    const v = annotations[k];
    if (counts[v] !== undefined) counts[v]++;
  }
  document.getElementById('progress').innerHTML = `
    <span><b>${labelled}</b> / ${SAMPLES.length} labelled</span>
    &nbsp;|&nbsp;
    <span style="color:#ffe082">B1: ${counts.B1}</span>
    <span style="color:#b39ddb">B2: ${counts.B2}</span>
    <span style="color:#80cbc4">B3: ${counts.B3}</span>
    <span style="color:#ef9a9a">B4: ${counts.B4}</span>
    <span style="color:#999">skip: ${counts.SKIP}</span>
  `;
}

function img(src, alt) {
  if (!src) return `<div class="missing">[no screenshot found]</div>`;
  return `<img src="${src}" alt="${alt}" />`;
}

function render() {
  const s = SAMPLES[cursor];
  const k = annotationKey(s);
  const cur = annotations[k] || '—';
  document.getElementById('main').innerHTML = `
    <div class="meta">
      <div>[${cursor + 1} / ${SAMPLES.length}] <span class="test_id">${s.test_id}</span></div>
      <div><b>app</b>: ${s.app} &nbsp;|&nbsp; <b>model</b>: ${s.model} &nbsp;|&nbsp; <b>type</b>: ${s.type}</div>
      <div><b>from page</b>: ${s.from_page_name || '?'} (${s.from_route_hint || '?'}) &nbsp;|&nbsp; <b>to page</b>: ${s.to_page_name || '?'} (${s.to_route_hint || '?'})</div>
      <div><b>expected_route</b>: ${s.expected_route || '?'} &nbsp;→&nbsp; <b>final_url</b>: ${s.final_url || '?'}</div>
      <div><b>current label</b>: <span style="color:#ffc26d">${cur}</span></div>
    </div>
    <div class="cols">
      <div class="col">
        <h3>1. Reference: from-page</h3>
        <div class="sub">What the agent was supposed to render.</div>
        ${img(s.from_reference_path, 'from reference')}
      </div>
      <div class="col">
        <h3>2. Agent: from-page (after click)</h3>
        <div class="sub">What the agent actually rendered. Click did nothing — URL did not change.</div>
        ${img(s.screenshot_after, 'agent render')}
      </div>
      <div class="col">
        <h3>3. Reference: to-page</h3>
        <div class="sub">Where the click should have gone (useful for spotting B3 inline rendering).</div>
        ${img(s.to_reference_path, 'to reference')}
      </div>
    </div>
    <div class="actions">
      <button class="btn-b1" onclick="label('B1')">B1 element no onClick <kbd>1</kbd></button>
      <button class="btn-b2" onclick="label('B2')">B2 stub page <kbd>2</kbd></button>
      <button class="btn-b3" onclick="label('B3')">B3 same-page inline <kbd>3</kbd></button>
      <button class="btn-b4" onclick="label('B4')">B4 wrong page <kbd>4</kbd></button>
      <button class="btn-skip" onclick="label('SKIP')">Skip <kbd>0</kbd></button>
    </div>
    <div class="help">
      <b>B1</b>: ${DEFINITIONS.B1}<br>
      <b>B2</b>: ${DEFINITIONS.B2}<br>
      <b>B3</b>: ${DEFINITIONS.B3}<br>
      <b>B4</b>: ${DEFINITIONS.B4}<br>
      <b>SKIP</b>: ${DEFINITIONS.SKIP}<br>
      Use <kbd>←</kbd> / <kbd>→</kbd> to navigate without labelling. Annotations save to localStorage automatically.
    </div>
  `;
  renderProgress();
}

function label(tag) {
  const s = SAMPLES[cursor];
  annotations[annotationKey(s)] = tag;
  saveAnnotations(annotations);
  if (cursor < SAMPLES.length - 1) {
    cursor++;
  }
  render();
}

function go(delta) {
  cursor = Math.max(0, Math.min(SAMPLES.length - 1, cursor + delta));
  render();
}

function exportAnnotations() {
  const records = SAMPLES.map(s => ({
    app: s.app,
    model: s.model,
    test_id: s.test_id,
    type: s.type,
    label: annotations[annotationKey(s)] || null,
  }));
  const data = JSON.stringify({
    exported_at: new Date().toISOString(),
    n_samples: SAMPLES.length,
    n_labelled: records.filter(r => r.label).length,
    records,
  }, null, 2);
  const blob = new Blob([data], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `b_bucket_labels_${new Date().toISOString().split('T')[0]}.json`;
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
        for (const r of data.records || []) {
          if (!r.label) continue;
          annotations[`${r.app}::${r.model}::${r.test_id}`] = r.label;
        }
        saveAnnotations(annotations);
        render();
        alert('Imported ' + Object.keys(annotations).length + ' annotations.');
      } catch (e) { alert('Failed to import: ' + e.message); }
    };
    reader.readAsText(file);
  };
  input.click();
}

function clearAnnotations() {
  if (!confirm('Erase all annotations?')) return;
  annotations = {};
  saveAnnotations(annotations);
  render();
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '1') label('B1');
  else if (e.key === '2') label('B2');
  else if (e.key === '3') label('B3');
  else if (e.key === '4') label('B4');
  else if (e.key === '0' || e.key.toLowerCase() === 's') label('SKIP');
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
  <button class="btn-clear" onclick="clearAnnotations()">Clear all</button>
</header>
<main id="main"></main>
<script>{js}</script>
</body>
</html>
"""
    out_path.write_text(out, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path, default=Path("experiments/failures/taxonomy.json"))
    parser.add_argument("--input-dir", type=Path, default=Path("input/mobileforge"))
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/failures"))
    parser.add_argument("--per-cell", type=int, default=6)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    tax = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    b_records = [
        r for r in tax["records"]
        if r["bucket"] == "B_NO_OP_CLICK" and r.get("type") in ("tab_navigation", "parent_child_navigation")
    ]

    cases = load_test_cases(args.input_dir)

    enriched: list[dict[str, Any]] = []
    skipped_no_screenshot = 0
    for r in b_records:
        case = cases.get(r["app"], {}).get(r["test_id"])
        if not case:
            continue
        from_p = case.get("from_page") or {}
        to_p = case.get("to_page") or {}
        after = r.get("screenshot_after")
        if not after or not Path(after).exists():
            after = resolve_after_screenshot(r["app"], r["model"], r["test_id"])
        if not after:
            # No screenshot at all — labelling is impossible. Skip from the sample
            # pool and report separately. These records remain in B_NO_OP_CLICK in
            # the taxonomy, just not eligible for B1/B2/B3 sub-labelling.
            skipped_no_screenshot += 1
            continue
        enriched.append({
            **r,
            "screenshot_after": after,
            "from_page_name": from_p.get("page_name"),
            "from_route_hint": from_p.get("route_hint"),
            "from_reference_path": resolve_reference(args.input_dir, r["app"], from_p.get("page_name"), from_p.get("reference_screenshot")),
            "to_page_name": to_p.get("page_name"),
            "to_route_hint": to_p.get("route_hint"),
            "to_reference_path": resolve_reference(args.input_dir, r["app"], to_p.get("page_name"), to_p.get("reference_screenshot")),
        })

    samples = stratified_sample(enriched, args.per_cell, args.seed)
    if skipped_no_screenshot:
        print(f"[note] skipped {skipped_no_screenshot} B records that have no after-screenshot on disk "
              f"(unlabelable). Eligible pool: {len(enriched)}.")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Symlink each referenced image into a co-located folder so the HTML can
    # use relative paths (works across browsers regardless of file:// rules).
    image_dir = args.out_dir / "b_label_images"
    if image_dir.exists():
        for old in image_dir.iterdir():
            if old.is_symlink() or old.is_file():
                old.unlink()
    for s in samples:
        tag_base = f"{s['app']}__{s['model']}__{s['test_id']}"
        s["from_reference_path"] = materialize_image(s["from_reference_path"], image_dir, f"{tag_base}__from_ref")
        s["screenshot_after"] = materialize_image(s.get("screenshot_after"), image_dir, f"{tag_base}__after")
        s["to_reference_path"] = materialize_image(s["to_reference_path"], image_dir, f"{tag_base}__to_ref")

    (args.out_dir / "b_bucket_sample.json").write_text(
        json.dumps({"n_samples": len(samples), "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path = args.out_dir / "b_bucket_label.html"
    build_html(samples, html_path)
    print(f"Sampled {len(samples)} of {len(enriched)} eligible B records.")
    print(f"\nTwo ways to open:")
    print(f"  1. Direct: file://{html_path.resolve()}")
    print(f"  2. Via local server (recommended for Chrome):")
    print(f"       cd {args.out_dir.resolve()} && python -m http.server 8765")
    print(f"     then visit http://localhost:8765/b_bucket_label.html")


if __name__ == "__main__":
    main()
