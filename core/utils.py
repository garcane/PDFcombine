"""Small, dependency-light helpers shared across the application."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from core.constants import PDF_EXTENSIONS

logger = logging.getLogger(__name__)

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_readable_size(num_bytes: int) -> str:
    """Return ``num_bytes`` formatted for humans, e.g. ``"1.4 MB"``."""
    size = float(max(num_bytes, 0))
    for unit in _SIZE_UNITS:
        if size < 1024 or unit == _SIZE_UNITS[-1]:
            precision = 0 if unit == "B" or size >= 100 else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return f"{size:.1f} {_SIZE_UNITS[-1]}"  # pragma: no cover - unreachable


def format_timestamp(timestamp: float) -> str:
    """Format a POSIX ``timestamp`` as ``DD MMM YYYY HH:MM``."""
    try:
        return _dt.datetime.fromtimestamp(timestamp).strftime("%d %b %Y %H:%M")
    except (OverflowError, OSError, ValueError):  # pragma: no cover - defensive
        return "Unknown"


def is_pdf(path: Path | str) -> bool:
    """Return ``True`` when ``path`` has a PDF extension (case-insensitive)."""
    return Path(path).suffix.lower() in PDF_EXTENSIONS


def discover_pdfs(folder: Path, recursive: bool = False) -> list[Path]:
    """Return every PDF inside ``folder``, sorted naturally by file name.

    Non-PDF files are ignored. When ``recursive`` is ``True`` sub-folders are
    scanned as well, which is the building block for a future
    "combine folders recursively" tool.
    """
    pattern = "**/*" if recursive else "*"
    try:
        entries = folder.glob(pattern)
        found = [p for p in entries if p.is_file() and is_pdf(p)]
    except OSError as exc:
        logger.warning("Could not scan folder %s: %s", folder, exc)
        return []
    return sorted(found, key=lambda p: natural_sort_key(p.name))


def natural_sort_key(name: str) -> tuple[object, ...]:
    """Sort key that orders ``file2`` before ``file10``.

    Digit runs are compared numerically, everything else case-insensitively.
    """
    parts: list[object] = []
    buffer = ""
    is_digit = False
    for char in name:
        if char.isdigit() != is_digit and buffer:
            parts.append(int(buffer) if is_digit else buffer.lower())
            buffer = ""
        is_digit = char.isdigit()
        buffer += char
    if buffer:
        parts.append(int(buffer) if is_digit else buffer.lower())
    # Ints and strings are not mutually comparable, so tag each element with a
    # type rank to keep the key total-ordered.
    return tuple((0, value, "") if isinstance(value, int) else (1, 0, value) for value in parts)


def unique_path(path: Path) -> Path:
    """Return ``path`` if free, otherwise ``name (2).pdf``, ``name (3).pdf`` …"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def ensure_directory(folder: Path) -> None:
    """Create ``folder`` (including parents) if it does not already exist."""
    folder.mkdir(parents=True, exist_ok=True)


def deduplicate(paths: Iterable[Path]) -> list[Path]:
    """Return ``paths`` with duplicates removed, preserving first occurrence.

    Comparison is done on the resolved path so that ``.\\a.pdf`` and the
    absolute form of the same file are treated as one entry.
    """
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = path.resolve()
        except OSError:  # pragma: no cover - defensive
            key = path
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def open_in_file_manager(target: Path) -> bool:
    """Reveal ``target`` in the system file manager.

    Returns ``True`` on success. Failures are logged rather than raised: this
    is always a convenience action, never part of a critical path.
    """
    try:
        if sys.platform == "win32":
            if target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
            else:
                subprocess.run(["explorer", "/select,", str(target)], check=False)
        elif sys.platform == "darwin":
            args = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
            subprocess.run(args, check=False)
        else:
            folder = target if target.is_dir() else target.parent
            subprocess.run(["xdg-open", str(folder)], check=False)
        return True
    except Exception as exc:  # noqa: BLE001 - convenience action, never fatal
        logger.warning("Could not open %s in file manager: %s", target, exc)
        return False


def truncate_middle(text: str, max_length: int = 48) -> str:
    """Shorten ``text`` to ``max_length`` characters using a middle ellipsis."""
    if len(text) <= max_length:
        return text
    keep = max_length - 1
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}…{text[-tail:]}"
