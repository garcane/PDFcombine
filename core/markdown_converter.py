"""Convert PDFs and images (PNG/JPEG) to Markdown with PyMuPDF4LLM.

This is the toolkit's LLM-oriented extraction layer, implementing the
"PDF -> Markdown" item from the consolidation plan. PyMuPDF4LLM reads the
pages of a document and emits Markdown with headings, lists and tables
preserved; scanned pages and standalone images are OCR'd automatically when
the optional Tesseract engine is installed.

Anything that is not a PDF is treated as a raster image. Images coming from
the same folder are collated into a single combined Markdown file named after
that folder (mirroring how one PDF becomes one Markdown file); an image
without any recognised text still contributes a reference to the picture
itself, so the conversion never silently yields an empty document.

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
    """Converts PDFs and PNG/JPEG images into Markdown files.

    A PDF becomes one Markdown file; images coming from the same folder are
    combined into a single Markdown file named after that folder, so a
    folder full of scanned pages converts to one document.
    """

    display_name = "Convert to Markdown"

    @staticmethod
    def plan(files: list[MarkdownFileInfo]) -> list[list[MarkdownFileInfo]]:
        """Split ``files`` into ordered conversion groups.

        Each PDF forms its own group. Images sharing a parent folder form
        one group so they collapse into a single combined Markdown file;
        images from different folders stay in separate groups and a lone
        image remains a group of one.

        Args:
            files: The sources, in display order.

        Returns:
            The groups, in the order their first member appears.
        """
        groups: list[list[MarkdownFileInfo]] = []
        group_of: dict[Path, int] = {}
        for info in files:
            if info.kind == "PDF":
                groups.append([info])
                continue
            folder = info.path.parent
            position = group_of.get(folder)
            if position is None:
                group_of[folder] = len(groups)
                groups.append([info])
            else:
                groups[position].append(info)
        return groups

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
            files: Sources to convert. PDFs keep one output file each;
                images sharing a parent folder are combined into one file.
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

        groups = self.plan(files)
        total = len(files)
        done = 0
        written: list[Path] = []
        warnings: list[str] = []
        pages_written = 0

        for group in groups:
            token.raise_if_cancelled()
            label = self._label(group)
            progress.update(done, total, f"Converting {label}")
            destination = self._destination_for(group, output_folder, mirror_structure)
            try:
                validate_output_folder(destination.parent)
                if not overwrite:
                    destination = unique_path(destination)
                if len(group) == 1:
                    markdown = self._markdown_for(pymupdf4llm, group[0], destination, token)
                else:
                    markdown = self._combined_markdown(
                        pymupdf4llm, group, destination, progress, done, total, warnings, token
                    )
                _write_text(destination, markdown)
            except PdfToolkitError as exc:
                logger.warning("Skipped %s: %s", label, exc)
                warnings.append(f"{label}: {exc}")
            else:
                written.append(destination)
                pages_written += sum(max(info.page_count, 1) for info in group)
            done += len(group)
            progress.update(done, total, f"Finished {label}")

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
    @classmethod
    def _markdown_for(
        cls,
        pymupdf4llm: Any,
        info: MarkdownFileInfo,
        destination: Path,
        token: CancellationToken,
    ) -> str:
        """Return the Markdown text for one standalone source file.

        Raises:
            PdfReadError: The file could not be converted, or a PDF contains
                no extractable text.
        """
        token.raise_if_cancelled()
        markdown = cls._extract_text(pymupdf4llm, info)
        if markdown:
            return f"{markdown}\n"
        if info.kind == "PDF":
            raise PdfReadError(
                f"'{info.name}' contains no extractable text.\n"
                "Scanned documents need the optional Tesseract OCR engine."
            )
        return cls._image_fallback(info, destination)

    @classmethod
    def _combined_markdown(
        cls,
        pymupdf4llm: Any,
        members: list[MarkdownFileInfo],
        destination: Path,
        progress: ProgressReporter,
        start: int,
        total: int,
        warnings: list[str],
        token: CancellationToken,
    ) -> str:
        """Collate the text of every image in ``members`` into one document.

        Images whose text cannot be recognised contribute their picture
        reference instead, and unreadable images are reported in ``warnings``
        while the rest of the group continues.

        Raises:
            PdfReadError: No image in the group could be converted.
        """
        parts: list[str] = []
        for offset, info in enumerate(members):
            token.raise_if_cancelled()
            progress.update(start + offset, total, f"Converting {info.name}")
            try:
                text = cls._extract_text(pymupdf4llm, info)
            except PdfReadError as exc:
                logger.warning("Skipped %s: %s", info.path, exc)
                warnings.append(f"{info.name}: {exc}")
                continue
            parts.append(text if text else cls._inline_reference(info, destination))

        if not parts:
            raise PdfReadError(
                f"None of the {len(members)} images could be converted."
            )
        return "\n\n".join(parts) + "\n"

    @staticmethod
    def _extract_text(pymupdf4llm: Any, info: MarkdownFileInfo) -> str:
        """Return the Markdown text recognised in one source, possibly empty.

        Raises:
            PdfReadError: The file could not be converted at all.
        """
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
        return markdown.strip()

    @classmethod
    def _image_fallback(cls, info: MarkdownFileInfo, destination: Path) -> str:
        """Markdown referencing the picture itself when no text was recognised."""
        return (
            f"# {info.stem}\n\n"
            f"{cls._inline_reference(info, destination)}\n\n"
            "_No text was recognised in this image. Install the Tesseract OCR "
            "engine to extract text from pictures automatically._\n"
        )

    @staticmethod
    def _inline_reference(info: MarkdownFileInfo, destination: Path) -> str:
        """A bare ``![name](relative/path)`` line pointing at the picture."""
        reference = MarkdownConvertTool._image_reference(info, destination)
        return f"![{info.stem}]({reference})"

    @staticmethod
    def _image_reference(info: MarkdownFileInfo, destination: Path) -> str:
        """Relative (POSIX) path from ``destination``'s folder to the image."""
        try:
            reference = os.path.relpath(info.path, destination.parent)
        except ValueError:  # pragma: no cover - source and output on drives
            reference = str(info.path)
        return Path(reference).as_posix()

    @staticmethod
    def _destination(info: MarkdownFileInfo, output_folder: Path, mirror: bool) -> Path:
        """Work out where one source's Markdown file belongs."""
        name = info.path.stem + MARKDOWN_EXTENSION
        if mirror and info.relative_path is not None:
            return output_folder / info.relative_path.parent / name
        return output_folder / name

    @classmethod
    def _destination_for(
        cls, group: list[MarkdownFileInfo], output_folder: Path, mirror: bool
    ) -> Path:
        """Work out where one group's Markdown file belongs.

        A lone source keeps its own name (honouring the mirror switch); a
        combined image group is named after its folder and honours the mirror
        switch using that folder's path relative to the selected input folder.
        """
        if len(group) == 1:
            return cls._destination(group[0], output_folder, mirror)
        folder_name = group[0].path.parent.name or "Images"
        relative_folder = (
            group[0].relative_path.parent
            if mirror and group[0].relative_path is not None
            else Path()
        )
        return output_folder / relative_folder / f"{folder_name}{MARKDOWN_EXTENSION}"

    @staticmethod
    def _label(group: list[MarkdownFileInfo]) -> str:
        """Human-readable identity of a group for progress and warnings."""
        if len(group) == 1:
            return group[0].name
        folder_name = group[0].path.parent.name or "Images"
        return f"{folder_name}/ ({len(group)} images)"

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
