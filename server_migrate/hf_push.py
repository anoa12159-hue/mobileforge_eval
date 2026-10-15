#!/usr/bin/env python3
"""Push local zip files to a HuggingFace dataset repo.

Behavior:
- Scans the workspace for *.zip files (excluding common junk dirs).
- Preserves relative directory structure when uploading
  (e.g. dataset/mobbin-scraper/selected.zip -> same path in repo).
- Skips files that already exist on the hub with identical size.
- Uploads small files first so progress shows quickly.

Usage:
    source env.sh                  # sets HF_TOKEN
    python server_migrate/hf_push.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List

from huggingface_hub import HfApi

REPO_ID = "unhappyNN/mobileforge"
WORK_DIR = Path("/data/mm64/vannwu/QQMail/OpenResearch/AgenticForge")
EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".cursor",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
}


def fmt(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.2f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.2f} TB"


def scan_zips(root: Path) -> List[Path]:
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".zip"):
                found.append(Path(dirpath) / fn)
    found.sort(key=lambda p: p.stat().st_size)
    return found


def fetch_remote_sizes(api: HfApi) -> dict[str, int]:
    try:
        info = api.repo_info(REPO_ID, repo_type="dataset", files_metadata=True)
    except Exception as e:
        print(f"WARN: cannot fetch existing files ({e}); will upload all", flush=True)
        return {}
    return {s.rfilename: (s.size or 0) for s in info.siblings}


def main() -> int:
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN not set. Run `source env.sh` first.", flush=True)
        return 1

    api = HfApi(token=token)
    print(
        f"==> create_repo({REPO_ID}, repo_type=dataset, private=True, exist_ok=True)",
        flush=True,
    )
    api.create_repo(repo_id=REPO_ID, repo_type="dataset", private=True, exist_ok=True)
    print("OK", flush=True)

    files = scan_zips(WORK_DIR)
    remote = fetch_remote_sizes(api)

    print(f"\n==> Found {len(files)} zip files in {WORK_DIR}:", flush=True)
    for p in files:
        rel = str(p.relative_to(WORK_DIR))
        sz = p.stat().st_size
        flag = "SKIP" if remote.get(rel) == sz else "QUEUE"
        print(f"  [{flag}]  {fmt(sz):>10}  {rel}", flush=True)

    uploaded = 0
    skipped = 0
    t_start = time.time()
    for p in files:
        rel = str(p.relative_to(WORK_DIR))
        sz = p.stat().st_size

        if remote.get(rel) == sz:
            print(f"\n--> SKIP {rel} (same size on hub)", flush=True)
            skipped += 1
            continue

        t0 = time.time()
        print(f"\n==> Uploading {rel} ({fmt(sz)})", flush=True)
        api.upload_file(
            path_or_fileobj=str(p),
            path_in_repo=rel,
            repo_id=REPO_ID,
            repo_type="dataset",
            commit_message=f"Upload {rel}",
        )
        dt = time.time() - t0
        speed = sz / dt if dt > 0 else 0
        print(f"OK  {rel} done in {dt:.1f}s ({fmt(speed)}/s)", flush=True)
        uploaded += sz

    elapsed = time.time() - t_start
    print(
        f"\n==> All done. Uploaded {fmt(uploaded)} (skipped {skipped} files) in "
        f"{elapsed:.1f}s -> https://huggingface.co/datasets/{REPO_ID}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
