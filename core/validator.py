"""Input validation: PDF inspection and page-selection parsing.

Every function here raises a :class:`core.exceptions.PdfToolkitError` subclass
whose message can be shown verbatim in a dialog. Nothing in this module writes
to disk.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError as PyPdfReadError

from core.constants import MAX_PAGE_NUMBER
from core.exceptions import PdfReadError, PdfWriteError, ValidationError
from core.models import PdfFileInfo
from core.utils import is_pdf

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# PDF inspection
# --------------------------------------------------------------------------- #
def inspect_pdf(path: Path) -> PdfFileInfo:
    """Read ``path`` and return its metadata.

    Args:
        path: Location of the PDF to inspect.

    Returns:
        A populated :class:`PdfFileInfo`.

    Raises:
        PdfReadError: The file is missing, empty, not a PDF, password
            protected, or otherwise unreadable.
    """
    if not path.exists():
        raise PdfReadError(f"'{path.name}' no longer exists at:\n{path.parent}")
    if not path.is_file():
        raise PdfReadError(f"'{path.name}' is not a file.")
    if not is_pdf(path):
        raise PdfReadError(f"'{path.name}' is not a PDF file.")

    try:
        stat = path.stat()
    except OSError as exc:
        raise PdfReadError(f"'{path.name}' could not be read.\n{exc}") from exc

    if stat.st_size == 0:
        raise PdfReadError(f"'{path.name}' is empty (0 bytes).")

    if not os.access(path, os.R_OK):
        raise PdfReadError(f"'{path.name}' cannot be opened - permission denied.")

    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = reader.is_encrypted
        if encrypted:
            # An empty user password is very common; try it before giving up.
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001 - unsupported cipher, treat as locked
                unlocked = 0
            if not unlocked:
                raise PdfReadError(
                    f"'{path.name}' is password protected and cannot be processed.\n"
                    "Remove the password and try again."
                )
        page_count = len(reader.pages)
        title = _safe_title(reader)
    except PdfReadError:
        raise
    except (PyPdfReadError, ValueError, OSError) as exc:
        logger.exception("Failed to parse %s", path)
        raise PdfReadError(f"'{path.name}' appears to be damaged or is not a valid PDF.\n{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - pypdf can raise arbitrary errors
        logger.exception("Unexpected error parsing %s", path)
        raise PdfReadError(f"'{path.name}' could not be read.\n{exc}") from exc

    if page_count == 0:
        raise PdfReadError(f"'{path.name}' contains no pages.")

    return PdfFileInfo(
        path=path,
        page_count=page_count,
        size_bytes=stat.st_size,
        modified=stat.st_mtime,
        title=title,
        encrypted=encrypted,
    )


def _safe_title(reader: PdfReader) -> str | None:
    """Return the document title from the PDF metadata, if any."""
    try:
        metadata = reader.metadata
        if metadata and metadata.title:
            return str(metadata.title).strip() or None
    except Exception:  # noqa: BLE001 - metadata is optional and often malformed
        pass
    return None


# --------------------------------------------------------------------------- #
# Output validation
# --------------------------------------------------------------------------- #
def validate_output_folder(folder: Path) -> None:
    """Ensure ``folder`` exists (or can be created) and is writable.

    Raises:
        PdfWriteError: The folder cannot be created or written to.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PdfWriteError(f"The output folder could not be created:\n{folder}\n\n{exc}") from exc

    if not os.access(folder, os.W_OK):
        raise PdfWriteError(f"The output folder is read-only:\n{folder}")


def validate_output_file(path: Path) -> None:
    """Ensure ``path`` can be written to.

    An existing file is allowed (the UI asks for overwrite confirmation first)
    but a read-only existing file is rejected up front.

    Raises:
        PdfWriteError: The destination folder or file is not writable.
    """
    validate_output_folder(path.parent)
    if path.exists():
        if path.is_dir():
            raise PdfWriteError(f"'{path.name}' is a folder, not a file.")
        if not os.access(path, os.W_OK):
            raise PdfWriteError(
                f"'{path.name}' is read-only and cannot be replaced.\n"
                "Choose a different name or clear the read-only flag."
            )


