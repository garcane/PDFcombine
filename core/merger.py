"""Merge tool: combines several PDFs into a single document.

The list of files is taken *exactly* as supplied by the caller - the review
screen in the UI is the single source of truth for ordering, never the order
in which the operating system handed the files over.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfWriter

from core.exceptions import PdfReadError, PdfToolkitError, PdfWriteError
from core.models import OperationResult, PdfFileInfo
from core.progress import CancellationToken, NullProgress, ProgressReporter
from core.validator import validate_output_file, validate_sources

logger = logging.getLogger(__name__)


class PdfMergeTool:
    """Combines an ordered list of PDFs into one output file."""

    #: Human readable name, used by the UI and by future tool registries.
    display_name = "Merge PDFs"

    def merge(
        self,
        files: list[PdfFileInfo],
        output_path: Path,
        progress: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> OperationResult:
        """Merge ``files`` into ``output_path``.

        Args:
            files: Documents to merge, in the exact output order.
            output_path: Destination file. Its folder is created if needed.
            progress: Optional progress sink.
            token: Optional cancellation token, polled between files.

        Returns:
            An :class:`OperationResult` describing the written file.

        Raises:
            ValidationError: Fewer than two usable files were supplied.
            PdfReadError: A source file vanished or is unreadable.
            PdfWriteError: The output could not be written.
            OperationCancelled: The caller cancelled the job.
        """
        progress = progress or NullProgress()
        token = token or CancellationToken()

        validate_sources(files, minimum=2)
        validate_output_file(output_path)
        self._reject_output_collision(files, output_path)

        total = len(files)
        pages_written = 0
        writer = PdfWriter()

        logger.info("Merging %d files into %s", total, output_path)
        try:
            for index, info in enumerate(files, start=1):
                token.raise_if_cancelled()
                progress.update(index - 1, total, f"Reading {info.name}")
                pages_written += self._append(writer, info)
                progress.update(index, total, f"Added {info.name}")

            token.raise_if_cancelled()
            progress.update(total, total, "Writing output file…")
            self._write(writer, output_path)
        finally:
            writer.close()

        logger.info("Merge complete: %s (%d pages)", output_path, pages_written)
        return OperationResult(
            output_paths=[output_path],
            output_folder=output_path.parent,
            pages_written=pages_written,
            message=(
                f"Merged {total} documents into '{output_path.name}' "
                f"({pages_written} pages)."
            ),
        )

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    @staticmethod
    def _append(writer: PdfWriter, info: PdfFileInfo) -> int:
        """Append every page of ``info`` to ``writer`` and return the count."""
        try:
            writer.append(str(info.path))
        except PdfToolkitError:
            raise
        except FileNotFoundError as exc:
            raise PdfReadError(f"'{info.name}' could not be found.") from exc
        except PermissionError as exc:
            raise PdfReadError(f"'{info.name}' could not be opened - permission denied.") from exc
        except Exception as exc:  # noqa: BLE001 - pypdf raises many error types
            logger.exception("Failed to append %s", info.path)
            raise PdfReadError(
                f"'{info.name}' could not be merged - the file may be damaged.\n{exc}"
            ) from exc
        return info.page_count

    @staticmethod
    def _write(writer: PdfWriter, output_path: Path) -> None:
        """Write ``writer`` to disk, translating OS errors to friendly ones."""
        try:
            with output_path.open("wb") as handle:
                writer.write(handle)
        except PermissionError as exc:
            raise PdfWriteError(
                f"'{output_path.name}' is in use or read-only.\n"
                "Close it in any other application and try again."
            ) from exc
        except OSError as exc:
            raise PdfWriteError(f"The merged file could not be saved.\n{exc}") from exc

    @staticmethod
    def _reject_output_collision(files: list[PdfFileInfo], output_path: Path) -> None:
        """Refuse to overwrite one of the inputs while reading from it."""
        try:
            destination = output_path.resolve()
        except OSError:  # pragma: no cover - defensive
            return
        for info in files:
            try:
                if info.path.resolve() == destination:
                    raise PdfWriteError(
                        f"'{output_path.name}' is one of the files being merged.\n"
                        "Choose a different output name."
                    )
            except OSError:  # pragma: no cover - defensive
                continue
