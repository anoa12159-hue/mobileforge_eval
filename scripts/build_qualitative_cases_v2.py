"""
Build a qualitative case gallery from v2 user-perception labels.

For every labelled record in `failure_labels_v2_*.json`, materialises:

  experiments/failures/qualitative_cases_v2/<LABEL>/<app>__<model>__<test_id>/
      1_ref_from.png        ← symlink to from_page reference
      2_agent_before.png    ← symlink to agent's from_page render (before click)
      3_agent_after.png     ← symlink to agent's from_page render (after click)
      4_ref_to.png          ← symlink to to_page reference
      grid_1x4.png          ← composite 1×4 image (paper-ready, horizontal)
      meta.json             ← test_id / app / model / type / label / urls

Plus an `INDEX.md` at the gallery root that lists every label bucket and
the cases it contains, with thumbnail-ish links to each grid.

Cases are auto-routed to `<LABEL>/` based on the v2 label assigned by the
user — no manual sorting needed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


LABEL_DIR_NAMES = {
    "OK":   "OK_actually_correct",
    "C1":   "C1_blank_start_page",
    "C2":   "C2_wrong_start_page",
    "C3":   "C3_trigger_unreachable",
    "C4":   "C4_no_response",
    "C5.1": "C5.1_wrong_destination",
    "C5.2": "C5.2_blank_destination",
    "C5.3": "C5.3_low_fidelity",
    "SKIP": "SKIP_uncertain",
}

LABEL_HUMAN = {
    "OK":   "Actually correct (false-positive nav fail)",
    "C1":   "Blank start page",
    "C2":   "Wrong page at this route",
    "C3":   "Trigger missing or occluded",
    "C4":   "Click had no response",
    "C5.1": "Navigated / opened wrong destination",
    "C5.2": "Navigated to blank / error page",
    "C5.3": "Navigated to right page but low visual fidelity",
    "SKIP": "Uncertain",
}

CELL_LABELS = [
    "1) Reference: from_page",
    "2) Agent: from_page (before click)",
    "3) Agent: from_page (after click)",
    "4) Reference: to_page",
]


def find_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/data/mm64/vannwu/miniconda3/fonts/Ubuntu-B.ttf",
        "/data/mm64/vannwu/miniconda3/fonts/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/data/mm64/vannwu/miniconda3/fonts/Ubuntu-R.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default()


def resolve_image_path(p: str | None, samples_dir: Path) -> str | None:
    """Resolve a path that may be relative to the samples dir; follow symlinks."""
    if not p:
        return None
    if Path(p).is_absolute():
        return p if Path(p).exists() else None
    candidate = (samples_dir / p).resolve()
    return str(candidate) if candidate.exists() else None


def load_sample_index(sample_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    samples_dir = sample_path.parent
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for s in payload["samples"]:
        d = dict(s)
        for k in ("from_reference_path", "screenshot_before",
                  "screenshot_after", "to_reference_path"):
            d[k] = resolve_image_path(s.get(k), samples_dir)
        out[(s["app"], s["model"], s["test_id"])] = d
    return out


def _same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def make_1x4_grid(
    image_paths: list[str | None],
    cell_titles: list[str],
    title: str,
    out_path: Path,
    cell_max_w: int = 380,
    pad: int = 16,
    title_h: int = 50,
    label_h: int = 28,
    bg: tuple[int, int, int] = (245, 245, 247),
    panel_bg: tuple[int, int, int] = (24, 24, 24),
    title_fg: tuple[int, int, int] = (20, 20, 20),
    label_fg: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Compose a 1×4 horizontal grid (paper-friendly): all 4 panels in one row.

    Reads order: (1) ref from-page → (2) agent before → (3) agent after →
    (4) ref to-page, mirroring the user's left-to-right reading flow when
    inspecting a navigation failure.
    """
    title_font = find_font(20)
    label_font = find_font(13)
    note_font = find_font(16)

    def load_or_placeholder(p: str | None, w: int, h: int) -> Image.Image:
        if p and Path(p).exists():
            try:
                return Image.open(p).convert("RGB")
            except Exception:
                pass
        ph = Image.new("RGB", (max(w, 200), max(h, 200)), (40, 40, 40))
        d = ImageDraw.Draw(ph)
        d.text((20, 20), "[no image]", fill=(180, 180, 180), font=label_font)
        return ph

    # Detect "no after-click screenshot was captured separately" — when
    # cell 3 (agent after-click) is the same file as cell 2 (agent before-click).
    after_is_same_as_before = _same_file(image_paths[1], image_paths[2])

    images = [load_or_placeholder(p, cell_max_w, cell_max_w) for p in image_paths]

    if after_is_same_as_before:
        before = images[1]
        ph = before.copy().convert("RGBA")
        overlay = Image.new("RGBA", ph.size, (0, 0, 0, 150))
        ph.alpha_composite(overlay)
        ph = ph.convert("RGB")
        d = ImageDraw.Draw(ph)
        msg_lines = [
            "(no change after click;",
            "URL unchanged,",
            "single screenshot)",
        ]
        line_h = note_font.size + 4
        total_h = line_h * len(msg_lines)
        y = (ph.height - total_h) // 2
        for line in msg_lines:
            bbox = d.textbbox((0, 0), line, font=note_font)
            tw = bbox[2] - bbox[0]
            x = (ph.width - tw) // 2
            d.text((x, y), line, fill=(255, 230, 180), font=note_font)
            y += line_h
        images[2] = ph

    cells: list[Image.Image] = []
    cell_h = 0
    for img in images:
        ar = img.height / img.width if img.width else 1.0
        new_w = cell_max_w
        new_h = max(1, int(new_w * ar))
        if new_h > 900:
            new_h = 900
            new_w = max(1, int(new_h / ar))
        img2 = img.resize((new_w, new_h), Image.LANCZOS)
        cells.append(img2)
        cell_h = max(cell_h, img2.height)

    canvas_w = (cell_max_w + pad) * 4 + pad
    canvas_h = title_h + cell_h + label_h + 2 * pad
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg)
    draw = ImageDraw.Draw(canvas)

    draw.text((pad, pad + 4), title, fill=title_fg, font=title_font)

    for i, (img, label) in enumerate(zip(cells, cell_titles)):
        x0 = pad + i * (cell_max_w + pad)
        y0 = title_h + pad
        panel = Image.new("RGB", (cell_max_w, cell_h + label_h), panel_bg)
        d2 = ImageDraw.Draw(panel)
        d2.text((10, 5), label, fill=label_fg, font=label_font)
        cx = (cell_max_w - img.width) // 2
        cy = label_h + (cell_h - img.height) // 2
        panel.paste(img, (cx, cy))
        canvas.paste(panel, (x0, y0))

    canvas.save(out_path, "PNG", optimize=True)


