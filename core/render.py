"""PDF to Image tool: render PDF pages to PNG or JPEG using PyMuPDF.

This is the app's first PyMuPDF-backed tool. Existing tools (merge, split,
thumbnails) stay on pypdf/pypdfium2 - PyMuPDF is being introduced here, and
for future rendering/extraction work, rather than migrating what already
works.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf as fitz

from core.constants import (
    IMAGE_FILENAME_TEMPLATE,
    IMAGE_FORMATS,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
)
from core.exceptions import PdfReadError, PdfWriteError, ValidationError
from core.models import OperationResult
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.utils import unique_path
from core.validator import inspect_pdf, validate_output_folder

logger = logging.getLogger(__name__)


class PdfToImageTool:
    """Renders each page of a PDF to a raster image."""

    display_name = "PDF to Image"

    def render(
        self,
        source: Path,
        output_folder: Path,
        image_format: str = "png",
        dpi: int = 150,
        pages: list[int] | None = None,
        overwrite: bool = False,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Render pages of ``source`` into ``output_folder``.

        Args:
            source: PDF to render.
            output_folder: Destination folder, created when missing.
            image_format: ``"png"`` or ``"jpg"``.
            dpi: Resolution to render at, bounded to a sane range.
            pages: 1-based page numbers to render; every page when omitted.
            overwrite: Replace existing files instead of adding a ``(2)``
                suffix to the name.
            progress: Optional progress sink.
            token: Optional cancellation token.

        Returns:
            An :class:`OperationResult` listing every image written.

        Raises:
            ValidationError: ``image_format``, ``dpi`` or ``pages`` is invalid.
            PdfReadError: ``source`` cannot be read.
            PdfWriteError: ``output_folder`` is not writable.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        image_format = image_format.lower().lstrip(".")
        if image_format not in IMAGE_FORMATS:
            raise ValidationError(
                f"'{image_format}' is not a supported image format. "
                f"Use one of: {', '.join(IMAGE_FORMATS)}."
            )
        if not (MIN_RENDER_DPI <= dpi <= MAX_RENDER_DPI):
            raise ValidationError(
                f"Resolution must be between {MIN_RENDER_DPI} and {MAX_RENDER_DPI} DPI."
            )

        info = inspect_pdf(source)
        validate_output_folder(output_folder)

        page_numbers = pages if pages else list(range(1, info.page_count + 1))
        out_of_range = [p for p in page_numbers if p < 1 or p > info.page_count]
        if out_of_range:
            raise ValidationError(
                f"These pages do not exist in a {info.page_count}-page document: "
                f"{', '.join(str(p) for p in out_of_range)}"
            )

        logger.info(
            "Rendering %s (%d page(s)) to %s at %d DPI", source, len(page_numbers), image_format, dpi
        )

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        written: list[Path] = []
        total = len(page_numbers)

        document = self._open(source)
        try:
            for index, page_number in enumerate(page_numbers, start=1):
                token.raise_if_cancelled()
                progress.update(index - 1, total, f"Rendering page {page_number}")
                filename = IMAGE_FILENAME_TEMPLATE.format(
                    stem=info.stem, index=page_number, ext=image_format
                )
                destination = output_folder / filename
                if not overwrite:
                    destination = unique_path(destination)
                self._render_page(document, page_number, matrix, destination)
                written.append(destination)
                progress.update(index, total, f"Rendered {destination.name}")
        finally:
            document.close()

        return OperationResult(
            output_paths=written,
            output_folder=output_folder,
            pages_written=len(written),
            message=self._summary(written),
        )

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    @staticmethod
    def _open(source: Path) -> fitz.Document:
        """Open ``source`` with PyMuPDF, unlocking empty-password files."""
        try:
            document = fitz.open(str(source))
            if document.is_encrypted and not document.authenticate(""):
                document.close()
                raise PdfReadError(
                    f"'{source.name}' is password protected and cannot be processed.\n"
                    "Remove the password and try again."
                )
            return document
        except PdfReadError:
            raise
        except Exception as exc:  # noqa: BLE001 - PyMuPDF raises many error types
            logger.exception("Could not open %s", source)
            raise PdfReadError(f"'{source.name}' could not be opened.\n{exc}") from exc

    @staticmethod
    def _render_page(
        document: fitz.Document, page_number: int, matrix: fitz.Matrix, destination: Path
    ) -> None:
        """Render one 1-based ``page_number`` of ``document`` to ``destination``."""
        try:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=matrix)
            pixmap.save(str(destination))
        except PermissionError as exc:
            raise PdfWriteError(
                f"'{destination.name}' is in use or read-only.\n"
                "Close it in any other application and try again."
            ) from exc
        except OSError as exc:
            raise PdfWriteError(f"'{destination.name}' could not be saved.\n{exc}") from exc

    @staticmethod
    def _summary(written: list[Path]) -> str:
        """Build the success message shown when the job finishes."""
        count = len(written)
        noun = "image" if count == 1 else "images"
        return f"Created {count} {noun}."
