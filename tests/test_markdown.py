"""Unit tests for the PyMuPDF4LLM-backed Markdown conversion layer.

Run with::

    python -m pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from core.exceptions import OperationCancelled, PdfReadError, PdfWriteError, ValidationError
from core.markdown_converter import MarkdownConvertTool
from core.models import MarkdownFileInfo
from core.progress import CancellationToken
from core.utils import discover_markdown_sources, is_image, is_markdown_source
from core.validator import inspect_markdown_source


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def make_pdf(path: Path, text: str = "Hello PyMuPDF4LLM from a PDF.") -> Path:
    """Create a single-page PDF containing ``text``."""
    document = canvas.Canvas(str(path))
    document.drawString(72, 700, text)
    document.showPage()
    document.save()
    return path


def make_image(path: Path, colour: tuple[int, int, int] = (240, 240, 240)) -> Path:
    """Create a small PNG or JPEG image (format follows the extension)."""
    image = Image.new("RGB", (120, 80), colour)
    image.save(path)
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """A one-page PDF with extractable text."""
    return make_pdf(tmp_path / "sample.pdf")


@pytest.fixture()
def sample_png(tmp_path: Path) -> Path:
    """A plain PNG image."""
    return make_image(tmp_path / "sample.png")


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A folder of convertible files including sub-folders and noise."""
    root = tmp_path / "input"
    (root / "Nested").mkdir(parents=True)
    make_pdf(root / "a.pdf", "Alpha document")
    make_image(root / "b.png")
    make_image(root / "c.jpg", colour=(30, 90, 200))
    make_pdf(root / "Nested" / "d.pdf", "Delta document")
    (root / "notes.txt").write_text("not convertible", encoding="utf-8")
    (root / "logo.gif").write_bytes(b"GIF89a not really parsed here")
    return root


def infos(paths: list[Path], base: Path | None = None) -> list[MarkdownFileInfo]:
    """Inspect every path, returning the metadata objects."""
    return [inspect_markdown_source(path, base_folder=base) for path in paths]


# --------------------------------------------------------------------------- #
# Discovery and validation
# --------------------------------------------------------------------------- #
def test_is_markdown_source_recognises_pdfs_and_images(tmp_path: Path) -> None:
    assert is_markdown_source(tmp_path / "report.pdf")
    assert is_markdown_source(tmp_path / "photo.PNG")
    assert is_markdown_source(tmp_path / "photo.JPEG")
    assert not is_markdown_source(tmp_path / "notes.txt")
    assert is_image(tmp_path / "photo.jpg")
    assert not is_image(tmp_path / "report.pdf")


def test_discover_markdown_sources_ignores_noise(tree: Path) -> None:
    found = [path.name for path in discover_markdown_sources(tree)]
    assert found == ["a.pdf", "b.png", "c.jpg"]


def test_discover_markdown_sources_recursive_includes_subfolders(tree: Path) -> None:
    found = [path.name for path in discover_markdown_sources(tree, recursive=True)]
    assert found == ["a.pdf", "b.png", "c.jpg", "d.pdf"]


def test_inspect_pdf_records_pages(tree: Path) -> None:
    info = inspect_markdown_source(tree / "a.pdf")
    assert info.kind == "PDF"
    assert info.page_count == 1
    assert info.dimensions is None
    assert "page" in info.detail_label


def test_inspect_image_records_dimensions(tree: Path) -> None:
    png = inspect_markdown_source(tree / "b.png")
    jpg = inspect_markdown_source(tree / "c.jpg")
    assert (png.kind, png.dimensions) == ("PNG", (120, 80))
    assert (jpg.kind, jpg.dimensions) == ("JPEG", (120, 80))
    assert png.page_count == 0
    assert "120 × 80 px" in png.detail_label


def test_inspect_records_relative_path(tree: Path) -> None:
    info = inspect_markdown_source(tree / "Nested" / "d.pdf", base_folder=tree)
    assert info.relative_path == Path("Nested/d.pdf")


def test_inspect_rejects_unsupported_extension(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("hello", encoding="utf-8")
    with pytest.raises(PdfReadError):
        inspect_markdown_source(other)


def test_inspect_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PdfReadError):
        inspect_markdown_source(tmp_path / "nope.pdf")


def test_inspect_rejects_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(PdfReadError):
        inspect_markdown_source(empty)


