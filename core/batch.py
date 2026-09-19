"""Batch tool: convert plain-text and CSV files to PDF using reportlab.

Handles the "arbitrary text file" and "table data" cases that don't fit the
Word-document or PDF-specific tools: log files, ``.sql`` scripts, delimited
data, and so on. One file in, one file out - or every file collated into one
PDF, in the order supplied.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.constants import CSV_EXTENSIONS, DEFAULT_BATCH_COMBINED_NAME, TEXT_EXTENSIONS
from core.exceptions import PdfReadError, PdfWriteError, ValidationError
from core.models import OperationResult
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.utils import unique_path
from core.validator import validate_output_folder

logger = logging.getLogger(__name__)

_PAGE_MARGIN = 0.6 * inch
_MAX_CSV_COLUMN_CHARS = 40


class TextToPdfTool:
    """Converts plain-text and CSV files to PDF, singly or collated."""

    display_name = "Batch to PDF"

    def convert(
        self,
        sources: list[Path],
        output_folder: Path,
        combine: bool = False,
        combined_name: str | None = None,
        overwrite: bool = False,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Convert ``sources`` into ``output_folder``.

        Args:
            sources: Text or CSV files to convert, in the order they should
                appear when ``combine`` is set.
            output_folder: Destination folder, created when missing.
            combine: Write one collated PDF instead of one PDF per file.
            combined_name: Name of the collated file; defaults to
                :data:`core.constants.DEFAULT_BATCH_COMBINED_NAME`.
            overwrite: Replace existing files instead of adding a ``(2)``
                suffix to the name.
            progress: Optional progress sink.
            token: Optional cancellation token.

        Returns:
            An :class:`OperationResult` listing every PDF written. Files that
            could not be read are reported in
            :attr:`~core.models.OperationResult.warnings` rather than
            aborting the batch.

        Raises:
            ValidationError: No files were supplied.
            PdfWriteError: The output folder is not writable, or nothing
                could be converted at all.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        if not sources:
            raise ValidationError("Select at least one text or CSV file to convert.")
        validate_output_folder(output_folder)

        if combine:
            return self._convert_combined(
                sources, output_folder, combined_name, overwrite, progress, token
            )
        return self._convert_each(sources, output_folder, overwrite, progress, token)

    # ----------------------------------------------------------------- #
    # One output file per input
    # ----------------------------------------------------------------- #
    def _convert_each(
        self,
        sources: list[Path],
        output_folder: Path,
        overwrite: bool,
        progress: ProgressReporter,
        token: CancellationToken,
    ) -> OperationResult:
        """Convert every file into its own PDF."""
        total = len(sources)
        written: list[Path] = []
        warnings: list[str] = []

        for index, source in enumerate(sources, start=1):
            token.raise_if_cancelled()
            progress.update(index - 1, total, f"Converting {source.name}")
            destination = output_folder / f"{source.stem}.pdf"
            if not overwrite:
                destination = unique_path(destination)
            try:
                flowables = self._read_flowables(source)
                _write_pdf(destination, flowables)
            except PdfReadError as exc:
                logger.warning("Skipped %s: %s", source, exc)
                warnings.append(f"{source.name}: {exc}")
            else:
                written.append(destination)
            progress.update(index, total, f"Finished {source.name}")

        if not written:
            raise PdfWriteError(
                "None of the selected files could be converted.\n\n" + "\n\n".join(warnings[:5])
            )

        return OperationResult(
            output_paths=written,
            output_folder=output_folder,
            pages_written=0,
            message=self._summary(len(written), len(warnings)),
            warnings=warnings,
        )

    # ----------------------------------------------------------------- #
    # One collated output file
    # ----------------------------------------------------------------- #
    def _convert_combined(
        self,
        sources: list[Path],
        output_folder: Path,
        combined_name: str | None,
        overwrite: bool,
        progress: ProgressReporter,
        token: CancellationToken,
    ) -> OperationResult:
        """Collate every file into one PDF, each preceded by a heading."""
        name = combined_name or DEFAULT_BATCH_COMBINED_NAME
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        destination = output_folder / name
        if not overwrite:
            destination = unique_path(destination)

        total = len(sources) + 1
        warnings: list[str] = []
        flowables: list = []
        styles = getSampleStyleSheet()

        for index, source in enumerate(sources, start=1):
            token.raise_if_cancelled()
            progress.update(index - 1, total, f"Reading {source.name}")
            try:
                section = self._read_flowables(source)
            except PdfReadError as exc:
                logger.warning("Skipped %s: %s", source, exc)
                warnings.append(f"{source.name}: {exc}")
                progress.update(index, total, f"Skipped {source.name}")
                continue
            if flowables:
                flowables.append(PageBreak())
            flowables.append(Paragraph(xml_escape(source.name), styles["Heading2"]))
            flowables.append(Spacer(1, 12))
            flowables.extend(section)
            progress.update(index, total, f"Read {source.name}")

        if not flowables:
            raise PdfWriteError(
                "None of the selected files could be converted.\n\n" + "\n\n".join(warnings[:5])
            )

        progress.update(total - 1, total, f"Writing {destination.name}")
        _write_pdf(destination, flowables)
        progress.update(total, total, "Finished")

        count = len(sources) - len(warnings)
        return OperationResult(
            output_paths=[destination],
            output_folder=output_folder,
            pages_written=0,
            message=f"Combined {count} file{'s' if count != 1 else ''} into '{destination.name}'.",
            warnings=warnings,
        )

    # ----------------------------------------------------------------- #
    # Reading
    # ----------------------------------------------------------------- #
    def _read_flowables(self, source: Path) -> list:
        """Read ``source`` and return the reportlab flowables representing it.

        Raises:
            PdfReadError: The file is missing, empty, unreadable or not a
                supported type.
        """
        if not source.exists():
            raise PdfReadError(f"'{source.name}' no longer exists at:\n{source.parent}")
        if not source.is_file():
            raise PdfReadError(f"'{source.name}' is not a file.")

        suffix = source.suffix.lower()
        if suffix not in CSV_EXTENSIONS and suffix not in TEXT_EXTENSIONS:
            raise PdfReadError(f"'{source.name}' is not a supported text or CSV file.")

        try:
            stat = source.stat()
        except OSError as exc:
            raise PdfReadError(f"'{source.name}' could not be read.\n{exc}") from exc
        if stat.st_size == 0:
            raise PdfReadError(f"'{source.name}' is empty (0 bytes).")

        text = _read_text(source)
        if suffix in CSV_EXTENSIONS:
            return _csv_flowables(text)
        return _text_flowables(text)

    @staticmethod
    def _summary(written: int, failed: int) -> str:
        """Build the success message shown when a per-file batch finishes."""
        noun = "file" if written == 1 else "files"
        text = f"Converted {written} {noun} to PDF."
        if failed:
            text += f"\n{failed} file{'s' if failed != 1 else ''} could not be converted."
        return text


# --------------------------------------------------------------------------- #
# Text reading and encoding
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    """Read ``path`` as text, tolerating non-UTF-8 encodings."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PdfReadError(f"'{path.name}' could not be read.\n{exc}") from exc

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    encoding = _detect_encoding(raw)
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("latin-1", errors="replace")


