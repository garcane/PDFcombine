"""Unit tests for the processing layer.

Run with::

    python -m pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from core.exceptions import PdfReadError, PdfWriteError, ValidationError
from core.merger import PdfMergeTool
from core.splitter import PdfSplitTool, SplitMode
from core.utils import deduplicate, human_readable_size, natural_sort_key, unique_path
from core.validator import inspect_pdf, parse_page_list, parse_page_ranges


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
    """A 10-page PDF."""
    return make_pdf(tmp_path / "sample.pdf", 10)


# --------------------------------------------------------------------------- #
# utils
# --------------------------------------------------------------------------- #
def test_human_readable_size() -> None:
    assert human_readable_size(0) == "0 B"
    assert human_readable_size(2048) == "2.0 KB"
    assert human_readable_size(5 * 1024 * 1024) == "5.0 MB"


def test_natural_sort_orders_numbers_numerically() -> None:
    names = ["file10.pdf", "file2.pdf", "File1.pdf"]
    assert sorted(names, key=natural_sort_key) == ["File1.pdf", "file2.pdf", "file10.pdf"]


def test_unique_path_avoids_collisions(tmp_path: Path) -> None:
    target = make_pdf(tmp_path / "out.pdf", 1)
    assert unique_path(target).name == "out (2).pdf"


def test_deduplicate_keeps_first_occurrence(tmp_path: Path) -> None:
    first = make_pdf(tmp_path / "a.pdf", 1)
    assert deduplicate([first, first, tmp_path / "b.pdf"]) == [first, tmp_path / "b.pdf"]


# --------------------------------------------------------------------------- #
# validator
# --------------------------------------------------------------------------- #
def test_inspect_pdf_reads_metadata(sample: Path) -> None:
    info = inspect_pdf(sample)
    assert info.page_count == 10
    assert info.size_bytes > 0
    assert info.page_label == "10 pages"


def test_inspect_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PdfReadError):
        inspect_pdf(tmp_path / "nope.pdf")


def test_inspect_pdf_rejects_corrupt_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf at all")
    with pytest.raises(PdfReadError):
        inspect_pdf(broken)


def test_inspect_pdf_rejects_non_pdf(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("hello", encoding="utf-8")
    with pytest.raises(PdfReadError):
        inspect_pdf(other)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1-5,6-10", [(1, 5), (6, 10)]),
        ("1-5, 8, 11-14", [(1, 5), (8, 8), (11, 14)]),
        (" 3 ; 4 ", [(3, 3), (4, 4)]),
    ],
)
def test_parse_page_ranges(text: str, expected: list[tuple[int, int]]) -> None:
    assert parse_page_ranges(text, 20) == expected


@pytest.mark.parametrize("text", ["", "abc", "5-1", "0-3", "1-99", "1--2", "-"])
def test_parse_page_ranges_rejects_bad_input(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_page_ranges(text, 10)


def test_parse_page_list_sorts_and_deduplicates() -> None:
    assert parse_page_list("7,1,4,4,2-3", 10) == [1, 2, 3, 4, 7]


# --------------------------------------------------------------------------- #
# merger
# --------------------------------------------------------------------------- #
def test_merge_preserves_supplied_order(tmp_path: Path) -> None:
    first = inspect_pdf(make_pdf(tmp_path / "b.pdf", 2))
    second = inspect_pdf(make_pdf(tmp_path / "a.pdf", 3))
    output = tmp_path / "merged.pdf"

    result = PdfMergeTool().merge([first, second], output)

    assert output.exists()
    assert result.pages_written == 5
    assert len(PdfReader(str(output)).pages) == 5


def test_merge_requires_two_files(tmp_path: Path) -> None:
    only = inspect_pdf(make_pdf(tmp_path / "one.pdf", 1))
    with pytest.raises(ValidationError):
        PdfMergeTool().merge([only], tmp_path / "out.pdf")


def test_merge_refuses_to_overwrite_an_input(tmp_path: Path) -> None:
    first = inspect_pdf(make_pdf(tmp_path / "a.pdf", 1))
    second = inspect_pdf(make_pdf(tmp_path / "b.pdf", 1))
    with pytest.raises(PdfWriteError):
        PdfMergeTool().merge([first, second], first.path)


def test_merge_reports_progress(tmp_path: Path) -> None:
    files = [
        inspect_pdf(make_pdf(tmp_path / f"f{index}.pdf", 1)) for index in range(3)
    ]
    seen: list[tuple[int, int, str]] = []

    class Recorder:
        def update(self, completed: int, total: int, message: str) -> None:
            seen.append((completed, total, message))

    PdfMergeTool().merge(files, tmp_path / "out.pdf", Recorder())
    assert seen[-1][0] == seen[-1][1] == 3


# --------------------------------------------------------------------------- #
# splitter
# --------------------------------------------------------------------------- #
def test_split_every_page(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "pages"
    result = PdfSplitTool().split(sample, out, SplitMode.EVERY_PAGE)

    assert result.file_count == 10
    assert (out / "Page_001.pdf").exists()
    assert (out / "Page_010.pdf").exists()
    assert len(PdfReader(str(out / "Page_001.pdf")).pages) == 1


def test_split_by_ranges(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "ranges"
    result = PdfSplitTool().split(sample, out, SplitMode.RANGES, "1-5,6-10")

    assert result.file_count == 2
    assert result.pages_written == 10
    assert len(PdfReader(str(result.output_paths[0])).pages) == 5


def test_extract_specific_pages(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "extract"
    result = PdfSplitTool().split(sample, out, SplitMode.EXTRACT, "1,4,7,10")

    assert result.file_count == 1
    assert len(PdfReader(str(result.output_paths[0])).pages) == 4


def test_split_rejects_out_of_range_selection(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        PdfSplitTool().split(sample, tmp_path / "out", SplitMode.RANGES, "1-50")


def test_split_keeps_existing_files_when_not_overwriting(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "twice"
    PdfSplitTool().split(sample, out, SplitMode.EXTRACT, "1")
    second = PdfSplitTool().split(sample, out, SplitMode.EXTRACT, "1")
    assert "(2)" in second.output_paths[0].name


def test_split_overwrites_when_requested(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "over"
    first = PdfSplitTool().split(sample, out, SplitMode.EXTRACT, "1")
    second = PdfSplitTool().split(sample, out, SplitMode.EXTRACT, "1", overwrite=True)
    assert first.output_paths[0] == second.output_paths[0]
    assert len(list(out.glob("*.pdf"))) == 1


def test_cancellation_stops_the_job(sample: Path, tmp_path: Path) -> None:
    from core.exceptions import OperationCancelled
    from core.progress import CancellationToken

    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        PdfSplitTool().split(sample, tmp_path / "cancel", SplitMode.EVERY_PAGE, token=token)
