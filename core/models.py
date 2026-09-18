"""Plain data structures passed between the core and the UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.utils import format_timestamp, human_readable_size


@dataclass(slots=True)
class FileInfo:
    """Metadata common to every document the application accepts."""

    path: Path
    size_bytes: int
    modified: float

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
    def detail_label(self) -> str:
        """Secondary line shown under the file name in the review list."""
        return f"{self.size_label}  ·  {self.modified_label}"

    @property
    def key(self) -> str:
        """Stable identity used to detect duplicate selections."""
        try:
            return str(self.path.resolve()).lower()
        except OSError:  # pragma: no cover - defensive
            return str(self.path).lower()


@dataclass(slots=True)
class PdfFileInfo(FileInfo):
    """A PDF that has been inspected and accepted by the application.

    Instances are produced by :func:`core.validator.inspect_pdf` and are the
    unit of work for the merge tool.
    """

    page_count: int = 0
    title: str | None = None
    encrypted: bool = False
    #: Populated lazily by the thumbnail service; ``None`` means "not rendered".
    thumbnail: object | None = field(default=None, repr=False, compare=False)

    @property
    def page_label(self) -> str:
        """``"1 page"`` / ``"12 pages"``."""
        return "1 page" if self.page_count == 1 else f"{self.page_count} pages"

    @property
    def detail_label(self) -> str:
        """Pages, size and modification date."""
        return f"{self.page_label}  ·  {self.size_label}  ·  {self.modified_label}"


@dataclass(slots=True)
class DocxFileInfo(FileInfo):
    """A Word document that has been inspected and accepted.

    Produced by :func:`core.validator.inspect_docx` and used by the
    Word-to-Markdown converter.
    """

    paragraph_count: int = 0
    table_count: int = 0
    #: Path of this file relative to the folder the user selected, when the
    #: file came from a folder scan. Used to mirror the folder structure.
    relative_path: Path | None = None

    @property
    def content_label(self) -> str:
        """``"42 paragraphs · 2 tables"`` for display in the review list."""
        paragraphs = (
            "1 paragraph" if self.paragraph_count == 1 else f"{self.paragraph_count} paragraphs"
        )
        if not self.table_count:
            return paragraphs
        tables = "1 table" if self.table_count == 1 else f"{self.table_count} tables"
        return f"{paragraphs}  ·  {tables}"

    @property
    def detail_label(self) -> str:
        """Content summary, size and modification date."""
        return f"{self.content_label}  ·  {self.size_label}  ·  {self.modified_label}"


@dataclass(slots=True, frozen=True)
class OperationResult:
    """Summary of a completed merge, split or conversion job."""

    output_paths: list[Path]
    output_folder: Path
    pages_written: int
    message: str
    #: Per-file problems that did not stop the job (used by batch conversions).
    warnings: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        """Number of files produced."""
        return len(self.output_paths)
