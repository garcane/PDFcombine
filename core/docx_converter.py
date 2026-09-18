"""Convert Word documents to Markdown or PDF.

Supports three shapes of job:

* one document in, one file out;
* many documents in, one file out each (optionally mirroring the source
  folder structure, which is what a recursive folder conversion needs);
* many documents in, **one collated file** out, in the order the caller
  supplies.

PDF output picks an engine automatically: Microsoft Word and LibreOffice give
the most faithful page layout when they are installed, and the built-in
renderer (:mod:`core.pdf_renderer`) is always available as a fallback so the
feature never depends on other software being present.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path

from core.docx_reader import DocxDocument, read_docx
from core.exceptions import PdfToolkitError, PdfWriteError, ValidationError
from core.markdown_writer import document_to_markdown, documents_to_markdown
from core.models import DocxFileInfo, OperationResult
from core.pdf_renderer import render_documents_to_pdf
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.utils import unique_path
from core.validator import validate_output_folder

logger = logging.getLogger(__name__)

#: Where LibreOffice usually lives on Windows, macOS and Linux.
_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)

#: Word's ``wdFormatPDF`` export constant.
_WD_FORMAT_PDF = 17


class OutputFormat(str, Enum):
    """What the converter produces."""

    MARKDOWN = "markdown"
    PDF = "pdf"

    @property
    def label(self) -> str:
        """Title shown in the UI."""
        return {OutputFormat.MARKDOWN: "Markdown (.md)", OutputFormat.PDF: "PDF (.pdf)"}[self]

    @property
    def suffix(self) -> str:
        """File extension including the dot."""
        return {OutputFormat.MARKDOWN: ".md", OutputFormat.PDF: ".pdf"}[self]

    @property
    def description(self) -> str:
        """One-line explanation shown under the title."""
        return {
            OutputFormat.MARKDOWN: "Plain-text Markdown, keeping headings, lists and tables.",
            OutputFormat.PDF: "A PDF document, ready to share or print.",
        }[self]


class PdfEngine(str, Enum):
    """Which program performs the Word-to-PDF conversion."""

    AUTO = "auto"
    BUILTIN = "builtin"
    WORD = "word"
    LIBREOFFICE = "libreoffice"

    @property
    def label(self) -> str:
        """Title shown in the UI."""
        return {
            PdfEngine.AUTO: "Best available",
            PdfEngine.BUILTIN: "Built-in renderer",
            PdfEngine.WORD: "Microsoft Word",
            PdfEngine.LIBREOFFICE: "LibreOffice",
        }[self]


class DocxConvertTool:
    """Converts Word documents to Markdown or PDF, singly or in batches."""

    display_name = "Convert Word files"

    def convert(
        self,
        files: list[DocxFileInfo],
        output_folder: Path,
        output_format: OutputFormat = OutputFormat.MARKDOWN,
        combine: bool = False,
        combined_name: str | None = None,
        mirror_structure: bool = True,
        overwrite: bool = False,
        engine: PdfEngine = PdfEngine.AUTO,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Convert ``files`` into ``output_folder``.

        Args:
            files: Documents to convert, in the order they should appear when
                ``combine`` is set.
            output_folder: Destination folder, created when missing.
            output_format: Markdown or PDF.
            combine: Write one collated file instead of one file per document.
            combined_name: Name of the collated file; defaults to
                ``Combined`` plus the format's extension.
            mirror_structure: Recreate each file's sub-folder path under the
                output folder. Ignored when ``combine`` is set.
            overwrite: Replace existing files instead of adding a ``(2)``
                suffix to the name.
            engine: Which converter to use for PDF output.
            progress: Optional progress sink.
            token: Optional cancellation token.

        Returns:
            An :class:`OperationResult` listing every file written. Documents
            that could not be read are reported in
            :attr:`~core.models.OperationResult.warnings` rather than aborting
            the batch.

        Raises:
            ValidationError: No files were supplied.
            PdfWriteError: The output folder is not writable, or no document
                could be converted at all.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        if not files:
            raise ValidationError("Select at least one Word document to convert.")
        validate_output_folder(output_folder)

        if combine:
            return self._convert_combined(
                files, output_folder, output_format, combined_name, overwrite, engine, progress, token
            )
        return self._convert_each(
            files, output_folder, output_format, mirror_structure, overwrite, engine, progress, token
        )

    # ----------------------------------------------------------------- #
    # One output file per document
    # ----------------------------------------------------------------- #
    def _convert_each(
        self,
        files: list[DocxFileInfo],
        output_folder: Path,
        output_format: OutputFormat,
        mirror_structure: bool,
        overwrite: bool,
        engine: PdfEngine,
        progress: ProgressReporter,
        token: CancellationToken,
    ) -> OperationResult:
        """Convert every document into its own output file."""
        total = len(files)
        written: list[Path] = []
        warnings: list[str] = []

        for index, info in enumerate(files, start=1):
            token.raise_if_cancelled()
            progress.update(index - 1, total, f"Converting {info.name}")
            destination = self._destination(info, output_folder, output_format, mirror_structure)
            try:
                validate_output_folder(destination.parent)
                if not overwrite:
                    destination = unique_path(destination)
                self._write_one(info, destination, output_format, engine)
            except PdfToolkitError as exc:
                logger.warning("Skipped %s: %s", info.path, exc)
                warnings.append(f"{info.name}: {exc}")
            else:
                written.append(destination)
            progress.update(index, total, f"Finished {info.name}")

        if not written:
            raise PdfWriteError(
                "None of the selected documents could be converted.\n\n"
                + "\n\n".join(warnings[:5])
            )

        return OperationResult(
            output_paths=written,
            output_folder=output_folder,
            pages_written=0,
            message=self._summary(len(written), len(warnings), output_format),
            warnings=warnings,
        )

    def _destination(
        self,
        info: DocxFileInfo,
        output_folder: Path,
        output_format: OutputFormat,
        mirror_structure: bool,
    ) -> Path:
        """Work out where one document's output belongs."""
        name = info.path.stem + output_format.suffix
        if mirror_structure and info.relative_path is not None:
            return output_folder / info.relative_path.parent / name
        return output_folder / name

    def _write_one(
        self,
        info: DocxFileInfo,
        destination: Path,
        output_format: OutputFormat,
        engine: PdfEngine,
    ) -> None:
        """Convert a single document to ``destination``."""
        if output_format is OutputFormat.MARKDOWN:
            document = read_docx(info.path)
            _write_text(destination, document_to_markdown(document))
            return
        self._write_pdf([info.path], destination, engine)

    # ----------------------------------------------------------------- #
    # One collated output file
    # ----------------------------------------------------------------- #
    def _convert_combined(
        self,
        files: list[DocxFileInfo],
        output_folder: Path,
        output_format: OutputFormat,
        combined_name: str | None,
        overwrite: bool,
        engine: PdfEngine,
        progress: ProgressReporter,
        token: CancellationToken,
    ) -> OperationResult:
        """Collate every document into one output file."""
        name = combined_name or f"Combined{output_format.suffix}"
        if not name.lower().endswith(output_format.suffix):
            name += output_format.suffix
        destination = output_folder / name
        if not overwrite:
            destination = unique_path(destination)

        total = len(files) + 1
        warnings: list[str] = []

        if output_format is OutputFormat.MARKDOWN:
            documents: list[DocxDocument] = []
            for index, info in enumerate(files, start=1):
                token.raise_if_cancelled()
                progress.update(index - 1, total, f"Reading {info.name}")
                try:
                    documents.append(read_docx(info.path))
                except PdfToolkitError as exc:
                    logger.warning("Skipped %s: %s", info.path, exc)
                    warnings.append(f"{info.name}: {exc}")
                progress.update(index, total, f"Read {info.name}")

            if not documents:
                raise PdfWriteError(
                    "None of the selected documents could be read.\n\n"
                    + "\n\n".join(warnings[:5])
                )
            progress.update(total - 1, total, f"Writing {destination.name}")
            _write_text(destination, documents_to_markdown(documents))
        else:
            sources = [info.path for info in files]
            progress.update(len(files), total, f"Writing {destination.name}")
            warnings.extend(self._write_pdf(sources, destination, engine, token=token))

        progress.update(total, total, "Finished")
        count = len(files) - len(warnings)
        return OperationResult(
            output_paths=[destination],
            output_folder=output_folder,
            pages_written=0,
            message=(
                f"Combined {count} document{'s' if count != 1 else ''} into "
                f"'{destination.name}'."
            ),
            warnings=warnings,
        )

    # ----------------------------------------------------------------- #
    # PDF engines
    # ----------------------------------------------------------------- #
    def _write_pdf(
        self,
        sources: list[Path],
        destination: Path,
        engine: PdfEngine,
        token: CancellationToken | None = None,
    ) -> list[str]:
        """Convert ``sources`` into one PDF at ``destination``.

        Returns:
            Warnings for individual documents that could not be converted.

        Raises:
            PdfWriteError: Nothing at all could be converted.
        """
        chosen = self._resolve_engine(engine)
        if chosen is not PdfEngine.BUILTIN:
            try:
                return self._write_pdf_external(sources, destination, chosen, token)
            except PdfToolkitError:
                raise
            except Exception as exc:  # noqa: BLE001 - COM/CLI failures vary wildly
                logger.warning(
                    "%s conversion failed (%s) - falling back to the built-in renderer",
                    chosen.value,
                    exc,
                )
        return self._write_pdf_builtin(sources, destination, token)

    def _write_pdf_builtin(
        self, sources: list[Path], destination: Path, token: CancellationToken | None
    ) -> list[str]:
        """Render with the bundled reportlab-based engine."""
        documents: list[DocxDocument] = []
        warnings: list[str] = []
        for path in sources:
            if token is not None:
                token.raise_if_cancelled()
            try:
                documents.append(read_docx(path))
            except PdfToolkitError as exc:
                logger.warning("Skipped %s: %s", path, exc)
                warnings.append(f"{path.name}: {exc}")

        if not documents:
            raise PdfWriteError(
                "None of the selected documents could be read.\n\n" + "\n\n".join(warnings[:5])
            )
        render_documents_to_pdf(
            documents, destination, include_titles=len(documents) > 1
        )
        return warnings

    def _write_pdf_external(
        self,
        sources: list[Path],
        destination: Path,
        engine: PdfEngine,
        token: CancellationToken | None,
    ) -> list[str]:
        """Convert with Word or LibreOffice, merging the results if needed."""
        warnings: list[str] = []
        with tempfile.TemporaryDirectory(prefix="pdf_toolkit_") as staging_dir:
            staging = Path(staging_dir)
            produced: list[Path] = []
            for index, path in enumerate(sources):
                if token is not None:
                    token.raise_if_cancelled()
                target = staging / f"{index:04d}_{path.stem}.pdf"
                try:
                    if engine is PdfEngine.WORD:
                        _convert_with_word(path, target)
                    else:
                        _convert_with_libreoffice(path, target)
                except PdfToolkitError as exc:
                    warnings.append(f"{path.name}: {exc}")
                    continue
                if target.exists():
                    produced.append(target)
                else:  # pragma: no cover - engine reported success but wrote nothing
                    warnings.append(f"{path.name}: the converter produced no output.")

            if not produced:
                raise PdfWriteError(
                    f"{engine.label} could not convert any of the selected documents."
                )
            if len(produced) == 1:
                shutil.copyfile(produced[0], destination)
            else:
                _merge_pdfs(produced, destination)
        return warnings

    @staticmethod
    def _resolve_engine(engine: PdfEngine) -> PdfEngine:
        """Turn :attr:`PdfEngine.AUTO` into a concrete, available engine."""
        if engine is not PdfEngine.AUTO:
            return engine
        for candidate in available_pdf_engines():
            if candidate is not PdfEngine.BUILTIN:
                return candidate
        return PdfEngine.BUILTIN

    @staticmethod
    def _summary(written: int, failed: int, output_format: OutputFormat) -> str:
        """Build the message shown when a per-file batch finishes."""
        noun = "file" if written == 1 else "files"
        text = f"Converted {written} {noun} to {output_format.label}."
        if failed:
            text += f"\n{failed} document{'s' if failed != 1 else ''} could not be converted."
        return text


