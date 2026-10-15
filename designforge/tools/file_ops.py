"""File system operations used by tool-using agents (Page / Merge / Build)."""

from __future__ import annotations

import shutil
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from designforge import config

_sandbox_root: Path | None = None
_sandbox_lock = threading.Lock()


def set_sandbox(root: str) -> None:
    """Set the allowed root directory for all file operations."""
    global _sandbox_root
    with _sandbox_lock:
        _sandbox_root = Path(root).resolve()


def get_sandbox_root() -> Path | None:
    """Return the current sandbox root, or None if not set."""
    with _sandbox_lock:
        return _sandbox_root


def resolve_sandbox_path(path: str) -> Path:
    """Resolve *path* to an absolute ``Path`` within the sandbox.

    Relative paths are resolved against the sandbox root (NOT the process CWD).
    Absolute paths are resolved directly.  The result is validated to be inside
    the sandbox; ``PermissionError`` is raised otherwise.

    This is the single source of truth for "LLM path string → absolute path"
    conversion, used by both file operations and write-protection checks.
    """
    with _sandbox_lock:
        root = _sandbox_root

    raw = Path(path)
    if root is not None and not raw.is_absolute():
        p = (root / raw).resolve()
    else:
        p = raw.resolve()

    if root is not None and not (p == root or str(p).startswith(str(root) + "/")):
        raise PermissionError(
            f"Path '{path}' resolves outside the sandbox root '{root}'"
        )
    return p


def to_sandbox_relative(abs_path: str | Path) -> str:
    """Convert an absolute path to a sandbox-relative string.

    Used to normalise paths in tool results before returning them to the LLM,
    so the LLM never sees machine-specific absolute paths.  If the sandbox is
    not set or the path is not inside the sandbox, the original string is
    returned unchanged.
    """
    with _sandbox_lock:
        root = _sandbox_root
    if root is None:
        return str(abs_path)
    try:
        return str(Path(abs_path).relative_to(root))
    except ValueError:
        return str(abs_path)


def _check_sandbox(path: str) -> Path:
    """Resolve *path* and ensure it lives under the sandbox root.

    Thin wrapper kept for backward-compatibility; delegates to
    ``resolve_sandbox_path``.
    """
    return resolve_sandbox_path(path)


def _path_has_ignored_dir(path: Path) -> bool:
    ignore = config.LIST_FILES_IGNORE_DIR_NAMES
    return any(part in ignore for part in path.parts)


