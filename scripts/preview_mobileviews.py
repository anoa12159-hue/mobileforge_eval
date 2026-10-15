"""
Preview MobileViews dataset samples without downloading huge files.

Two approaches:
1. Download the small OneExample.zip (~9KB) for a complete app trace sample
2. Stream first N rows from a Parquet file to see screenshot + view hierarchy pairs
"""

import os
import json
import argparse
from pathlib import Path

OUTPUT_DIR = Path("./mobileviews_preview")


def download_one_example():
    """Download the tiny OneExample.zip from MobileViews."""
    from huggingface_hub import hf_hub_download

    print("=== Downloading OneExample.zip (small sample) ===")
    path = hf_hub_download(
        repo_id="mllmTeam/MobileViews",
        filename="MobileViews_Apps_CompleteTraces/OneExample.zip",
        repo_type="dataset",
        local_dir=str(OUTPUT_DIR / "traces"),
    )
    print(f"Downloaded to: {path}")

    import zipfile
    extract_dir = OUTPUT_DIR / "traces" / "OneExample"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as z:
        z.extractall(extract_dir)
    print(f"Extracted to: {extract_dir}")

    print("\n=== File structure ===")
    for p in sorted(extract_dir.rglob("*")):
        rel = p.relative_to(extract_dir)
        prefix = "  " * (len(rel.parts) - 1)
        print(f"{prefix}{p.name}" + ("/" if p.is_dir() else f"  ({p.stat().st_size / 1024:.1f} KB)"))

    for json_file in sorted(extract_dir.rglob("*.json"))[:2]:
        print(f"\n=== View Hierarchy sample: {json_file.name} ===")
        data = json.loads(json_file.read_text())
        print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
        print("... (truncated)")

    csv_files = list(extract_dir.rglob("*.csv"))
    for csv_file in csv_files[:1]:
        print(f"\n=== Actions CSV: {csv_file.name} ===")
        lines = csv_file.read_text().splitlines()
        for line in lines[:20]:
            print(line)
        if len(lines) > 20:
            print(f"... ({len(lines)} total lines)")


def download_app_metadata():
    """Download AppMetadata.csv (~4.78MB) to see what apps are included."""
    from huggingface_hub import hf_hub_download

    print("=== Downloading AppMetadata.csv ===")
    path = hf_hub_download(
        repo_id="mllmTeam/MobileViews",
        filename="AppMetadata.csv",
        repo_type="dataset",
        local_dir=str(OUTPUT_DIR),
    )
    print(f"Downloaded to: {path}")

    import csv
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\nTotal apps: {len(rows)}")
    print(f"Columns: {list(rows[0].keys()) if rows else 'N/A'}")

    print("\n=== First 10 apps ===")
    for row in rows[:10]:
        name = row.get("title", row.get("appId", "?"))
        category = row.get("genre", row.get("category", "?"))
        updated = row.get("lastUpdatedOn", "?")
        print(f"  {name} | {category} | updated: {updated}")

    print("\n=== Category distribution (top 15) ===")
    from collections import Counter
    categories = Counter(row.get("genre", row.get("category", "?")) for row in rows)
    for cat, count in categories.most_common(15):
        print(f"  {cat}: {count}")


def stream_parquet_samples(n=5):
    """Stream first N rows from a Parquet file without downloading the whole thing."""
    import pyarrow.parquet as pq

    repo_id = "mllmTeam/MobileViews"
    parquet_path = "MobileViews_Screenshots_ViewHierarchies/Parquets/MobileViews_0-150000.parquet"
    uri = f"hf://datasets/{repo_id}/{parquet_path}"

    print(f"=== Streaming first {n} rows from Parquet ===")
    print(f"URI: {uri}")

    pf = pq.ParquetFile(uri)
    print(f"Total row groups: {pf.num_row_groups}")
    print(f"Schema: {pf.schema_arrow}")

    table = pf.read_row_group(0)
    print(f"Row group 0: {table.num_rows} rows")

    save_dir = OUTPUT_DIR / "parquet_samples"
    save_dir.mkdir(parents=True, exist_ok=True)

    for i in range(min(n, table.num_rows)):
        img_bytes = table.column("image_content")[i].as_py()
        json_str = table.column("json_content")[i].as_py()

        img_path = save_dir / f"sample_{i}.jpg"
        img_path.write_bytes(img_bytes)
        print(f"\n--- Sample {i} ---")
        print(f"  Screenshot: {img_path} ({len(img_bytes) / 1024:.1f} KB)")

        vh = json.loads(json_str) if isinstance(json_str, str) else json_str
        json_path = save_dir / f"sample_{i}.json"
        json_path.write_text(json.dumps(vh, indent=2, ensure_ascii=False))
        print(f"  View Hierarchy: {json_path}")

        if isinstance(vh, dict):
            activity = vh.get("activity_name", vh.get("activity", "?"))
            print(f"  Activity: {activity}")
            root = vh.get("activity", {}).get("root", {})
            if root:
                cls = root.get("class", "?")
                children_count = len(root.get("children", []))
                print(f"  Root class: {cls}, children: {children_count}")

    print(f"\nSaved {min(n, table.num_rows)} samples to {save_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview MobileViews dataset")
    parser.add_argument(
        "mode",
        choices=["example", "metadata", "parquet", "all"],
        help="What to preview: example=OneExample.zip, metadata=AppMetadata.csv, parquet=stream Parquet samples",
    )
    parser.add_argument("-n", type=int, default=5, help="Number of Parquet samples to extract")
    parser.add_argument("-o", "--output", type=str, default="./mobileviews_preview", help="Output directory")
    args = parser.parse_args()

    OUTPUT_DIR = Path(args.output)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode in ("example", "all"):
        download_one_example()
    if args.mode in ("metadata", "all"):
        download_app_metadata()
    if args.mode in ("parquet", "all"):
        stream_parquet_samples(args.n)
