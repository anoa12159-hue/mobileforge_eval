#!/usr/bin/env python3
"""
Check Tool – visually review metadata.json and test_cases.json for MobileForge apps.

Browse each app's pages with screenshots side-by-side, edit fields inline,
flag issues, and export reviewed JSON files (without overwriting originals).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testgen.generator import generate_all_cases

REVIEWER_VERSION = "0.1"

app = FastAPI(title="MobileForge Check Tool")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INPUT_DIR: Path = Path()


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _list_screenshots(app_dir: Path) -> set[str]:
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    return {f.name for f in app_dir.iterdir() if f.is_file() and f.suffix.lower() in exts}


# ── Auto-detection ───────────────────────────────────────────────────

def run_checks(metadata: dict, test_cases: dict | None, existing_files: set[str]) -> list[dict]:
    """Return a list of detected issues across metadata and test_cases."""
    issues: list[dict] = []
    pages = metadata.get("pages", [])
    valid_pages = [p for p in pages if "page_name" in p]
    for i, p in enumerate(pages):
        if "page_name" not in p:
            issues.append({
                "scope": "metadata",
                "target": f"pages[{i}]",
                "severity": "error",
                "message": f"pages[{i}] 缺少 page_name 字段（可能是标注失败的残留数据）",
            })
    page_names = {p["page_name"] for p in valid_pages}
    page_map = {p["page_name"]: p for p in valid_pages}
    tab_structure = metadata.get("tab_structure", [])
    relationships = metadata.get("page_relationships", [])

    # --- metadata checks ---
    for i, tab_name in enumerate(tab_structure):
        if tab_name not in page_names:
            issues.append({
                "scope": "metadata",
                "target": f"tab_structure[{i}]",
                "severity": "error",
                "message": f"tab_structure 中的 '{tab_name}' 在 pages 中没有对应定义",
            })
        else:
            pg = page_map[tab_name]
            if pg.get("page_type") != "main_page":
                issues.append({
                    "scope": "metadata",
                    "target": f"page:{tab_name}",
                    "severity": "warning",
                    "message": f"'{tab_name}' 在 tab_structure 中但 page_type 不是 main_page（当前: {pg.get('page_type')}）",
                })
            expected_pos = i + 1
            actual_pos = pg.get("structural_features", {}).get("tab_position")
            if actual_pos is not None and actual_pos != expected_pos:
                issues.append({
                    "scope": "metadata",
                    "target": f"page:{tab_name}",
                    "severity": "error",
                    "message": f"'{tab_name}' 的 tab_position={actual_pos}，但在 tab_structure 中排第 {expected_pos}",
                })

    tab_set = set(tab_structure)
    pos_seen: dict[int, list[str]] = {}
    for pg in pages:
        if pg.get("page_type") == "main_page":
            tp = pg.get("structural_features", {}).get("tab_position")
            if tp is not None:
                pos_seen.setdefault(tp, []).append(pg["page_name"])
                if pg["page_name"] not in tab_set:
                    issues.append({
                        "scope": "metadata",
                        "target": f"page:{pg['page_name']}",
                        "severity": "warning",
                        "message": f"'{pg['page_name']}' 是 main_page 且 tab_position={tp}，但不在 tab_structure 中",
                    })
    for tp, names in pos_seen.items():
        if len(names) > 1:
            issues.append({
                "scope": "metadata",
                "target": f"tab_position:{tp}",
                "severity": "error",
                "message": f"tab_position={tp} 被多个 main_page 共用: {', '.join(names)}",
            })

    for pg in pages:
        screenshot = pg.get("screenshot", "")
        if screenshot and screenshot not in existing_files:
            issues.append({
                "scope": "metadata",
                "target": f"page:{pg['page_name']}",
                "severity": "error",
                "message": f"截图文件 '{screenshot}' 不存在",
            })
        parent = pg.get("parent_page")
        if parent and parent not in page_names:
            issues.append({
                "scope": "metadata",
                "target": f"page:{pg['page_name']}",
                "severity": "error",
                "message": f"parent_page '{parent}' 在 pages 中不存在",
            })

    for rel in relationships:
        if rel["page"] not in page_names:
            issues.append({
                "scope": "metadata",
                "target": f"relationship:{rel['page']}",
                "severity": "error",
                "message": f"page_relationships 中的子页 '{rel['page']}' 在 pages 中不存在",
            })
        if rel["parent"] not in page_names:
            issues.append({
                "scope": "metadata",
                "target": f"relationship:{rel['page']}",
                "severity": "error",
                "message": f"page_relationships 中的父页 '{rel['parent']}' 在 pages 中不存在",
            })

    # --- test_cases checks ---
    if test_cases:
        for tc in test_cases.get("cases", []):
            tid = tc.get("test_id", "?")
            tc_type = tc.get("type", "")

            for side in ("from_page", "to_page"):
                ref = tc.get(side, {})
                pn = ref.get("page_name", "")
                if pn and pn not in page_names:
                    issues.append({
                        "scope": "test_case",
                        "target": f"case:{tid}",
                        "severity": "error",
                        "message": f"{side}.page_name '{pn}' 在 metadata.pages 中不存在",
                    })
                ss = ref.get("reference_screenshot", "")
                if ss and ss not in existing_files:
                    issues.append({
                        "scope": "test_case",
                        "target": f"case:{tid}",
                        "severity": "warning",
                        "message": f"{side}.reference_screenshot '{ss}' 文件不存在",
                    })

            action = tc.get("action", {})

            if tc_type == "tab_navigation":
                tp = action.get("tab_position")
                to_name = tc.get("to_page", {}).get("page_name", "")
                if tp is not None and to_name in page_map:
                    expected = page_map[to_name].get("structural_features", {}).get("tab_position")
                    if expected is not None and tp != expected:
                        issues.append({
                            "scope": "test_case",
                            "target": f"case:{tid}",
                            "severity": "error",
                            "message": f"action.tab_position={tp} 与目标页 '{to_name}' 的 tab_position={expected} 不一致",
                        })
                if tp is not None and 1 <= tp <= len(tab_structure):
                    expected_tab = tab_structure[tp - 1]
                    if to_name and to_name != expected_tab:
                        issues.append({
                            "scope": "test_case",
                            "target": f"case:{tid}",
                            "severity": "warning",
                            "message": f"tab_position={tp} 对应 tab_structure 中的 '{expected_tab}'，但 to_page 是 '{to_name}'",
                        })

            if tc_type == "parent_child_navigation":
                from_name = tc.get("from_page", {}).get("page_name", "")
                to_name = tc.get("to_page", {}).get("page_name", "")
                if from_name and to_name:
                    found = any(
                        r["parent"] == from_name and r["page"] == to_name
                        for r in relationships
                    )
                    if not found:
                        issues.append({
                            "scope": "test_case",
                            "target": f"case:{tid}",
                            "severity": "warning",
                            "message": f"parent_child 导航 '{from_name}' → '{to_name}' 在 page_relationships 中未找到",
                        })

    return issues


# ── API ──────────────────────────────────────────────────────────────

@app.get("/api/apps")
def list_apps():
    apps: list[dict] = []
    if not INPUT_DIR.exists():
        return apps
    for d in sorted(INPUT_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        meta_reviewed = _load_json(d / "metadata.reviewed.json")
        meta_original = _load_json(d / "metadata.json")
        if meta_original is None and meta_reviewed is None:
            continue
        meta = meta_reviewed if meta_reviewed is not None else meta_original
        has_tests = (d / "test_cases.json").exists() or (d / "test_cases.reviewed.json").exists()
        review_state = _load_json(d / "review_state.json")
        num_pages = len(meta.get("pages", []))
        deleted_screenshots = sorted(
            f.name[: -len(".deleted")]
            for f in d.iterdir()
            if f.is_file() and f.name.endswith(".deleted")
        )

        status = "pending"
        reviewed_count = 0
        issue_count = 0
        if review_state:
            page_states = review_state.get("pages", {})
            reviewed_count = sum(1 for v in page_states.values() if v.get("status") in ("pass", "fail"))
            issue_count = sum(1 for v in page_states.values() if v.get("status") == "fail")
            if reviewed_count >= num_pages:
                status = "done"
            elif reviewed_count > 0:
                status = "in_progress"

        apps.append({
            "slug": d.name,
            "app_name": meta.get("app_name", d.name),
            "category": meta.get("category", ""),
            "num_pages": num_pages,
            "has_tests": has_tests,
            "review_status": status,
            "reviewed_count": reviewed_count,
            "issue_count": issue_count,
            "deleted_screenshots": deleted_screenshots,
        })
    return apps


@app.get("/api/apps/{slug}/review")
def get_review(slug: str):
    app_dir = INPUT_DIR / slug
    if not app_dir.is_dir():
        raise HTTPException(404, f"App '{slug}' not found")

    meta_original = _load_json(app_dir / "metadata.json")
    if meta_original is None:
        raise HTTPException(404, f"metadata.json not found for '{slug}'")

    meta_reviewed = _load_json(app_dir / "metadata.reviewed.json")
    meta = meta_reviewed if meta_reviewed is not None else meta_original

    tc_original = _load_json(app_dir / "test_cases.json")
    tc_reviewed = _load_json(app_dir / "test_cases.reviewed.json")
    test_cases = tc_reviewed if tc_reviewed is not None else tc_original

    review_state = _load_json(app_dir / "review_state.json")
    existing_files = _list_screenshots(app_dir)
    deleted_screenshots = sorted(
        f.name[: -len(".deleted")]
        for f in app_dir.iterdir()
        if f.is_file() and f.name.endswith(".deleted")
    )

    issues = run_checks(meta, test_cases, existing_files)

    return {
        "slug": slug,
        "metadata": meta,
        "test_cases": test_cases,
        "review_state": review_state,
        "issues": issues,
        "screenshot_files": sorted(existing_files),
        "has_reviewed_metadata": meta_reviewed is not None,
        "has_reviewed_test_cases": tc_reviewed is not None,
        "deleted_screenshots": deleted_screenshots,
    }


@app.get("/api/apps/{slug}/image")
def get_image(slug: str, name: str = Query(...)):
    app_dir = INPUT_DIR / slug
    if not app_dir.is_dir():
        raise HTTPException(404, "App not found")
    img_path = (app_dir / name).resolve()
    if not str(img_path).startswith(str(app_dir.resolve())):
        raise HTTPException(403, "Access denied")
    if not img_path.exists():
        raise HTTPException(404, "Image not found")
    suffix = img_path.suffix.lower().lstrip(".")
    media_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    return FileResponse(img_path, media_type=media_types.get(suffix, "image/png"))


def _regenerate_test_cases(metadata: dict) -> dict:
    """Use testgen to regenerate test cases from metadata dict."""
    cases = generate_all_cases(metadata)
    from testgen.models import TestType
    data: dict[str, Any] = {
        "total_cases": len(cases),
        "by_type": {},
        "cases": [c.to_dict() for c in cases],
    }
    for t in TestType:
        count = sum(1 for c in cases if c.test_type == t)
        if count > 0:
            data["by_type"][t.value] = count
    return data


class SaveRequest(BaseModel):
    metadata: dict[str, Any]
    review_state: dict[str, Any]
    metadata_changed: bool = False
    delete_screenshots: list[str] = []


@app.post("/api/apps/{slug}/save")
def save_review(slug: str, req: SaveRequest):
    app_dir = INPUT_DIR / slug
    if not app_dir.is_dir():
        raise HTTPException(404, "App not found")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    reviewed_meta = req.metadata
    if "annotation" not in reviewed_meta:
        reviewed_meta["annotation"] = {}
    reviewed_meta["annotation"]["reviewed_at"] = now
    reviewed_meta["annotation"]["reviewer_version"] = REVIEWER_VERSION

    rels = reviewed_meta.get("page_relationships", [])
    if rels:
        seen = set()
        deduped = []
        for r in rels:
            key = (r.get("parent", ""), r.get("page", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        reviewed_meta["page_relationships"] = deduped

    with open(app_dir / "metadata.reviewed.json", "w", encoding="utf-8") as f:
        json.dump(reviewed_meta, f, indent=2, ensure_ascii=False)

    tc_regenerated = None
    if req.metadata_changed:
        reviewed_tc = _regenerate_test_cases(reviewed_meta)
        with open(app_dir / "test_cases.reviewed.json", "w", encoding="utf-8") as f:
            json.dump(reviewed_tc, f, indent=2, ensure_ascii=False)
        tc_regenerated = reviewed_tc["total_cases"]

    state = req.review_state

    page_names = {p["page_name"] for p in reviewed_meta.get("pages", [])}
    if "pages" in state:
        state["pages"] = {k: v for k, v in state["pages"].items() if k in page_names}

    tc_path = app_dir / "test_cases.reviewed.json"
    if not tc_path.exists():
        tc_path = app_dir / "test_cases.json"
    tc_data = _load_json(tc_path)
    if tc_data and "cases" in state:
        case_ids = {c["test_id"] for c in tc_data.get("cases", [])}
        state["cases"] = {k: v for k, v in state["cases"].items() if k in case_ids}

    state["saved_at"] = now
    state["reviewer_version"] = REVIEWER_VERSION
    with open(app_dir / "review_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    del_count = 0
    for fname in req.delete_screenshots:
        if "/" in fname or "\\" in fname or ".." in fname:
            continue
        src = app_dir / fname
        if src.exists() and src.is_file() and src.parent == app_dir:
            dest = app_dir / (fname + ".deleted")
            src.rename(dest)
            del_count += 1

    return {
        "ok": True,
        "saved_at": now,
        "metadata_changed": req.metadata_changed,
        "test_cases_regenerated": tc_regenerated,
        "screenshots_deleted": del_count,
    }


@app.post("/api/apps/{slug}/preview-testcases")
def preview_testcases(slug: str, metadata: dict[str, Any]):
    """Regenerate test cases from metadata in-memory (no file write)."""
    try:
        result = _regenerate_test_cases(metadata)
    except Exception as e:
        raise HTTPException(400, f"生成测试用例失败: {e}")
    return result


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = Path(__file__).parent / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "index.html not found")
    return index_path.read_text(encoding="utf-8")


def main():
    import uvicorn

    parser = argparse.ArgumentParser(
        description="MobileForge Check Tool — visual review of metadata & test cases",
    )
    parser.add_argument(
        "--input-dir", "-i",
        default="../../input/mobileforge",
        help="Path to mobileforge data directory (default: ../../input/mobileforge)",
    )
    parser.add_argument("--port", "-p", type=int, default=8767)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    global INPUT_DIR
    INPUT_DIR = Path(args.input_dir).resolve()

    if not INPUT_DIR.exists():
        print(f"ERROR: Input directory not found: {INPUT_DIR}")
        return

    print(f"Input dir: {INPUT_DIR}")
    print(f"Starting server at http://{args.host}:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
