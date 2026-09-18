"""Render the document block model as Markdown."""

from __future__ import annotations

from core.docx_reader import Block, DocxDocument, Span

#: Characters that would otherwise be read as Markdown syntax at the start of
#: a line. Escaped so document text survives the round trip intact.
_LINE_PREFIXES = ("#", ">", "-", "+", "*", "|", "=")


def document_to_markdown(document: DocxDocument, include_title: bool = False) -> str:
    """Convert ``document`` to a Markdown string.

    Args:
        document: The parsed Word document.
        include_title: Prefix the output with the document name as a level-1
            heading. Used when several documents are collated into one file.

    Returns:
        Markdown text ending with a single newline.
    """
    parts: list[tuple[str, str]] = []
    if include_title:
        parts.append(("heading", f"# {_escape(document.title)}"))

    numbering: dict[int, int] = {}
    for block in document.blocks:
        if block.kind == "number":
            numbering[block.level] = numbering.get(block.level, 0) + 1
        else:
            numbering.clear()
        rendered = _render_block(block, numbering)
        if rendered:
            parts.append((block.kind, rendered))

    body = _join_blocks(parts)
    return f"{body}\n" if body else ""


def _join_blocks(parts: list[tuple[str, str]]) -> str:
    """Join rendered blocks, keeping consecutive list items in a tight list."""
    lines: list[str] = []
    previous_kind: str | None = None
    for kind, text in parts:
        if lines:
            same_list = kind == previous_kind and kind in {"bullet", "number"}
            lines.append("\n" if same_list else "\n\n")
        lines.append(text)
        previous_kind = kind
    return "".join(lines)


def documents_to_markdown(documents: list[DocxDocument]) -> str:
    """Collate several documents into one Markdown file, separated by rules."""
    sections = [document_to_markdown(doc, include_title=True) for doc in documents]
    return "\n\n---\n\n".join(section.rstrip() for section in sections if section) + "\n"


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #
def _render_block(block: Block, numbering: dict[int, int]) -> str:
    """Render one block."""
    if block.kind == "table":
        return _render_table(block)

    text = _inline(block.spans)
    if not text.strip():
        return ""

    if block.kind == "heading":
        return f"{'#' * max(1, min(block.level, 6))} {text}"
    if block.kind == "bullet":
        return f"{'  ' * block.level}- {text}"
    if block.kind == "number":
        index = numbering.get(block.level, 1)
        return f"{'  ' * block.level}{index}. {text}"
    if block.kind == "quote":
        return "\n".join(f"> {line}" for line in text.splitlines() or [""])
    return _escape_leading(text)


def _render_table(block: Block) -> str:
    """Render a table as a GitHub-flavoured Markdown pipe table."""
    if not block.rows:
        return ""
    width = max(len(row) for row in block.rows)
    header, *body = block.rows

    lines = [_render_row(header, width), "|" + "|".join([" --- "] * width) + "|"]
    lines.extend(_render_row(row, width) for row in body)
    return "\n".join(lines)


def _render_row(cells: list[list[Span]], width: int) -> str:
    """Render one table row, padding short rows to ``width`` columns."""
    rendered = [_inline(cell).replace("|", r"\|").replace("\n", " ") for cell in cells]
    rendered += [""] * (width - len(rendered))
    return "| " + " | ".join(rendered) + " |"


# --------------------------------------------------------------------------- #
# Inline formatting
# --------------------------------------------------------------------------- #
def _inline(spans: list[Span]) -> str:
    """Render inline spans, applying bold and italic markers."""
    parts: list[str] = []
    for span in spans:
        text = _escape(span.text)
        if not text.strip():
            parts.append(text)
            continue
        # Markers must hug the text, so move any padding outside them.
        stripped = text.strip()
        leading = text[: len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()) :]
        if span.bold and span.italic:
            stripped = f"***{stripped}***"
        elif span.bold:
            stripped = f"**{stripped}**"
        elif span.italic:
            stripped = f"*{stripped}*"
        parts.append(f"{leading}{stripped}{trailing}")
    return "".join(parts).strip()


def _escape(text: str) -> str:
    """Escape the inline Markdown characters that appear in ordinary prose."""
    for char in ("\\", "*", "_", "`", "[", "]"):
        text = text.replace(char, f"\\{char}")
    return text


def _escape_leading(text: str) -> str:
    """Escape a leading character that would turn the line into markup."""
    if text[:1] in _LINE_PREFIXES:
        return f"\\{text}"
    return text