def _truncate_text_for_llm(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    msg = (
        f"\n\n...[truncated {omitted} characters; use grep on this file path "
        "to find symbols/lines, or request a smaller section]...\n\n"
    )
    budget = max_chars - len(msg)
    head = budget * 55 // 100
    tail = budget - head
    return text[:head] + msg + text[-tail:]


def write_file(path: str, content: str) -> str:
    p = _check_sandbox(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {to_sandbox_relative(p)}"


def read_file(path: str, max_chars: int | None = None) -> str:
    """Read file from disk. *max_chars* caps the string returned to the LLM (not disk read)."""
    p = _check_sandbox(path)
    if not p.exists():
        raise FileNotFoundError(f"{to_sandbox_relative(p)} does not exist")
    text = p.read_text(encoding="utf-8")
    if max_chars is None:
        limit = config.READ_FILE_MAX_CHARS
    else:
        limit = int(max_chars)
    if limit <= 0:
        return text
    return _truncate_text_for_llm(text, limit)


def str_replace(path: str, old_string: str, new_string: str) -> str:
    """Replace exactly one occurrence of old_string with new_string in the file."""
    p = _check_sandbox(path)
    rel = to_sandbox_relative(p)
    if not p.exists():
        raise FileNotFoundError(f"{rel} does not exist")
    content = p.read_text(encoding="utf-8")
    count = content.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {rel}")
    if count > 1:
        raise ValueError(
            f"old_string appears {count} times in {rel}; "
            "provide more context to make it unique"
        )
    new_content = content.replace(old_string, new_string, 1)
    p.write_text(new_content, encoding="utf-8")
    return f"replaced {len(old_string)} chars with {len(new_string)} chars in {rel}"


def list_files(directory: str, pattern: str = "*") -> list[str]:
    root = _check_sandbox(directory)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in root.rglob(pattern):
        if not p.is_file():
            continue
        if _path_has_ignored_dir(p):
            continue
        out.append(to_sandbox_relative(p))
    return sorted(out)


def delete_file(path: str) -> str:
    p = _check_sandbox(path)
    rel = to_sandbox_relative(p)
    if p.exists():
        p.unlink()
        return f"deleted {rel}"
    return f"{rel} does not exist"


def grep_files(
    directory: str,
    pattern: str,
    glob_pattern: str = "*",
    *,
    max_matches: int = 100,
    max_line_len: int = 240,
) -> str:
    """Search files under *directory* for lines containing *pattern* (literal substring)."""
    if not pattern:
        return "ERROR: pattern must be non-empty"
    root = _check_sandbox(directory)
    if not root.is_dir():
        return f"ERROR: not a directory: {to_sandbox_relative(root)}"

    matches: list[str] = []
    count = 0
    for path in sorted(root.rglob(glob_pattern)):
        if not path.is_file():
            continue
        if _path_has_ignored_dir(path):
            continue
        if count >= max_matches:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = to_sandbox_relative(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern not in line:
                continue
            trimmed = line.rstrip()
            if len(trimmed) > max_line_len:
                trimmed = trimmed[: max_line_len - 3] + "..."
            matches.append(f"{rel_path}:{line_no}:{trimmed}")
            count += 1
            if count >= max_matches:
                break

    if not matches:
        return "(no matches)"
    out = "\n".join(matches)
    if count >= max_matches:
        out += f"\n(truncated: showing at most {max_matches} matching lines)"
    return out


def batch_str_replace(edits: list[dict[str, str]]) -> str:
    """Apply multiple str_replace operations across files in a single call.

    Edits are grouped by file path. Within each file, all old_string positions
    are located on the *original* content, checked for overlaps, and then
    applied in reverse positional order so that earlier positions remain stable.

    Guarantees:
      - Per-file atomicity: if any edit in a file fails, that entire file is
        left unchanged; other files proceed independently.
      - Overlap detection: if two edits' [start, start+len) regions intersect
        within the same file, both are rejected with a descriptive message.
      - Error classification: not_found / ambiguous / overlap are reported
        separately so the caller can take appropriate action.

    Returns a structured text report for the LLM.
    """
    if not edits:
        return "ERROR: edits array is empty"

    grouped: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    bad_indices: list[int] = []
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict) or "path" not in edit:
            bad_indices.append(idx)
            continue
        grouped[edit["path"]].append((idx, edit))
    if bad_indices and not grouped:
        return (
            f"ERROR: all {len(edits)} edit(s) are malformed (not dicts or missing 'path'). "
            f"Each edit must be an object with keys: path, old_string, new_string."
        )
    bad_note = ""
    if bad_indices:
        bad_note = (
            f"\nWARNING: skipped {len(bad_indices)} malformed edit(s) at indices "
            f"{bad_indices}. Each edit must be an object with keys: path, old_string, new_string."
        )

    file_reports: list[str] = []
    total_ok = 0
    total_err = 0

    for path, indexed_edits in grouped.items():
        report = _apply_edits_to_file(path, indexed_edits)
        file_reports.append(report.summary)
        total_ok += report.ok_count
        total_err += report.err_count

    total_err += len(bad_indices)
    header = f"batch_str_replace: {total_ok} edit(s) applied, {total_err} failed"
    return header + bad_note + "\n\n" + "\n\n".join(file_reports)


class _FileEditReport:
    __slots__ = ("summary", "ok_count", "err_count")

    def __init__(self, summary: str, ok_count: int, err_count: int):
        self.summary = summary
        self.ok_count = ok_count
        self.err_count = err_count


def _apply_edits_to_file(
    path: str,
    indexed_edits: list[tuple[int, dict[str, str]]],
) -> _FileEditReport:
    """Validate and apply all edits for a single file atomically."""
    p = _check_sandbox(path)
    rel = to_sandbox_relative(p)
    if not p.exists():
        n = len(indexed_edits)
        return _FileEditReport(
            f"[{rel}] ERROR: file does not exist — {n} edit(s) skipped",
            ok_count=0, err_count=n,
        )

    content = p.read_text(encoding="utf-8")

    located: list[tuple[int, int, int, dict[str, str]]] = []
    errors: list[str] = []

    for edit_idx, edit in indexed_edits:
        old = edit["old_string"]
        count = content.count(old)
        if count == 0:
            errors.append(
                f"  edit[{edit_idx}] not_found: old_string ({len(old)} chars) "
                f"not in file — \"{old[:60]}{'...' if len(old) > 60 else ''}\""
            )
            continue
        if count > 1:
            errors.append(
                f"  edit[{edit_idx}] ambiguous: old_string appears {count} times "
                f"— provide more context to make it unique"
            )
            continue
        start = content.index(old)
        end = start + len(old)
        located.append((start, end, edit_idx, edit))

    overlap_errors = _detect_overlaps(located)
    if overlap_errors:
        errors.extend(overlap_errors)
        # On overlap: reject ALL edits for this file (atomicity).
        n = len(indexed_edits)
        detail = "\n".join(errors)
        return _FileEditReport(
            f"[{rel}] CONFLICT: {n} edit(s) rolled back\n{detail}",
            ok_count=0, err_count=n,
        )

    if errors:
        n = len(indexed_edits)
        detail = "\n".join(errors)
        return _FileEditReport(
            f"[{rel}] ERROR: {n} edit(s) rolled back (per-file atomicity)\n{detail}",
            ok_count=0, err_count=n,
        )

    located.sort(key=lambda t: t[0], reverse=True)

    new_content = content
    for start, end, edit_idx, edit in located:
        new_content = new_content[:start] + edit["new_string"] + new_content[end:]

    p.write_text(new_content, encoding="utf-8")

    ok = len(located)
    lines: list[str] = [f"[{rel}] OK: {ok} edit(s) applied"]
    for start, end, edit_idx, edit in sorted(located, key=lambda t: t[0]):
        old_len = len(edit["old_string"])
        new_len = len(edit["new_string"])
        lines.append(f"  edit[{edit_idx}]: replaced {old_len} → {new_len} chars at pos {start}")
    return _FileEditReport("\n".join(lines), ok_count=ok, err_count=0)


def _detect_overlaps(
    located: list[tuple[int, int, int, dict[str, str]]],
) -> list[str]:
    """Check for overlapping edit regions within a single file.

    Each entry is (start, end, edit_idx, edit_dict). Returns a list of
    human-readable overlap descriptions, or [] if all regions are disjoint.
    """
    if len(located) < 2:
        return []

    sorted_locs = sorted(located, key=lambda t: t[0])
    errors: list[str] = []

    for i in range(len(sorted_locs) - 1):
        s_a, e_a, idx_a, _ = sorted_locs[i]
        s_b, e_b, idx_b, _ = sorted_locs[i + 1]
        if e_a > s_b:
            errors.append(
                f"  overlap: edit[{idx_a}] region [{s_a}:{e_a}) "
                f"intersects edit[{idx_b}] region [{s_b}:{s_b + (e_b - s_b)})"
            )

    return errors


def ensure_dir(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return f"ensured directory {path}"


def copy_file(src: str, dst: str) -> str:
    src_p = _check_sandbox(src)
    dst_p = _check_sandbox(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src_p), str(dst_p))
    return f"copied {to_sandbox_relative(src_p)} -> {to_sandbox_relative(dst_p)}"