def test_inspect_rejects_corrupt_image(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"definitely not a png image")
    with pytest.raises(PdfReadError):
        inspect_markdown_source(broken)


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
def test_convert_pdf_to_markdown(sample_pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    result = MarkdownConvertTool().convert(infos([sample_pdf]), output)

    written = result.output_paths
    assert [path.name for path in written] == ["sample.md"]
    markdown = written[0].read_text(encoding="utf-8")
    assert "Hello PyMuPDF4LLM from a PDF." in markdown
    assert result.pages_written == 1
    assert result.warnings == []


def test_convert_image_to_markdown_produces_content(sample_png: Path, tmp_path: Path) -> None:
    """An image yields recognised text (OCR) or a reference to the picture."""
    output = tmp_path / "out"
    result = MarkdownConvertTool().convert(infos([sample_png]), output)

    assert len(result.output_paths) == 1
    markdown = result.output_paths[0].read_text(encoding="utf-8")
    assert markdown.strip()  # never an empty document


def test_image_fallback_references_the_picture(tmp_path: Path) -> None:
    """Without recognised text the Markdown embeds the image itself."""
    (tmp_path / "shots").mkdir()
    image = make_image(tmp_path / "shots" / "page1.png")
    info = inspect_markdown_source(image)
    destination = tmp_path / "out" / "page1.md"

    markdown = MarkdownConvertTool._image_fallback(info, destination)

    assert markdown.startswith("# page1")
    assert "](../shots/page1.png)" in markdown
    assert "Tesseract" in markdown


def test_convert_mirrors_subfolder_structure(tree: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    files = infos(discover_markdown_sources(tree, recursive=True), base=tree)
    result = MarkdownConvertTool().convert(files, output)

    relative = {path.relative_to(output).as_posix() for path in result.output_paths}
    assert relative == {"a.md", "b.md", "c.md", "Nested/d.md"}


def test_convert_flattens_when_not_mirroring(tree: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    files = infos(discover_markdown_sources(tree, recursive=True), base=tree)
    result = MarkdownConvertTool().convert(files, output, mirror_structure=False)

    assert {path.name for path in result.output_paths} == {"a.md", "b.md", "c.md", "d.md"}
    assert all(path.parent == output for path in result.output_paths)


def test_convert_keeps_existing_files_when_not_overwriting(
    sample_pdf: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "sample.md").write_text("keep me", encoding="utf-8")

    result = MarkdownConvertTool().convert(infos([sample_pdf]), output)

    assert result.output_paths[0].name == "sample (2).md"
    assert (output / "sample.md").read_text(encoding="utf-8") == "keep me"


def test_convert_overwrites_when_requested(sample_pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "sample.md").write_text("old", encoding="utf-8")

    result = MarkdownConvertTool().convert(infos([sample_pdf]), output, overwrite=True)

    assert result.output_paths[0] == output / "sample.md"
    assert "Hello PyMuPDF4LLM" in (output / "sample.md").read_text(encoding="utf-8")


def test_convert_reports_failures_as_warnings(tree: Path, tmp_path: Path) -> None:
    """A file that vanished is reported; the rest of the batch continues."""
    output = tmp_path / "out"
    files = infos([tree / "a.pdf", tree / "b.png"])
    deleted = files[1].path
    deleted.unlink()

    result = MarkdownConvertTool().convert(files, output)

    assert [path.name for path in result.output_paths] == ["a.md"]
    assert len(result.warnings) == 1
    assert "b.png" in result.warnings[0]
    assert "1 could not be converted" in result.message


def test_convert_raises_when_nothing_works(sample_pdf: Path, tmp_path: Path) -> None:
    files = infos([sample_pdf])
    files[0].path.unlink()
    with pytest.raises(PdfWriteError):
        MarkdownConvertTool().convert(files, tmp_path / "out")


def test_convert_rejects_empty_selection(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        MarkdownConvertTool().convert([], tmp_path / "out")


def test_cancellation_stops_before_any_output(sample_pdf: Path, tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        MarkdownConvertTool().convert(infos([sample_pdf]), tmp_path / "out", token=token)
    assert not (tmp_path / "out" / "sample.md").exists()


def test_inspect_rejects_corrupt_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 but damaged beyond repair")
    with pytest.raises(PdfReadError):
        inspect_markdown_source(broken)
