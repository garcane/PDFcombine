"""Render the document block model to a PDF.

This is the toolkit's built-in Word-to-PDF engine: it needs no Microsoft Word
and no LibreOffice, so conversion works on any machine. It reproduces
structure - headings, paragraphs, lists, quotes, tables and inline bold/italic
- rather than Word's exact page layout. When pixel-faithful output matters and
Word or LibreOffice is installed, :mod:`core.docx_converter` uses those
instead; see :class:`core.docx_converter.PdfEngine`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.docx_reader import Block, DocxDocument, Span
from core.exceptions import PdfWriteError

logger = logging.getLogger(__name__)

#: Point sizes for heading levels 1-6.
_HEADING_SIZES = (20, 16.5, 14, 12.5, 11.5, 11)


def render_documents_to_pdf(
    documents: list[DocxDocument],
    output_path: Path,
    include_titles: bool = False,
    page_break_between: bool = True,
    page_numbers: bool = True,
) -> None:
    """Write ``documents`` to a single PDF at ``output_path``.

    Args:
        documents: Parsed documents, in output order.
        output_path: Destination file; its folder must already exist.
        include_titles: Start each document with its name as a heading.
        page_break_between: Begin every document after the first on a new page.
        page_numbers: Draw "Page n of m" at the foot of each page.

    Raises:
        PdfWriteError: reportlab is unavailable, or the file cannot be written.
    """
    platypus, styles_module, units, colors, pagesizes = _import_reportlab()
    styles = _build_styles(styles_module, units)

    story: list[object] = []
    for index, document in enumerate(documents):
        if index and page_break_between:
            story.append(platypus.PageBreak())
        if include_titles:
            story.append(platypus.Paragraph(_markup([Span(document.title, bold=True)]), styles["h1"]))
        story.extend(_flowables(document, platypus, styles, units, colors))

    if not story:
        story.append(platypus.Paragraph("(This document is empty.)", styles["body"]))

    title = documents[0].title if len(documents) == 1 else output_path.stem
    doc = platypus.SimpleDocTemplate(
        str(output_path),
        pagesize=pagesizes.A4,
        leftMargin=2.0 * units.cm,
        rightMargin=2.0 * units.cm,
        topMargin=2.0 * units.cm,
        bottomMargin=2.0 * units.cm,
        title=title,
        author="PDF Toolkit",
    )

    footer = _page_footer(colors) if page_numbers else None
    try:
        if footer is None:
            doc.build(story)
        else:
            doc.build(story, onFirstPage=footer, onLaterPages=footer)
    except PermissionError as exc:
        raise PdfWriteError(
            f"'{output_path.name}' is in use or read-only.\n"
            "Close it in any other application and try again."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - reportlab raises many types
        logger.exception("PDF rendering failed for %s", output_path)
        raise PdfWriteError(f"'{output_path.name}' could not be created.\n{exc}") from exc


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def _import_reportlab():  # noqa: ANN202 - returns a bundle of modules
    """Import reportlab, raising a friendly error when it is missing."""
    try:
        from reportlab import platypus
        from reportlab.lib import colors, pagesizes, styles as styles_module, units
    except ImportError as exc:
        raise PdfWriteError(
            "PDF output needs the reportlab package.\n"
            "Run:  python -m pip install reportlab"
        ) from exc
    return platypus, styles_module, units, colors, pagesizes


def _build_styles(styles_module, units):  # noqa: ANN001, ANN202
    """Create the paragraph styles used by the renderer."""
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle

    base = styles_module.getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}

    styles["body"] = ParagraphStyle(
        "ToolkitBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        spaceBefore=0,
        spaceAfter=8,
        alignment=TA_JUSTIFY,
    )
    for level, size in enumerate(_HEADING_SIZES, start=1):
        styles[f"h{level}"] = ParagraphStyle(
            f"ToolkitHeading{level}",
            parent=styles["body"],
            fontName="Helvetica-Bold",
            fontSize=size,
            leading=size * 1.3,
            spaceBefore=14 if level > 1 else 6,
            spaceAfter=6,
            alignment=0,
        )
    styles["quote"] = ParagraphStyle(
        "ToolkitQuote",
        parent=styles["body"],
        leftIndent=1.0 * units.cm,
        rightIndent=0.5 * units.cm,
        fontName="Helvetica-Oblique",
        textColor="#444444",
        borderPadding=0,
    )
    styles["list"] = ParagraphStyle(
        "ToolkitList",
        parent=styles["body"],
        alignment=0,
        spaceAfter=4,
        bulletIndent=0,
    )
    styles["cell"] = ParagraphStyle(
        "ToolkitCell",
        parent=styles["body"],
        fontSize=9.5,
        leading=12,
        spaceAfter=0,
        alignment=0,
    )
    styles["cell_head"] = ParagraphStyle(
        "ToolkitCellHead",
        parent=styles["cell"],
        fontName="Helvetica-Bold",
    )
    return styles


def _page_footer(colors):  # noqa: ANN001, ANN202
    """Return an ``onPage`` callback drawing a centred page number."""

    def draw(canvas, doc) -> None:  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#808080"))
        canvas.drawCentredString(
            doc.pagesize[0] / 2.0, 1.1 * 28.35, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()

    return draw


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #
def _flowables(document: DocxDocument, platypus, styles, units, colors) -> list[object]:  # noqa: ANN001
    """Convert one document's blocks into reportlab flowables."""
    flowables: list[object] = []
    numbering: dict[int, int] = {}

    for block in document.blocks:
        if block.kind == "number":
            numbering[block.level] = numbering.get(block.level, 0) + 1
        else:
            numbering.clear()

        if block.kind == "table":
            table = _table(block, platypus, styles, units, colors)
            if table is not None:
                flowables.extend([table, platypus.Spacer(1, 10)])
            continue

        markup = _markup(block.spans)
        if not markup.strip():
            continue

        if block.kind == "heading":
            level = max(1, min(block.level, len(_HEADING_SIZES)))
            flowables.append(platypus.Paragraph(markup, styles[f"h{level}"]))
        elif block.kind in {"bullet", "number"}:
            style = _indented(styles["list"], block.level, units)
            bullet = "•" if block.kind == "bullet" else f"{numbering.get(block.level, 1)}."
            flowables.append(platypus.Paragraph(markup, style, bulletText=bullet))
        elif block.kind == "quote":
            flowables.append(platypus.Paragraph(markup, styles["quote"]))
        else:
            flowables.append(platypus.Paragraph(markup, styles["body"]))
    return flowables