def _detect_encoding(raw: bytes) -> str:
    """Best-effort encoding guess. ``chardet`` is optional; latin-1 never fails."""
    try:
        import chardet
    except ImportError:
        return "latin-1"
    try:
        result = chardet.detect(raw)
        return result.get("encoding") or "latin-1"
    except Exception:  # noqa: BLE001 - detection is a convenience, never fatal
        return "latin-1"


# --------------------------------------------------------------------------- #
# Flowable construction
# --------------------------------------------------------------------------- #
def _text_flowables(text: str) -> list:
    """Render plain text (logs, .sql, .md) in a monospace block.

    ``Preformatted`` treats its content as literal text rather than markup,
    so unlike :class:`~reportlab.platypus.Paragraph` it needs no XML escaping.
    """
    styles = getSampleStyleSheet()
    mono = ParagraphStyle(
        "Mono", parent=styles["Code"], fontName="Courier", fontSize=8, leading=10
    )
    return [Preformatted(text, mono)]


def _csv_flowables(text: str) -> list:
    """Render CSV as a table, with the first row styled as a header."""
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        raise PdfReadError("The CSV file has no rows.")

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8, leading=10)
    header_style = ParagraphStyle(
        "CellHeader", parent=cell_style, fontName="Helvetica-Bold", textColor=colors.white
    )

    width = len(rows[0])
    data = []
    for row_index, row in enumerate(rows):
        cells = (list(row) + [""] * width)[:width]
        style = header_style if row_index == 0 else cell_style
        data.append([Paragraph(_format_cell(cell), style) for cell in cells])

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [table]


def _format_cell(text: str) -> str:
    """Truncate and XML-escape one CSV cell so it is safe inside a Paragraph."""
    text = text.replace("\n", " ")
    if len(text) > _MAX_CSV_COLUMN_CHARS:
        text = text[: _MAX_CSV_COLUMN_CHARS - 1] + "…"
    return xml_escape(text)


def _write_pdf(destination: Path, flowables: list) -> None:
    """Write ``flowables`` to ``destination`` with a page-number footer."""
    try:
        document = SimpleDocTemplate(
            str(destination),
            pagesize=A4,
            leftMargin=_PAGE_MARGIN,
            rightMargin=_PAGE_MARGIN,
            topMargin=_PAGE_MARGIN,
            bottomMargin=_PAGE_MARGIN,
        )
        document.build(flowables, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    except PermissionError as exc:
        raise PdfWriteError(
            f"'{destination.name}' is in use or read-only.\n"
            "Close it in any other application and try again."
        ) from exc
    except OSError as exc:
        raise PdfWriteError(f"'{destination.name}' could not be saved.\n{exc}") from exc


def _draw_footer(canvas, document) -> None:
    """Draw a small centred page-number footer on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawCentredString(document.pagesize[0] / 2, 0.35 * inch, f"Page {document.page}")
    canvas.restoreState()
