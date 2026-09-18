"""Read a Word document into a small, format-neutral block model.

Both output formats are generated from this model: :mod:`core.markdown_writer`
turns it into Markdown, :mod:`core.pdf_renderer` draws it as a PDF. Keeping the
parse in one place means the two exports never drift apart.

The model deliberately covers the structure that survives a format change -
headings, paragraphs, lists, quotes and tables, with bold/italic runs - rather
than trying to reproduce Word's full formatting model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

from core.exceptions import PdfReadError

logger = logging.getLogger(__name__)

BlockKind = Literal["heading", "paragraph", "bullet", "number", "quote", "table"]

#: Word style names that map onto a block kind. Anything unrecognised becomes
#: a plain paragraph, so unusual templates degrade gracefully.
_QUOTE_STYLES = frozenset({"quote", "intense quote", "block text"})


@dataclass(slots=True)
class Span:
    """A run of text sharing the same inline formatting."""

    text: str
    bold: bool = False
    italic: bool = False


@dataclass(slots=True)
class Block:
    """One structural element of a document."""

    kind: BlockKind
    spans: list[Span] = field(default_factory=list)
    #: Heading level (1-9) or list nesting depth (0 = top level).
    level: int = 0
    #: Populated for ``table`` blocks: rows of cells, each cell a list of spans.
    rows: list[list[list[Span]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The block's plain text, with formatting removed."""
        return "".join(span.text for span in self.spans)

    @property
    def is_empty(self) -> bool:
        """``True`` when the block carries no visible content."""
        if self.kind == "table":
            return not self.rows
        return not self.text.strip()


@dataclass(slots=True)
class DocxDocument:
    """A parsed Word document."""

    path: Path
    blocks: list[Block] = field(default_factory=list)

    @property
    def title(self) -> str:
        """Best-effort document title: the first heading, else the file name."""
        for block in self.blocks:
            if block.kind == "heading" and block.text.strip():
                return block.text.strip()
        return self.path.stem

    @property
    def paragraph_count(self) -> int:
        """Number of non-table blocks holding text."""
        return sum(1 for block in self.blocks if block.kind != "table")

    @property
    def table_count(self) -> int:
        """Number of tables in the document."""
        return sum(1 for block in self.blocks if block.kind == "table")


def read_docx(path: Path) -> DocxDocument:
    """Parse ``path`` into a :class:`DocxDocument`.

    Args:
        path: The ``.docx`` file to read.

    Returns:
        The parsed document, with body content in reading order.

    Raises:
        PdfReadError: The file is missing, not a Word document, password
            protected, or damaged. (The toolkit uses one read-error type for
            every format so the UI can present them identically.)
    """
    document = _open(path)
    blocks: list[Block] = []
    for item in _iter_body(document):
        block = _paragraph_block(item) if item[0] == "p" else _table_block(item[1])
        if block is not None and not block.is_empty:
            blocks.append(block)
    return DocxDocument(path=path, blocks=blocks)


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
def _open(path: Path):  # noqa: ANN202 - python-docx has no public type alias
    """Open ``path`` with python-docx, translating failures to friendly errors."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise PdfReadError(
            "Word support is not installed.\n"
            "Run:  python -m pip install python-docx"
        ) from exc

    if not path.exists():
        raise PdfReadError(f"'{path.name}' no longer exists at:\n{path.parent}")
    if path.suffix.lower() != ".docx":
        raise PdfReadError(
            f"'{path.name}' is not a .docx file.\n"
            "Older .doc documents must be saved as .docx in Word first."
        )
    try:
        return Document(str(path))
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types
        logger.exception("Could not open %s", path)
        message = str(exc).lower()
        if "encrypted" in message or "password" in message:
            raise PdfReadError(
                f"'{path.name}' is password protected and cannot be converted."
            ) from exc
        raise PdfReadError(
            f"'{path.name}' could not be opened - the file may be damaged "
            f"or may not be a real Word document.\n{exc}"
        ) from exc


def _iter_body(document) -> Iterator[tuple[str, object]]:  # noqa: ANN001
    """Yield ``("p", Paragraph)`` / ``("tbl", Table)`` pairs in reading order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(child, document)
        elif tag == "tbl":
            yield "tbl", Table(child, document)