def _indented(style, level: int, units):  # noqa: ANN001, ANN202
    """Clone ``style`` with a left indent matching the list nesting ``level``."""
    from reportlab.lib.styles import ParagraphStyle

    step = 0.7 * units.cm
    return ParagraphStyle(
        f"{style.name}L{level}",
        parent=style,
        leftIndent=step * (level + 1),
        bulletIndent=step * level,
    )


def _table(block: Block, platypus, styles, units, colors):  # noqa: ANN001, ANN202
    """Build a reportlab table from a ``table`` block."""
    if not block.rows:
        return None
    width = max(len(row) for row in block.rows)
    available = 21.0 * units.cm - 4.0 * units.cm  # A4 width minus margins
    col_width = available / width

    data = []
    for index, row in enumerate(block.rows):
        style = styles["cell_head"] if index == 0 else styles["cell"]
        cells = [platypus.Paragraph(_markup(cell) or " ", style) for cell in row]
        cells += [platypus.Paragraph(" ", style)] * (width - len(cells))
        data.append(cells)

    table = platypus.Table(data, colWidths=[col_width] * width, repeatRows=1)
    table.setStyle(
        platypus.TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9c0c8")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


# --------------------------------------------------------------------------- #
# Inline formatting
# --------------------------------------------------------------------------- #
def _markup(spans: list[Span]) -> str:
    """Render spans using reportlab's inline markup, escaping the text."""
    parts: list[str] = []
    for span in spans:
        text = _escape(span.text)
        if span.bold:
            text = f"<b>{text}</b>"
        if span.italic:
            text = f"<i>{text}</i>"
        parts.append(text)
    return "".join(parts)


def _escape(text: str) -> str:
    """Escape the characters reportlab would otherwise treat as markup."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\t", "    ")
    )
