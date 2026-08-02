"""Plain data structures passed between the core and the UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.utils import format_timestamp, human_readable_size


@dataclass(slots=True)
class PdfFileInfo:
    """Metadata describing a single PDF that has been accepted by the app.

    Instances are produced by :func:`core.validator.inspect_pdf` and are the
    unit of work for the merge tool.
    """

    path: Path
    page_count: int
    size_bytes: int
    modified: float
    title: str | None = None
    encrypted: bool = False
    #: Populated lazily by the thumbnail service; ``None`` means "not rendered".
    thumbnail: object | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        """File name including extension."""
        return self.path.name

    @property
    def stem(self) -> str:
        """File name without extension."""
        return self.path.stem

    @property
    def size_label(self) -> str:
        """Human readable file size, e.g. ``"2.1 MB"``."""
        return human_readable_size(self.size_bytes)

    @property
    def modified_label(self) -> str:
        """Human readable last-modified timestamp."""
        return format_timestamp(self.modified)

    @property
    def page_label(self) -> str:
        """``"1 page"`` / ``"12 pages"``."""
        return "1 page" if self.page_count == 1 else f"{self.page_count} pages"

    @property
    def key(self) -> str:
        """Stable identity used to detect duplicate selections."""
        try:
            return str(self.path.resolve()).lower()
        except OSError:  # pragma: no cover - defensive
            return str(self.path).lower()


@dataclass(slots=True, frozen=True)
class OperationResult:
    """Summary of a completed merge or split job."""

    output_paths: list[Path]
    output_folder: Path
    pages_written: int
    message: str

    @property
    def file_count(self) -> int:
        """Number of files produced."""
        return len(self.output_paths)
