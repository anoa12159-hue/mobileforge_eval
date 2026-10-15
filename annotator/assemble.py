"""
Metadata assembly — merges Phase 1 descriptors + Phase 2 global analysis
into the final metadata.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ANNOTATOR_VERSION = "0.1"


def assemble_metadata(
    image_paths: list[str],
    descriptors: list[dict[str, Any]],
    app_analysis: dict[str, Any] | None,
    model_name: str,
) -> dict[str, Any]:
    """Merge per-page descriptors and global analysis into final metadata.

    Args:
        image_paths: Ordered screenshot paths.
        descriptors: Phase 1 descriptors (same order as image_paths).
        app_analysis: Phase 2 global analysis dict, or None if --describe-only.
        model_name: Model used for annotation.

    Returns:
        Complete metadata dict ready for JSON serialization.
    """
    pages = []
    relationships = (app_analysis or {}).get("page_relationships", [])
    relationship_map: dict[str, dict] = {}
    for rel in relationships:
        page_name = rel.get("page", "")
        if page_name:
            relationship_map[page_name] = rel

    for desc in descriptors:
        page_entry = dict(desc)
        page_name = desc.get("page_name", "")
        if page_name in relationship_map:
            rel = relationship_map[page_name]
            page_entry.setdefault("parent_page", rel.get("parent"))
            page_entry.setdefault("trigger", rel.get("trigger"))
        pages.append(page_entry)

    metadata: dict[str, Any] = {
        "annotation": {
            "model": model_name,
            "annotated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "annotator_version": ANNOTATOR_VERSION,
        },
        "app_name": (app_analysis or {}).get("app_name", "unknown"),
        "category": (app_analysis or {}).get("category", "other"),
        "tab_structure": (app_analysis or {}).get("tab_structure", []),
        "pages": pages,
        "page_relationships": relationships,
    }

    return metadata


def write_metadata(input_dir: str, metadata: dict[str, Any]) -> Path:
    """Write metadata.json to the input directory.

    Args:
        input_dir: App input directory containing the screenshots.
        metadata: Assembled metadata dict.

    Returns:
        Path to the written metadata.json file.
    """
    out_path = Path(input_dir) / "metadata.json"
    out_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[assemble] metadata.json 已写入: %s", out_path)
    return out_path