# --------------------------------------------------------------------------- #
# Paragraphs
# --------------------------------------------------------------------------- #
def _paragraph_block(item: tuple[str, object]) -> Block | None:
    """Convert one Word paragraph into a :class:`Block`."""
    paragraph = item[1]
    spans = _spans(paragraph)
    if not spans:
        return None

    style_name = _style_name(paragraph)
    kind, level = _classify(paragraph, style_name)
    return Block(kind=kind, spans=spans, level=level)


def _style_name(paragraph) -> str:  # noqa: ANN001
    """Return the paragraph's style name in lower case, or ``""``."""
    try:
        return (paragraph.style.name or "").strip().lower()
    except Exception:  # noqa: BLE001 - broken style references are common
        return ""


def _classify(paragraph, style_name: str) -> tuple[BlockKind, int]:  # noqa: ANN001
    """Work out the block kind and level from the paragraph's style."""
    if style_name.startswith("heading"):
        return "heading", _trailing_number(style_name, default=1, maximum=6)
    if style_name in {"title"}:
        return "heading", 1
    if style_name in {"subtitle"}:
        return "heading", 2
    if style_name.startswith("list bullet"):
        return "bullet", _trailing_number(style_name, default=1, maximum=6) - 1
    if style_name.startswith("list number"):
        return "number", _trailing_number(style_name, default=1, maximum=6) - 1
    if style_name in _QUOTE_STYLES:
        return "quote", 0
    if _has_numbering(paragraph):
        # A list built with direct numbering rather than a list style.
        return "bullet", _indent_level(paragraph)
    return "paragraph", 0


def _trailing_number(style_name: str, default: int, maximum: int) -> int:
    """Extract the trailing digit of e.g. ``"heading 2"`` / ``"list bullet 3"``."""
    tail = style_name.rsplit(" ", 1)[-1]
    if tail.isdigit():
        return max(1, min(int(tail), maximum))
    return default


def _has_numbering(paragraph) -> bool:  # noqa: ANN001
    """``True`` when the paragraph carries direct list numbering."""
    try:
        return paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    except Exception:  # noqa: BLE001 - defensive against odd documents
        return False


def _indent_level(paragraph) -> int:  # noqa: ANN001
    """Approximate a list nesting depth from the paragraph indent."""
    try:
        indent = paragraph.paragraph_format.left_indent
        if indent is None:
            return 0
        # Word's default list step is 0.25", i.e. 228600 EMU.
        return max(0, min(int(indent.emu // 228600), 5))
    except Exception:  # noqa: BLE001 - defensive
        return 0


def _spans(paragraph) -> list[Span]:  # noqa: ANN001
    """Collect the paragraph's runs, merging neighbours with equal formatting."""
    spans: list[Span] = []
    for run in paragraph.runs:
        text = run.text
        if not text:
            continue
        bold = bool(run.bold)
        italic = bool(run.italic)
        if spans and spans[-1].bold == bold and spans[-1].italic == italic:
            spans[-1].text += text
        else:
            spans.append(Span(text=text, bold=bold, italic=italic))

    if not spans:
        # Hyperlinks and fields live outside ``runs``; fall back to the text.
        text = paragraph.text
        if text.strip():
            spans.append(Span(text=text))
    return spans


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def _table_block(table) -> Block | None:  # noqa: ANN001
    """Convert a Word table into a ``table`` block."""
    rows: list[list[list[Span]]] = []
    try:
        for row in table.rows:
            cells: list[list[Span]] = []
            for cell in row.cells:
                cell_spans: list[Span] = []
                for paragraph in cell.paragraphs:
                    part = _spans(paragraph)
                    if not part:
                        continue
                    if cell_spans:
                        cell_spans.append(Span(text=" "))
                    cell_spans.extend(part)
                cells.append(cell_spans)
            rows.append(cells)
    except Exception:  # noqa: BLE001 - merged/nested cells can misbehave
        logger.debug("Could not fully read a table", exc_info=True)

    rows = [row for row in rows if any(_join(cell).strip() for cell in row)]
    return Block(kind="table", rows=rows) if rows else None


def _join(spans: list[Span]) -> str:
    """Plain text of a span list."""
    return "".join(span.text for span in spans)
