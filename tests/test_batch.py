"""Unit tests for the batch text/CSV to PDF tool (core/batch.py).

Run with::

    python -m pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from core.batch import TextToPdfTool
from core.exceptions import OperationCancelled, PdfWriteError, ValidationError
from core.progress import CancellationToken


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def make_txt(path: Path, text: str = "hello world\nsecond line") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def make_csv(path: Path, rows: list[list[str]] | None = None) -> Path:
    rows = rows or [["Name", "Age"], ["Ada", "36"], ["Grace", "85"]]
    path.write_text("\n".join(",".join(row) for row in rows), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_convert_txt_each(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "log.txt")
    out = tmp_path / "out"
    result = TextToPdfTool().convert([source], out)

    assert result.file_count == 1
    output = out / "log.pdf"
    assert output.exists()
    assert len(PdfReader(str(output)).pages) >= 1


def test_convert_csv_each(tmp_path: Path) -> None:
    source = make_csv(tmp_path / "data.csv")
    out = tmp_path / "out"
    result = TextToPdfTool().convert([source], out)

    assert result.file_count == 1
    assert (out / "data.pdf").exists()


def test_convert_sql_treated_as_text(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "query.sql", "SELECT * FROM users;")
    out = tmp_path / "out"
    result = TextToPdfTool().convert([source], out)

    assert (out / "query.pdf").exists()
    assert result.file_count == 1


def test_convert_combined_produces_one_file(tmp_path: Path) -> None:
    a = make_txt(tmp_path / "a.txt", "first file")
    b = make_csv(tmp_path / "b.csv")
    out = tmp_path / "out"
    result = TextToPdfTool().convert([a, b], out, combine=True)

    assert result.file_count == 1
    assert result.output_paths[0].name == "Batch_Combined.pdf"


def test_convert_combined_custom_name(tmp_path: Path) -> None:
    a = make_txt(tmp_path / "a.txt")
    out = tmp_path / "out"
    result = TextToPdfTool().convert([a], out, combine=True, combined_name="Report")
    assert result.output_paths[0].name == "Report.pdf"


def test_convert_skips_unreadable_and_warns(tmp_path: Path) -> None:
    good = make_txt(tmp_path / "good.txt")
    missing = tmp_path / "missing.txt"
    out = tmp_path / "out"

    result = TextToPdfTool().convert([good, missing], out)

    assert result.file_count == 1
    assert len(result.warnings) == 1
    assert "missing.txt" in result.warnings[0]


def test_convert_rejects_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "image.png"
    bad.write_bytes(b"not really an image")
    out = tmp_path / "out"

    with pytest.raises(PdfWriteError):
        TextToPdfTool().convert([bad], out)


def test_convert_requires_at_least_one_file(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        TextToPdfTool().convert([], tmp_path / "out")


def test_convert_keeps_existing_files_when_not_overwriting(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "a.txt")
    out = tmp_path / "out"
    TextToPdfTool().convert([source], out)
    second = TextToPdfTool().convert([source], out)
    assert "(2)" in second.output_paths[0].name


def test_convert_overwrites_when_requested(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "a.txt")
    out = tmp_path / "out"
    first = TextToPdfTool().convert([source], out)
    second = TextToPdfTool().convert([source], out, overwrite=True)
    assert first.output_paths[0] == second.output_paths[0]
    assert len(list(out.glob("*.pdf"))) == 1


def test_convert_handles_non_utf8_text(tmp_path: Path) -> None:
    source = tmp_path / "latin.txt"
    source.write_bytes("café".encode("latin-1"))
    out = tmp_path / "out"
    result = TextToPdfTool().convert([source], out)
    assert result.file_count == 1


def test_convert_csv_with_special_xml_characters(tmp_path: Path) -> None:
    source = make_csv(
        tmp_path / "data.csv", [["Name", "Note"], ["A & B", "<tag> value"]]
    )
    out = tmp_path / "out"
    result = TextToPdfTool().convert([source], out)
    assert result.file_count == 1


def test_convert_reports_progress(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "a.txt")
    seen: list[tuple[int, int, str]] = []

    class Recorder:
        def update(self, completed: int, total: int, message: str) -> None:
            seen.append((completed, total, message))

    TextToPdfTool().convert([source], tmp_path / "out", progress=Recorder())
    assert seen[-1][0] == seen[-1][1] == 1


def test_convert_cancellation_stops_the_job(tmp_path: Path) -> None:
    source = make_txt(tmp_path / "a.txt")
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        TextToPdfTool().convert([source], tmp_path / "out", token=token)
