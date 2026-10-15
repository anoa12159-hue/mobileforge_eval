"""
Annotator — VLM-based dataset annotation for app screenshots.

Generates structured metadata.json descriptors for input screenshots,
enabling efficient page matching in the evaluator pipeline.

Usage:
    python -m annotator --input input/wxreader-2
    python -m annotator --input input/ --batch
"""

from annotator.describe import describe_screenshot
from annotator.analyze_app import analyze_app
from annotator.assemble import assemble_metadata, write_metadata

__all__ = [
    "describe_screenshot",
    "analyze_app",
    "assemble_metadata",
    "write_metadata",
]
