"""Convert PDFs and images (PNG/JPEG) to Markdown with PyMuPDF4LLM.

This is the toolkit's LLM-oriented extraction layer, implementing the
"PDF -> Markdown" item from the consolidation plan. PyMuPDF4LLM reads the
pages of a document and emits Markdown with headings, lists and tables
preserved; scanned pages and standalone images are OCR'd automatically when
the optional Tesseract engine is installed.

Anything that is not a PDF is treated as a raster image: images without any
recognised text still produce a useful Markdown file that references the
picture itself, so the conversion never silently yields an empty document.

Like every other core tool, this module imports no UI code and reports
progress through :class:`core.progress.ProgressReporter`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.constants import MARKDOWN_EXTENSION
from core.exceptions import PdfReadError, PdfToolkitError, PdfWriteError, ValidationError
from core.models import MarkdownFileInfo, OperationResult
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.utils import unique_path
from core.validator import validate_output_folder

logger = logging.getLogger(__name__)


def _import_pymupdf4llm() -> Any:
    """Import :mod:`pymupdf4llm` lazily so the app starts without it.

    Returns:
        The imported module.

    Raises:
        PdfToolkitError: The dependency is not installed.
    """
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise PdfToolkitError(
            "PyMuPDF4LLM is not installed on this computer.\n\n"
            "Install it with:\n"
            "    python -m pip install pymupdf4llm"
        ) from exc
    return pymupdf4llm


class MarkdownConvertTool:
    """Converts PDFs and PNG/JPEG images into Markdown files, one per input."""

    display_name = "Convert to Markdown"

    def convert(
        self,
        files: list[MarkdownFileInfo],
        output_folder: Path,
        mirror_structure: bool = True,
        overwrite: bool = False,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Convert ``files`` into ``output_folder``.

        Args:
            files: Sources to convert, in the order they should be processed.
            output_folder: Destination folder, created when missing.
            mirror_structure: Recreate each file's sub-folder path under the
                output folder.
            overwrite: Replace existing files instead of adding a ``(2)``
                suffix to the name.
            progress: Optional progress sink.
            token: Optional cancellation token.

        Returns:
            An :class:`OperationResult` listing every Markdown file written.
            Sources that could not be converted are reported in
            :attr:`~core.models.OperationResult.warnings` rather than aborting
            the batch.

        Raises:
            ValidationError: No files were supplied.
            PdfToolkitError: PyMuPDF4LLM is not installed.
            PdfWriteError: The output folder is not writable, or no source
                could be converted at all.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        if not files:
            raise ValidationError("Select at least one PDF or image to convert.")
        validate_output_folder(output_folder)

        pymupdf4llm = _import_pymupdf4llm()

        total = len(files)
        written: list[Path] = []
        warnings: list[str] = []
        pages_written = 0

        for index, info in enumerate(files, start=1):
            token.raise_if_cancelled()
            progress.update(index - 1, total, f"Converting {info.name}")
            destination = self._destination(info, output_folder, mirror_structure)
            try:
                validate_output_folder(destination.parent)
                if not overwrite:
                    destination = unique_path(destination)
                markdown = self._markdown_for(pymupdf4llm, info, destination, token)
                _write_text(destination, markdown)
            except PdfToolkitError as exc:
                logger.warning("Skipped %s: %s", info.path, exc)
                warnings.append(f"{info.name}: {exc}")
            else:
                written.append(destination)
                pages_written += max(info.page_count, 1)
            progress.update(index, total, f"Finished {info.name}")

        if not written:
            raise PdfWriteError(
                "None of the selected files could be converted.\n\n"
                + "\n\n".join(warnings[:5])
            )

        return OperationResult(
            output_paths=written,
            output_folder=output_folder,
            pages_written=pages_written,
            message=self._summary(len(written), len(warnings)),
            warnings=warnings,
        )


    # ----------------------------------------------------------------- #
    # Conversion
    # ----------------------------------------------------------------- #
    @staticmethod
    def _markdown_for(
        pymupdf4llm: Any,
        info: MarkdownFileInfo,
        destination: Path,
        token: CancellationToken,
    ) -> str:
        """Return the Markdown text for one source file.

        Raises:
            PdfReadError: The file could not be converted, or a PDF contains
                no extractable text.
        """
        token.raise_if_cancelled()
        try:
            # Always pass a path string: PyMuPDF4LLM opens (and closes) the
            # document itself, which keeps its page handling completely under
            # its own control.
            markdown = pymupdf4llm.to_markdown(str(info.path))
        except ImportError as exc:
            # OCR was requested but its engine is not installed; retry
            # without OCR so digital text is still extracted.
            logger.info("Retrying %s without OCR: %s", info.name, exc)
            try:
                markdown = pymupdf4llm.to_markdown(str(info.path), use_ocr=False)
            except Exception as retry_exc:  # noqa: BLE001 - library errors vary
                raise PdfReadError(
                    f"'{info.name}' could not be converted.\n{retry_exc}"
                ) from retry_exc
        except Exception as exc:  # noqa: BLE001 - library errors vary
            logger.exception("Could not convert %s", info.path)
            raise PdfReadError(f"'{info.name}' could not be converted.\n{exc}") from exc

        markdown = markdown.strip()
        if markdown:
            return f"{markdown}\n"
        if info.kind == "PDF":
            raise PdfReadError(
                f"'{info.name}' contains no extractable text.\n"
                "Scanned documents need the optional Tesseract OCR engine."
            )
        return MarkdownConvertTool._image_fallback(info, destination)

    @staticmethod
    def _image_fallback(info: MarkdownFileInfo, destination: Path) -> str:
        """Markdown referencing the picture itself when no text was recognised."""
        try:
            reference = os.path.relpath(info.path, destination.parent)
        except ValueError:  # pragma: no cover - source and output on drives
            reference = str(info.path)
        reference = Path(reference).as_posix()
        return (
            f"# {info.stem}\n\n"
            f"![{info.stem}]({reference})\n\n"
            "_No text was recognised in this image. Install the Tesseract OCR "
            "engine to extract text from pictures automatically._\n"
        )

    @staticmethod
    def _destination(info: MarkdownFileInfo, output_folder: Path, mirror: bool) -> Path:
        """Work out where one source's Markdown file belongs."""
        name = info.path.stem + MARKDOWN_EXTENSION
        if mirror and info.relative_path is not None:
            return output_folder / info.relative_path.parent / name
        return output_folder / name

    @staticmethod
    def _summary(written: int, failed: int) -> str:
        """Build the message shown when a per-file batch finishes."""
        noun = "file" if written == 1 else "files"
        message = f"Converted {written} {noun} to Markdown."
        if failed:
            message += f" {failed} could not be converted."
        return message


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _write_text(destination: Path, text: str) -> None:
    """Write ``text`` as UTF-8, translating OS errors to friendly ones."""
    try:
        destination.write_text(text, encoding="utf-8")
    except PermissionError as exc:
        raise PdfWriteError(
            f"'{destination.name}' is in use or read-only.\n"
            "Close it in any other application and try again."
        ) from exc
    except OSError as exc:
        raise PdfWriteError(f"'{destination.name}' could not be saved.\n{exc}") from exc
