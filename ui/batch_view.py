"""Batch to PDF view: convert text, CSV, log and .sql files to PDF."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from core.batch import TextToPdfTool
from core.constants import BATCH_INPUT_EXTENSIONS, DEFAULT_BATCH_COMBINED_NAME
from core.models import OperationResult
from core.progress import CallbackProgress, CancellationToken
from core.utils import deduplicate, open_in_file_manager
from ui.base_view import BaseView
from ui.dialogs import ask_batch_files, ask_confirm, ask_folder, show_error, show_message, show_success
from ui.dnd import enable_file_drop
from ui.theme import (
    CARD_RADIUS,
    COLORS,
    PAD,
    entry_style,
    fonts,
    primary_button_style,
    secondary_button_style,
    small_button_style,
)
from ui.widgets import Card, EmptyState, PageHeader, ProgressPanel
from ui.workers import JobRunner

logger = logging.getLogger(__name__)


class _FileRow(ctk.CTkFrame):
    """One entry in the batch file list: name, position controls, remove."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        path: Path,
        on_remove: Callable[[Path], None],
        on_move: Callable[[Path, int], None],
    ) -> None:
        super().__init__(master, fg_color=COLORS.surface_alt, corner_radius=8)
        self.path = path

        ctk.CTkLabel(
            self,
            text=path.name,
            font=fonts().body,
            text_color=COLORS.text,
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=PAD.sm, pady=PAD.xs)

        ctk.CTkButton(
            self, text="▲", width=28, command=lambda: on_move(path, -1), **small_button_style()
        ).pack(side="left", padx=(0, 2))
        ctk.CTkButton(
            self, text="▼", width=28, command=lambda: on_move(path, 1), **small_button_style()
        ).pack(side="left", padx=(0, PAD.sm))
        ctk.CTkButton(
            self, text="✕", width=28, command=lambda: on_remove(path), **small_button_style()
        ).pack(side="left", padx=(0, PAD.sm))


