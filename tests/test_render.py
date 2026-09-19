"""Unit tests for the PDF to Image tool (core/render.py).

Run with::

    python -m pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from core.exceptions import OperationCancelled, PdfReadError, ValidationError
from core.progress import CancellationToken
from core.render import PdfToImageTool


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def make_pdf(path: Path, pages: int) -> Path:
    """Create a valid PDF at ``path`` with ``pages`` blank A4 pages."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    """A 5-page PDF."""
    return make_pdf(tmp_path / "sample.pdf", 5)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_render_every_page(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "images"
    result = PdfToImageTool().render(sample, out)

    assert result.file_count == 5
    assert (out / "sample_Page_001.png").exists()
    assert (out / "sample_Page_005.png").exists()
    with Image.open(out / "sample_Page_001.png") as image:
        assert image.format == "PNG"


def test_render_specific_pages(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "images"
    result = PdfToImageTool().render(sample, out, pages=[1, 3])

    assert result.file_count == 2
    assert (out / "sample_Page_001.png").exists()
    assert (out / "sample_Page_003.png").exists()
    assert not (out / "sample_Page_002.png").exists()


def test_render_jpg_format(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "images"
    result = PdfToImageTool().render(sample, out, image_format="jpg", pages=[1])

    assert result.output_paths[0].suffix == ".jpg"
    with Image.open(result.output_paths[0]) as image:
        assert image.format == "JPEG"


def test_render_higher_dpi_produces_larger_image(sample: Path, tmp_path: Path) -> None:
    low = PdfToImageTool().render(sample, tmp_path / "low", dpi=72, pages=[1])
    high = PdfToImageTool().render(sample, tmp_path / "high", dpi=300, pages=[1])

    with Image.open(low.output_paths[0]) as low_image, Image.open(high.output_paths[0]) as high_image:
        assert high_image.width > low_image.width


def test_render_rejects_bad_format(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        PdfToImageTool().render(sample, tmp_path / "out", image_format="bmp")


def test_render_rejects_dpi_out_of_range(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        PdfToImageTool().render(sample, tmp_path / "out", dpi=5)


def test_render_rejects_out_of_range_page(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        PdfToImageTool().render(sample, tmp_path / "out", pages=[99])


def test_render_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PdfReadError):
        PdfToImageTool().render(tmp_path / "nope.pdf", tmp_path / "out")


def test_render_keeps_existing_files_when_not_overwriting(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "images"
    PdfToImageTool().render(sample, out, pages=[1])
    second = PdfToImageTool().render(sample, out, pages=[1])
    assert "(2)" in second.output_paths[0].name


def test_render_overwrites_when_requested(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "images"
    first = PdfToImageTool().render(sample, out, pages=[1])
    second = PdfToImageTool().render(sample, out, pages=[1], overwrite=True)
    assert first.output_paths[0] == second.output_paths[0]
    assert len(list(out.glob("*.png"))) == 1


def test_render_reports_progress(sample: Path, tmp_path: Path) -> None:
    seen: list[tuple[int, int, str]] = []

    class Recorder:
        def update(self, completed: int, total: int, message: str) -> None:
            seen.append((completed, total, message))

    PdfToImageTool().render(sample, tmp_path / "out", progress=Recorder())
    assert seen[-1][0] == seen[-1][1] == 5


def test_render_cancellation_stops_the_job(sample: Path, tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        PdfToImageTool().render(sample, tmp_path / "out", token=token)
