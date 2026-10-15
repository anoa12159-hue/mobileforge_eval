#!/usr/bin/env python3
"""Pull zip files from the HuggingFace dataset repo back to the workspace.

This is the inverse of ``hf_push.py``: it lists every file in the dataset
repo, then downloads each one to the same relative path inside the
workspace (so ``dataset/mobbin-scraper/selected.zip`` on the hub lands at
``<WORK_DIR>/dataset/mobbin-scraper/selected.zip`` locally).

Behavior:
- Skips files that already exist locally with identical size.
- Downloads small files first so progress shows quickly.
- Writes real files (no symlinks into the HF cache) so the workspace
  works even if the cache is later cleared.

Usage:
    source env.sh                   # sets HF_TOKEN
    python server_migrate/hf_pull.py             # download into the repo root
    AGENTICFORGE_DIR=/some/path python server_migrate/hf_pull.py
    python server_migrate/hf_pull.py --dry-run   # only list, do not download
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

from huggingface_hub import HfApi, hf_hub_download

REPO_ID = "unhappyNN/mobileforge"
DEFAULT_WORK_DIR = Path(__file__).resolve().parent.parent


def fmt(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.2f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.2f} TB"


def fetch_remote_files(api: HfApi) -> List[Tuple[str, int]]:
    """Return [(rfilename, size), ...] for every file in the repo, smallest first."""
    info = api.repo_info(REPO_ID, repo_type="dataset", files_metadata=True)
    files: List[Tuple[str, int]] = []
    for s in info.siblings:
        if s.rfilename.endswith(".zip"):
            files.append((s.rfilename, s.size or 0))
    files.sort(key=lambda x: x[1])
    return files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--work-dir",
        type=Path,
        default=Path(os.environ.get("AGENTICFORGE_DIR", str(DEFAULT_WORK_DIR))),
        help=(
            "Target workspace directory (default: repo root, "
            "or $AGENTICFORGE_DIR if set)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list what would be downloaded; do not actually fetch.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a same-size file already exists locally.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    work_dir: Path = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN not set. Run `source env.sh` first.", flush=True)
        return 1

    api = HfApi(token=token)
    print(f"==> repo:      {REPO_ID}", flush=True)
    print(f"==> work_dir:  {work_dir}", flush=True)

    try:
        files = fetch_remote_files(api)
    except Exception as e:
        print(f"ERROR: cannot list repo files ({e})", flush=True)
        return 2

    if not files:
        print("==> Repo is empty (no .zip files found).", flush=True)
        return 0

    total_remote = sum(sz for _, sz in files)
    print(
        f"\n==> Found {len(files)} zip files on the hub "
        f"(total {fmt(total_remote)}):",
        flush=True,
    )
    plan: List[Tuple[str, int, str]] = []  # (rel, size, action)
    for rel, sz in files:
        local = work_dir / rel
        if not args.force and local.exists() and local.stat().st_size == sz:
            action = "SKIP"
        else:
            action = "QUEUE"
        plan.append((rel, sz, action))
        print(f"  [{action}]  {fmt(sz):>10}  {rel}", flush=True)

    queued = [x for x in plan if x[2] == "QUEUE"]
    queued_bytes = sum(sz for _, sz, _ in queued)
    print(
        f"\n==> Plan: download {len(queued)} files "
        f"({fmt(queued_bytes)}), skip {len(plan) - len(queued)}.",
        flush=True,
    )

    if args.dry_run:
        print("==> Dry run, exiting without downloading.", flush=True)
        return 0

    downloaded = 0
    t_start = time.time()
    for rel, sz, action in plan:
        if action == "SKIP":
            print(f"\n--> SKIP {rel} (same size locally)", flush=True)
            continue

        t0 = time.time()
        print(f"\n==> Downloading {rel} ({fmt(sz)})", flush=True)
        hf_hub_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            filename=rel,
            local_dir=str(work_dir),
            token=token,
        )
        dt = time.time() - t0
        speed = sz / dt if dt > 0 else 0
        print(f"OK  {rel} done in {dt:.1f}s ({fmt(speed)}/s)", flush=True)
        downloaded += sz

    elapsed = time.time() - t_start
    print(
        f"\n==> All done. Downloaded {fmt(downloaded)} in {elapsed:.1f}s "
        f"-> {work_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