def validate_sources(files: list[PdfFileInfo], minimum: int = 2) -> None:
    """Check that a merge selection is usable.

    Raises:
        ValidationError: Fewer than ``minimum`` files are selected.
        PdfReadError: One of the files has disappeared since selection.
    """
    if len(files) < minimum:
        raise ValidationError(
            f"Select at least {minimum} PDF files to merge.\n"
            f"Currently selected: {len(files)}."
        )
    missing = [info.name for info in files if not info.path.exists()]
    if missing:
        listed = "\n".join(f"  • {name}" for name in missing[:10])
        raise PdfReadError(f"These files are no longer available:\n{listed}")


# --------------------------------------------------------------------------- #
# Page selection parsing
# --------------------------------------------------------------------------- #
def parse_page_ranges(text: str, page_count: int) -> list[tuple[int, int]]:
    """Parse ``"1-5, 6-10, 12"`` into inclusive 1-based ``(start, end)`` pairs.

    Args:
        text: Raw user input.
        page_count: Number of pages in the source document; used to bound-check.

    Returns:
        The ranges in the order the user typed them.

    Raises:
        ValidationError: The input is empty, malformed or out of bounds.
    """
    tokens = _tokenise(text)
    ranges: list[tuple[int, int]] = []

    for token in tokens:
        if "-" in token.strip("-"):
            start_text, _, end_text = token.partition("-")
            start = _parse_page_number(start_text, token, page_count)
            end = _parse_page_number(end_text, token, page_count)
            if start > end:
                raise ValidationError(
                    f"'{token}' is not a valid range - the first page must not be "
                    "greater than the last."
                )
            ranges.append((start, end))
        else:
            page = _parse_page_number(token, token, page_count)
            ranges.append((page, page))

    if not ranges:
        raise ValidationError("Enter at least one page range, for example: 1-5, 6-10")
    return ranges


def parse_page_list(text: str, page_count: int) -> list[int]:
    """Parse ``"1,4,7-9"`` into a sorted list of unique 1-based page numbers.

    Args:
        text: Raw user input.
        page_count: Number of pages in the source document.

    Returns:
        Ascending page numbers with duplicates removed.

    Raises:
        ValidationError: The input is empty, malformed or out of bounds.
    """
    pages: set[int] = set()
    for start, end in parse_page_ranges(text, page_count):
        pages.update(range(start, end + 1))
    if not pages:
        raise ValidationError("Enter at least one page number, for example: 1, 4, 7")
    return sorted(pages)


def _tokenise(text: str) -> list[str]:
    """Split user input on commas, semicolons and whitespace."""
    if not text or not text.strip():
        raise ValidationError("Enter the pages you want, for example: 1-5, 8, 11-14")
    normalised = text.replace(";", ",").replace("\n", ",")
    tokens = [token.strip() for token in normalised.split(",")]
    return [token for token in tokens if token]


def _parse_page_number(raw: str, token: str, page_count: int) -> int:
    """Convert one page-number string to an ``int`` and bound-check it."""
    value = raw.strip()
    if not value.isdigit():
        raise ValidationError(
            f"'{token}' is not a valid page selection.\n"
            "Use numbers and ranges only, for example: 1-5, 8, 11-14"
        )
    number = int(value)
    if number < 1:
        raise ValidationError(f"'{token}' is invalid - pages are numbered from 1.")
    if number > MAX_PAGE_NUMBER:
        raise ValidationError(f"'{token}' is far outside the document.")
    if number > page_count:
        raise ValidationError(
            f"'{token}' is out of range - this document has {page_count} "
            f"page{'s' if page_count != 1 else ''}."
        )
    return number