# --------------------------------------------------------------------------- #
# Engine discovery
# --------------------------------------------------------------------------- #
def available_pdf_engines() -> list[PdfEngine]:
    """Return the PDF engines usable on this machine, best fidelity first."""
    engines: list[PdfEngine] = []
    if _word_available():
        engines.append(PdfEngine.WORD)
    if find_libreoffice() is not None:
        engines.append(PdfEngine.LIBREOFFICE)
    engines.append(PdfEngine.BUILTIN)  # always available
    return engines


def _word_available() -> bool:
    """``True`` when Microsoft Word can be driven through COM."""
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        import winreg
    except ImportError:
        return False
    try:
        # Word registers this ProgID; checking the registry avoids the cost and
        # side effects of actually starting the application.
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


def find_libreoffice() -> Path | None:
    """Locate the LibreOffice command-line binary, if it is installed."""
    from_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if from_path:
        return Path(from_path)
    for candidate in _SOFFICE_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


# --------------------------------------------------------------------------- #
# External converters
# --------------------------------------------------------------------------- #
def _convert_with_word(source: Path, destination: Path) -> None:
    """Convert one document using Microsoft Word through COM automation."""
    import pythoncom  # type: ignore[import-not-found]
    import win32com.client  # type: ignore[import-not-found]

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        document = word.Documents.Open(str(source.resolve()), ReadOnly=True, AddToRecentFiles=False)
        document.SaveAs(str(destination.resolve()), FileFormat=_WD_FORMAT_PDF)
    except Exception as exc:  # noqa: BLE001 - COM raises opaque errors
        raise PdfWriteError(f"Word could not convert '{source.name}'.\n{exc}") from exc
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        pythoncom.CoUninitialize()


def _convert_with_libreoffice(source: Path, destination: Path) -> None:
    """Convert one document using a headless LibreOffice process."""
    soffice = find_libreoffice()
    if soffice is None:  # pragma: no cover - guarded by engine discovery
        raise PdfWriteError("LibreOffice is not installed on this computer.")

    outdir = destination.parent
    command = [
        str(soffice),
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(source.resolve()),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfWriteError(f"LibreOffice timed out converting '{source.name}'.") from exc
    except OSError as exc:
        raise PdfWriteError(f"LibreOffice could not be started.\n{exc}") from exc

    produced = outdir / f"{source.stem}.pdf"
    if not produced.exists():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PdfWriteError(f"LibreOffice could not convert '{source.name}'.\n{detail}")
    if produced != destination:
        os.replace(produced, destination)


def _merge_pdfs(sources: list[Path], destination: Path) -> None:
    """Concatenate already-converted PDFs into ``destination``."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    try:
        for path in sources:
            writer.append(str(path))
        with destination.open("wb") as handle:
            writer.write(handle)
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        raise PdfWriteError(f"The converted pages could not be combined.\n{exc}") from exc
    finally:
        writer.close()


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
