"""
Unified logging configuration: Rich console + rotating file log.

Call `setup_logging()` once at startup. All modules use standard
`logging.getLogger(__name__)` — this module wires up the handlers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_console = Console()


def setup_logging(
    log_dir: Path,
    *,
    verbose: bool = False,
    console: Console | None = None,
) -> Path:
    """Configure root logger with console + file handlers.

    Returns the path to the log file.
    """
    console = console or _console
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)

    # --- Rich console handler (INFO or DEBUG) ---
    console_handler = RichHandler(
        rich_tracebacks=True,
        console=console,
        show_path=False,
        show_time=True,
    )
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_fmt = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_fmt)
    root.addHandler(console_handler)

    # --- File handler (always DEBUG, full detail) ---
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    logging.info("Log file: %s", log_file)
    return log_file