def build_one_case(
    case_data: dict[str, Any],
    label: str,
    out_root: Path,
) -> tuple[Path, list[str]]:
    bucket_dir = out_root / LABEL_DIR_NAMES.get(label, label.replace("/", "_"))
    case_id = f"{case_data['app']}__{case_data['model']}__{case_data['test_id']}"
    case_dir = bucket_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    img_specs = [
        ("1_ref_from.png",     case_data.get("from_reference_path")),
        ("2_agent_before.png", case_data.get("screenshot_before")),
        ("3_agent_after.png",  case_data.get("screenshot_after")),
        ("4_ref_to.png",       case_data.get("to_reference_path")),
    ]
    missing: list[str] = []
    for name, src in img_specs:
        dst = case_dir / name
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        if src and Path(src).exists():
            os.symlink(os.path.abspath(src), dst)
        else:
            missing.append(name)

    grid_path = case_dir / "grid_1x4.png"
    legacy_grid = case_dir / "grid_2x2.png"
    if legacy_grid.exists():
        legacy_grid.unlink()
    title = f"[{label}] {case_data['app']} · {case_data['model']} · {case_data['test_id']}"
    make_1x4_grid(
        [s for _, s in img_specs],
        CELL_LABELS,
        title,
        grid_path,
    )

    after_eq_before = _same_file(
        case_data.get("screenshot_before"),
        case_data.get("screenshot_after"),
    )
    meta = {
        "app": case_data["app"],
        "model": case_data["model"],
        "test_id": case_data["test_id"],
        "type": case_data["type"],
        "user_perception_label": label,
        "label_human_readable": LABEL_HUMAN.get(label, label),
        "from_page_name": case_data.get("from_page_name"),
        "from_route_hint": case_data.get("from_route_hint"),
        "to_page_name": case_data.get("to_page_name"),
        "to_route_hint": case_data.get("to_route_hint"),
        "expected_route": case_data.get("expected_route"),
        "final_url": case_data.get("final_url"),
        "error": case_data.get("error"),
        "screenshots_present": [n for n, _ in img_specs if (case_dir / n).is_symlink()],
        "screenshots_missing": missing,
        "after_screenshot_identical_to_before": after_eq_before,
    }
    (case_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return case_dir, missing


def render_index(
    out_root: Path,
    cases_by_label: dict[str, list[dict[str, Any]]],
) -> None:
    L: list[str] = []
    L.append("# Qualitative Failure Case Gallery (v2 schema)\n")
    L.append("Each case bundle contains:\n")
    L.append("- `1_ref_from.png` — reference for from_page")
    L.append("- `2_agent_before.png` — agent's from_page render before the click")
    L.append("- `3_agent_after.png` — agent's from_page render after the click")
    L.append("- `4_ref_to.png` — reference for to_page")
    L.append("- `grid_1x4.png` — composite 1×4 horizontal image (paper-ready)")
    L.append("- `meta.json` — app / model / test_id / route / label\n")
    L.append("---\n")
    L.append("## Bucket counts\n")
    L.append("| Label | Description | # cases |")
    L.append("|---|---|---:|")
    label_order = ["OK", "C1", "C2", "C3", "C4", "C5.1", "C5.2", "C5.3", "SKIP"]
    for lbl in label_order:
        n = len(cases_by_label.get(lbl, []))
        L.append(f"| **{lbl}** | {LABEL_HUMAN.get(lbl, lbl)} | {n} |")
    other = sorted(k for k in cases_by_label if k not in label_order)
    for lbl in other:
        n = len(cases_by_label[lbl])
        L.append(f"| `{lbl}` | (other) | {n} |")
    L.append("")

    for lbl in label_order + other:
        cases = cases_by_label.get(lbl, [])
        if not cases:
            continue
        bucket_dir = LABEL_DIR_NAMES.get(lbl, lbl.replace("/", "_"))
        L.append(f"\n## {lbl} — {LABEL_HUMAN.get(lbl, lbl)}  (n = {len(cases)})\n")
        L.append(f"_Directory_: `{bucket_dir}/`\n")
        L.append("| App | Model | Test ID | grid |")
        L.append("|---|---|---|---|")
        for c in sorted(cases, key=lambda x: (x["app"], x["model"], x["test_id"])):
            case_id = f"{c['app']}__{c['model']}__{c['test_id']}"
            grid_rel = f"{bucket_dir}/{case_id}/grid_1x4.png"
            L.append(f"| `{c['app']}` | `{c['model']}` | `{c['test_id']}` "
                     f"| [grid]({grid_rel}) |")
    (out_root / "INDEX.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path,
                        default=Path("experiments/failures/failure_labels_v2_2026-05-02.json"))
    parser.add_argument("--samples", type=Path,
                        default=Path("experiments/failures/b_bucket_sample.json"))
    parser.add_argument("--out-root", type=Path,
                        default=Path("experiments/failures/qualitative_cases_v2"))
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N cases (debug).")
    args = parser.parse_args()

    labels_data = json.loads(args.labels.read_text(encoding="utf-8"))
    sample_index = load_sample_index(args.samples)

    args.out_root.mkdir(parents=True, exist_ok=True)
    cases_by_label: dict[str, list[dict[str, Any]]] = {}

    n_done = 0
    n_missing_total = 0
    for r in labels_data["records"]:
        if not r.get("label"):
            continue
        key = (r["app"], r["model"], r["test_id"])
        case_data = sample_index.get(key)
        if not case_data:
            print(f"[warn] no sample data for {key}, skipping")
            continue
        case_dir, missing = build_one_case(case_data, r["label"], args.out_root)
        cases_by_label.setdefault(r["label"], []).append(case_data | {"label": r["label"]})
        n_done += 1
        if missing:
            n_missing_total += len(missing)
        if args.limit and n_done >= args.limit:
            break

    render_index(args.out_root, cases_by_label)
    print(f"Built {n_done} cases under {args.out_root}")
    print(f"Per bucket: {{ {', '.join(f'{k}:{len(v)}' for k, v in sorted(cases_by_label.items()))} }}")
    if n_missing_total:
        print(f"[note] {n_missing_total} screenshots could not be resolved (placeholder used in grid).")
    print(f"Index: {args.out_root / 'INDEX.md'}")


if __name__ == "__main__":
    main()