class BatchView(BaseView):
    """Workflow for converting one or more text/CSV files to PDF."""

    view_name = "batch"
    status_hint = "Choose text or CSV files to convert"

    def build(self) -> None:
        """Create every widget in the view."""
        self._tool = TextToPdfTool()
        self._runner: JobRunner[OperationResult] = JobRunner(self)
        self._files: list[Path] = []
        self._output_folder: Path | None = None
        self._last_folder: Path | None = None
        self._combine = ctk.BooleanVar(value=False)
        self._overwrite = ctk.BooleanVar(value=False)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.xl, pady=PAD.lg)

        PageHeader(
            container,
            "Batch to PDF",
            "Convert text, CSV, log and .sql files to PDF - one file at a time or all "
            "collated into one.",
            on_back=self.go_home,
        ).pack(fill="x", pady=(0, PAD.md))

        self._progress = ProgressPanel(container, on_cancel=self._cancel)
        self._build_footer(container)

        body = ctk.CTkFrame(container, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3, uniform="batch")
        body.grid_columnconfigure(1, weight=2, uniform="batch")
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD.md))
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_source(left)
        self._build_options(right)
        self._build_output(right)

        enable_file_drop(self, self._handle_drop)
        self._sync_state()

    # ----------------------------------------------------------------- #
    # Layout sections
    # ----------------------------------------------------------------- #
    def _build_source(self, parent: ctk.CTkFrame) -> None:
        """File picker, drop target and the re-orderable review list."""
        card = Card(parent, "1.  Choose the files")
        card.pack(fill="both", expand=True)
        card.body.pack_configure(fill="both", expand=True)

        button_row = ctk.CTkFrame(card.body, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(
            button_row,
            text="🗎   Select files",
            width=170,
            command=self._choose_files,
            **secondary_button_style(),
        ).pack(side="left")
        ctk.CTkButton(
            button_row, text="Clear all", width=110, command=self._clear_files, **small_button_style()
        ).pack(side="left", padx=(PAD.sm, 0))

        self._list_frame = ctk.CTkScrollableFrame(
            card.body,
            fg_color=COLORS.surface,
            corner_radius=CARD_RADIUS,
            scrollbar_button_color=COLORS.border_strong,
            scrollbar_button_hover_color=COLORS.accent,
        )
        self._list_frame.pack(fill="both", expand=True, pady=(PAD.md, 0))

        self._empty_state = EmptyState(
            self._list_frame,
            "🗎",
            "No files yet",
            "Select or drag in .txt, .log, .sql, .md or .csv files.",
        )

    def _build_options(self, parent: ctk.CTkFrame) -> None:
        """Combine switch and the combined-file name entry."""
        card = Card(parent, "2.  Choose the output")
        card.pack(fill="x")

        ctk.CTkSwitch(
            card.body,
            text="Collate everything into one PDF",
            variable=self._combine,
            command=self._sync_state,
            font=fonts().small,
            text_color=COLORS.text_muted,
            progress_color=COLORS.accent,
            button_color=COLORS.text_muted,
        ).pack(anchor="w")

        name_row = ctk.CTkFrame(card.body, fg_color="transparent")
        name_row.pack(fill="x", pady=(PAD.md, 0))
        ctk.CTkLabel(
            name_row, text="Combined file name", font=fonts().small_bold, text_color=COLORS.text_muted, anchor="w"
        ).pack(fill="x")
        self._name_entry = ctk.CTkEntry(
            name_row, placeholder_text=DEFAULT_BATCH_COMBINED_NAME, **entry_style()
        )
        self._name_entry.pack(fill="x", pady=(PAD.xs, 0))

    def _build_output(self, parent: ctk.CTkFrame) -> None:
        """Destination folder chooser and the overwrite switch."""
        card = Card(parent, "3.  Choose where to save")
        card.pack(fill="x", pady=(PAD.md, 0))

        ctk.CTkButton(
            card.body,
            text="📁   Output folder",
            command=self._choose_output,
            **secondary_button_style(),
        ).pack(fill="x")

        self._output_label = ctk.CTkLabel(
            card.body,
            text="Defaults to the folder of the first file",
            font=fonts().small,
            text_color=COLORS.text_muted,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self._output_label.pack(fill="x", pady=(PAD.sm, 0))

        ctk.CTkSwitch(
            card.body,
            text="Replace files that already exist",
            variable=self._overwrite,
            font=fonts().small,
            text_color=COLORS.text_muted,
            progress_color=COLORS.accent,
            button_color=COLORS.text_muted,
        ).pack(anchor="w", pady=(PAD.md, 0))

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        """Summary text and the primary convert action."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(PAD.md, 0))

        self._summary = ctk.CTkLabel(
            footer, text="", font=fonts().body, text_color=COLORS.text_muted, anchor="w"
        )
        self._summary.pack(side="left", fill="x", expand=True)

        self._convert_button = ctk.CTkButton(
            footer, text="Convert to PDF", width=200, command=self._convert, **primary_button_style()
        )
        self._convert_button.pack(side="right")

    # ----------------------------------------------------------------- #
    # File selection
    # ----------------------------------------------------------------- #
    def _choose_files(self) -> None:
        """Pick text/CSV files with the native multi-select dialog."""
        if self._busy_guard():
            return
        paths = ask_batch_files(self, self._last_folder)
        if not paths:
            return
        self._add_files(paths)

    def _handle_drop(self, paths: list[Path]) -> None:
        """Accept text/CSV files dropped onto the window, ignoring the rest."""
        if self._busy_guard():
            return
        wanted = {ext.lower() for ext in BATCH_INPUT_EXTENSIONS}
        matched = [
            path for path in paths if path.is_file() and path.suffix.lower() in wanted
        ]
        if not matched:
            show_message(
                self,
                "Nothing to add",
                "Drop .txt, .log, .sql, .md or .csv files here.",
                "warning",
            )
            return
        self._add_files(matched)

    def _add_files(self, paths: list[Path]) -> None:
        """Append ``paths`` to the selection, skipping duplicates."""
        self._files = deduplicate(self._files + paths)
        self._last_folder = paths[0].parent
        if self._output_folder is None:
            self._output_folder = paths[0].parent
        self._refresh_list()
        self._sync_state()
        self.controller.set_status(f"{len(paths)} file(s) added", transient=True, tone="success")

    def _remove_file(self, path: Path) -> None:
        """Remove one file from the selection."""
        self._files = [item for item in self._files if item != path]
        self._refresh_list()
        self._sync_state()

    def _move_file(self, path: Path, delta: int) -> None:
        """Move ``path`` up or down one position."""
        index = self._files.index(path)
        target = index + delta
        if not (0 <= target < len(self._files)):
            return
        self._files[index], self._files[target] = self._files[target], self._files[index]
        self._refresh_list()

    def _clear_files(self) -> None:
        """Remove every selected file."""
        if self._busy_guard():
            return
        self._files = []
        self._refresh_list()
        self._sync_state()

    def _refresh_list(self) -> None:
        """Rebuild the review list widgets from :attr:`_files`."""
        for child in self._list_frame.winfo_children():
            child.pack_forget()
            if child is not self._empty_state:
                child.destroy()

        if not self._files:
            self._empty_state.pack(fill="both", expand=True)
            return

        for path in self._files:
            row = _FileRow(self._list_frame, path, self._remove_file, self._move_file)
            row.pack(fill="x", pady=(0, PAD.xs))

    # ----------------------------------------------------------------- #
    # Output selection
    # ----------------------------------------------------------------- #
    def _choose_output(self) -> None:
        """Pick the destination folder."""
        if self._busy_guard():
            return
        folder = ask_folder(self, "Select an output folder", self._output_folder or self._last_folder)
        if folder is None:
            return
        self._output_folder = folder
        self._sync_state()

    # ----------------------------------------------------------------- #
    # Validation / state
    # ----------------------------------------------------------------- #
    def _sync_state(self) -> None:
        """Refresh derived labels and enable/disable controls."""
        if self._output_folder is not None:
            self._output_label.configure(text=str(self._output_folder), text_color=COLORS.text)
        self._name_entry.configure(state="normal" if self._combine.get() else "disabled")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """Update the footer summary line."""
        count = len(self._files)
        if not count:
            self._summary.configure(text="No files selected")
            return
        noun = "file" if count == 1 else "files"
        mode = "combined into one PDF" if self._combine.get() else "converted individually"
        self._summary.configure(text=f"{count} {noun} selected  ·  will be {mode}")

    # ----------------------------------------------------------------- #
    # Converting
    # ----------------------------------------------------------------- #
    def _convert(self) -> None:
        """Validate everything, confirm, then run the batch job."""
        if self._busy_guard():
            return
        if not self._files:
            show_message(self, "No files selected", "Choose at least one file to convert.", "warning")
            return

        missing = [path.name for path in self._files if not path.exists()]
        if missing:
            show_message(
                self,
                "Files no longer available",
                "These files are missing:\n" + "\n".join(f"  • {name}" for name in missing),
                "warning",
            )
            return

        folder = self._output_folder or self._files[0].parent
        combine = bool(self._combine.get())
        combined_name = self._name_entry.get().strip() or None

        if len(self._files) > 50 and not ask_confirm(
            self,
            "Convert many files?",
            f"This will process {len(self._files)} files into:\n{folder}\n\nContinue?",
            confirm_label="Convert",
        ):
            return

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(PAD.md, 0))
        self._progress.start("Converting files…")

        files = list(self._files)
        overwrite = bool(self._overwrite.get())

        def job(progress: CallbackProgress, token: CancellationToken) -> OperationResult:
            """Run the batch tool on the worker thread."""
            return self._tool.convert(
                sources=files,
                output_folder=folder,
                combine=combine,
                combined_name=combined_name,
                overwrite=overwrite,
                progress=progress,
                token=token,
            )

        self._runner.start(
            job,
            on_success=self._convert_done,
            on_error=self._job_failed,
            on_progress=self._progress.update_progress,
            on_cancelled=self._job_cancelled,
        )

    def _convert_done(self, result: OperationResult) -> None:
        """Report success and offer to open the output folder."""
        self._progress.finish("Conversion complete")
        self._finish_job()
        self.controller.set_status("Conversion completed successfully", transient=True, tone="success")

        preview = "\n".join(f"  • {path.name}" for path in result.output_paths[:5])
        if len(result.output_paths) > 5:
            preview += f"\n  …and {len(result.output_paths) - 5} more"
        message = f"{result.message}\n\nSaved to:\n{result.output_folder}\n\n{preview}"
        if result.warnings:
            shown = "\n".join(f"  • {warning}" for warning in result.warnings[:5])
            message += f"\n\nSkipped:\n{shown}"

        if show_success(self, "Conversion completed successfully", message):
            open_in_file_manager(result.output_folder)

    # ----------------------------------------------------------------- #
    # Job lifecycle helpers
    # ----------------------------------------------------------------- #
    def _cancel(self) -> None:
        """Cancel the running conversion."""
        self._runner.cancel()
        self.controller.set_status("Cancelling…", tone="warning")

    def _job_cancelled(self) -> None:
        """Restore the UI after cancellation."""
        self._finish_job()
        self.controller.set_status(
            "Cancelled - files created so far were kept", transient=True, tone="warning"
        )

    def _job_failed(self, error: Exception) -> None:
        """Restore the UI and show the error."""
        self._finish_job()
        self.controller.set_status("The operation failed", transient=True, tone="error")
        show_error(self, error)

    def _finish_job(self) -> None:
        """Hide the progress panel and re-enable the controls."""
        self._set_busy(False)
        self._progress.pack_forget()

    def _set_busy(self, busy: bool) -> None:
        """Enable or disable the primary action while work is in flight."""
        self._convert_button.configure(state="disabled" if busy else "normal")

    def _busy_guard(self) -> bool:
        """Return ``True`` (and warn) when a job is already running."""
        if self._runner.busy:
            self.controller.set_status("Please wait for the current task to finish", transient=True)
            return True
        return False

    # ----------------------------------------------------------------- #
    # View lifecycle
    # ----------------------------------------------------------------- #
    def on_show(self) -> None:
        """Bind view shortcuts."""
        super().on_show()
        top = self.winfo_toplevel()
        top.bind("<Control-o>", lambda _event: self._choose_files(), add="+")
        top.bind("<Control-Return>", lambda _event: self._convert(), add="+")
        top.bind("<Escape>", lambda _event: self.go_home(), add="+")

    def on_hide(self) -> None:
        """Release view shortcuts."""
        top = self.winfo_toplevel()
        for sequence in ("<Control-o>", "<Control-Return>", "<Escape>"):
            try:
                top.unbind(sequence)
            except Exception:  # noqa: BLE001 - binding may not exist
                pass

    def can_leave(self) -> bool:
        """Block navigation while a conversion is running."""
        if self._runner.busy:
            if not ask_confirm(
                self,
                "Task in progress",
                "A conversion is still running. Cancel it and leave this screen?",
                confirm_label="Cancel task",
                destructive=True,
            ):
                return False
            self._cancel()
        return True

    def destroy(self) -> None:
        """Stop background workers before tearing the view down."""
        self._runner.shutdown()
        super().destroy()
