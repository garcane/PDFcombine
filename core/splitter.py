"""Split tool: three ways of breaking one PDF into smaller documents."""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from core.constants import (
    EXTRACT_FILENAME_TEMPLATE,
    PAGE_FILENAME_TEMPLATE,
    RANGE_FILENAME_TEMPLATE,
)
from core.exceptions import PdfReadError, PdfWriteError
from core.models import OperationResult
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.utils import unique_path
from core.validator import (
    inspect_pdf,
    parse_page_list,
    parse_page_ranges,
    validate_output_folder,
)

logger = logging.getLogger(__name__)


class SplitMode(str, Enum):
    """The three supported split strategies."""

    EVERY_PAGE = "every_page"
    RANGES = "ranges"
    EXTRACT = "extract"

    @property
    def label(self) -> str:
        """Title shown in the UI."""
        return {
            SplitMode.EVERY_PAGE: "Split every page",
            SplitMode.RANGES: "Split by page ranges",
            SplitMode.EXTRACT: "Extract specific pages",
        }[self]

    @property
    def description(self) -> str:
        """One-line explanation shown under the title."""
        return {
            SplitMode.EVERY_PAGE: "Create one PDF for each page of the document.",
            SplitMode.RANGES: "Create one PDF per range, e.g. 1-5, 6-10, 11-20.",
            SplitMode.EXTRACT: "Build a single PDF from chosen pages, e.g. 1, 4, 7, 10.",
        }[self]


class PdfSplitTool:
    """Splits a single PDF according to a :class:`SplitMode`."""

    display_name = "Split PDF"

    def split(
        self,
        source: Path,
        output_folder: Path,
        mode: SplitMode,
        selection: str = "",
        overwrite: bool = False,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Split ``source`` into ``output_folder``.

        Args:
            source: PDF to split.
            output_folder: Destination folder, created when missing.
            mode: Which split strategy to apply.
            selection: Page expression, required for
                :attr:`SplitMode.RANGES` and :attr:`SplitMode.EXTRACT`.
            overwrite: Replace existing files instead of adding a ``(2)``
                suffix to the name.
            progress: Optional progress sink.
            token: Optional cancellation token.

        Returns:
            An :class:`OperationResult` listing every file written.

        Raises:
            ValidationError: ``selection`` is malformed or out of range.
            PdfReadError: ``source`` cannot be read.
            PdfWriteError: ``output_folder`` is not writable.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        info = inspect_pdf(source)
        validate_output_folder(output_folder)
        reader = self._open(source)

        if mode is SplitMode.EVERY_PAGE:
            jobs = self._jobs_every_page(info.page_count)
        elif mode is SplitMode.RANGES:
            jobs = self._jobs_ranges(selection, info.page_count, info.stem)
        else:
            jobs = self._jobs_extract(selection, info.page_count, info.stem)

        logger.info("Splitting %s using %s into %d file(s)", source, mode.value, len(jobs))

        written: list[Path] = []
        pages_written = 0
        total = len(jobs)

        for index, (filename, pages) in enumerate(jobs, start=1):
            token.raise_if_cancelled()
            progress.update(index - 1, total, f"Creating {filename}")
            destination = output_folder / filename
            if not overwrite:
                destination = unique_path(destination)
            self._write_pages(reader, pages, destination)
            written.append(destination)
            pages_written += len(pages)
            progress.update(index, total, f"Created {destination.name}")

        return OperationResult(
            output_paths=written,
            output_folder=output_folder,
            pages_written=pages_written,
            message=self._summary(mode, written, pages_written),
        )

    # ----------------------------------------------------------------- #
    # Job planning - each job is (filename, 1-based page numbers)
    # ----------------------------------------------------------------- #
    @staticmethod
    def _jobs_every_page(page_count: int) -> list[tuple[str, list[int]]]:
        """One output file per page."""
        return [
            (PAGE_FILENAME_TEMPLATE.format(index=page), [page])
            for page in range(1, page_count + 1)
        ]

    @staticmethod
    def _jobs_ranges(selection: str, page_count: int, stem: str) -> list[tuple[str, list[int]]]:
        """One output file per user supplied range."""
        ranges = parse_page_ranges(selection, page_count)
        return [
            (
                RANGE_FILENAME_TEMPLATE.format(stem=stem, start=start, end=end),
                list(range(start, end + 1)),
            )
            for start, end in ranges
        ]

    @staticmethod
    def _jobs_extract(selection: str, page_count: int, stem: str) -> list[tuple[str, list[int]]]:
        """A single output file containing the chosen pages."""
        pages = parse_page_list(selection, page_count)
        return [(EXTRACT_FILENAME_TEMPLATE.format(stem=stem), pages)]

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    @staticmethod
    def _open(source: Path) -> PdfReader:
        """Open ``source`` for reading, unlocking empty-password files."""
        try:
            reader = PdfReader(str(source), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            return reader
        except Exception as exc:  # noqa: BLE001 - pypdf raises many error types
            logger.exception("Could not open %s", source)
            raise PdfReadError(f"'{source.name}' could not be opened.\n{exc}") from exc

    @staticmethod
    def _write_pages(reader: PdfReader, pages: list[int], destination: Path) -> None:
        """Write the given 1-based ``pages`` of ``reader`` to ``destination``."""
        writer = PdfWriter()
        try:
            for page_number in pages:
                writer.add_page(reader.pages[page_number - 1])
            with destination.open("wb") as handle:
                writer.write(handle)
        except PermissionError as exc:
            raise PdfWriteError(
                f"'{destination.name}' is in use or read-only.\n"
                "Close it in any other application and try again."
            ) from exc
        except OSError as exc:
            raise PdfWriteError(f"'{destination.name}' could not be saved.\n{exc}") from exc
        except IndexError as exc:  # pragma: no cover - guarded by the validator
            raise PdfReadError("The document has fewer pages than expected.") from exc
        finally:
            writer.close()

    @staticmethod
    def _summary(mode: SplitMode, written: list[Path], pages_written: int) -> str:
        """Build the success message shown when the job finishes."""
        count = len(written)
        if mode is SplitMode.EXTRACT:
            return f"Extracted {pages_written} pages into '{written[0].name}'."
        noun = "file" if count == 1 else "files"
        return f"Created {count} {noun} containing {pages_written} pages in total."
