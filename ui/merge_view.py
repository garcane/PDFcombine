"""Merge view: pick PDFs, review and re-order them, then merge."""

from __future__ import annotations

import logging
from pathlib import Path

import customtkinter as ctk

from core.constants import DEFAULT_MERGE_FILENAME
from core.exceptions import PdfReadError, PdfToolkitError
from core.merger import PdfMergeTool
from core.models import OperationResult, PdfFileInfo
from core.progress import CallbackProgress, CancellationToken
from core.utils import deduplicate, discover_pdfs, human_readable_size, is_pdf, open_in_file_manager
from core.validator import inspect_pdf
from ui.base_view import BaseView
from ui.dialogs import (
    ask_confirm,
    ask_folder,
    ask_pdf_files,
    ask_save_pdf,
    show_error,
    show_message,
    show_success,
)
from ui.dnd import DND_AVAILABLE, enable_file_drop
from ui.file_list import FileListPanel
from ui.theme import (
    COLORS,
    PAD,
    fonts,
    primary_button_style,
    secondary_button_style,
    small_button_style,
)
from ui.widgets import EmptyState, PageHeader, ProgressPanel
from ui.workers import JobRunner

logger = logging.getLogger(__name__)

#: Shortcuts registered while this view is on screen.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("<Control-o>", "add_files"),
    ("<Control-O>", "add_files"),
    ("<Control-Shift-O>", "add_folder"),
    ("<Control-d>", "add_folder"),
    ("<Delete>", "remove_selected"),
    ("<Alt-Up>", "move_up"),
    ("<Alt-Down>", "move_down"),
    ("<Control-Return>", "merge"),
    ("<Escape>", "back"),
)


class MergeView(BaseView):
    """Two-step merge workflow: choose sources, then review and merge."""

    view_name = "merge"
    status_hint = "Add PDF files or a folder to begin"

    def build(self) -> None:
        """Create every widget in the view."""
        self._tool = PdfMergeTool()
        self._import_runner: JobRunner[tuple[list[PdfFileInfo], list[str]]] = JobRunner(self)
        self._merge_runner: JobRunner[OperationResult] = JobRunner(self)
        self._last_folder: Path | None = None
        self._token: CancellationToken | None = None
        self._added_order_map: dict[str, int] = {}

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.xl, pady=PAD.lg)

        PageHeader(
            container,
            "Merge PDFs",
            "The order shown below is the order used in the merged document.",
            on_back=self._request_back,
        ).pack(fill="x", pady=(0, PAD.md))

        self._build_sources(container)
        self._build_toolbar(container)

        # Footer first: packed against the bottom it keeps its space no matter
        # how short the window becomes.
        self._progress = ProgressPanel(container, on_cancel=self._cancel_current)
        self._build_footer(container)

        self._list = FileListPanel(
            container, on_change=self._refresh_summary, empty_widget_factory=self._empty_state
        )
        self._list.pack(fill="both", expand=True, pady=(PAD.sm, PAD.md))
        self._enable_drop()
        self._refresh_summary()

    # ----------------------------------------------------------------- #
    # Layout sections
    # ----------------------------------------------------------------- #
    def _build_sources(self, parent: ctk.CTkFrame) -> None:
        """The two primary input methods."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkButton(
            row,
            text="📁   Select folder",
            command=self._add_folder,
            width=190,
            **secondary_button_style(),
        ).pack(side="left")

        ctk.CTkButton(
            row,
            text="🗎   Select files",
            command=self._add_files,
            width=190,
            **secondary_button_style(),
        ).pack(side="left", padx=PAD.sm)

        hint = (
            "…or drag PDFs straight into this window"
            if DND_AVAILABLE
            else "Tip: use Ctrl+O for files, Ctrl+D for a folder"
        )
        ctk.CTkLabel(row, text=hint, font=fonts().small, text_color=COLORS.text_disabled).pack(
            side="left", padx=PAD.md
        )

    def _build_toolbar(self, parent: ctk.CTkFrame) -> None:
        """Ordering controls that operate on the review list."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(PAD.md, 0))

        ctk.CTkLabel(
            bar, text="Order", font=fonts().small_bold, text_color=COLORS.text_muted
        ).pack(side="left", padx=(PAD.xs, PAD.sm))

        buttons = (
            ("Sort A–Z", self._sort),
            ("Reverse", self._list_action("reverse")),
            ("Reset order", self._reset_order),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
        )
        for label, command in buttons:
            ctk.CTkButton(bar, text=label, width=94, command=command, **small_button_style()).pack(
                side="left", padx=(0, PAD.xs)
            )

        ctk.CTkButton(
            bar, text="Remove", width=94, command=self._remove_selected, **small_button_style()
        ).pack(side="right")
        ctk.CTkButton(
            bar, text="Clear all", width=94, command=self._clear_all, **small_button_style()
        ).pack(side="right", padx=(0, PAD.xs))

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        """Summary line and the primary merge action."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(side="bottom", fill="x")

        self._summary = ctk.CTkLabel(
            footer, text="", font=fonts().body, text_color=COLORS.text_muted, anchor="w"
        )
        self._summary.pack(side="left", fill="x", expand=True)

        self._merge_button = ctk.CTkButton(
            footer,
            text="Merge PDFs",
            width=200,
            command=self._merge,
            **primary_button_style(),
        )
        self._merge_button.pack(side="right")

    @staticmethod
    def _empty_state(master: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
        """Placeholder shown while the review list is empty."""
        message = (
            "Drag PDF files here, or use Select folder / Select files above."
            if DND_AVAILABLE
            else "Use Select folder or Select files above to add documents."
        )
        return EmptyState(master, "🗂", "No PDFs selected yet", message)

    def _enable_drop(self) -> None:
        """Register the whole view as a drop target for PDFs and folders."""
        if not enable_file_drop(self, self._handle_drop):
            logger.debug("Drag-and-drop not registered for the merge view.")

    # ----------------------------------------------------------------- #
    # Adding files
    # ----------------------------------------------------------------- #
    def _add_files(self) -> None:
        """Open the native multi-select picker and import the result."""
        if self._busy_guard():
            return
        paths = ask_pdf_files(self, self._last_folder)
        if not paths:
            self.controller.set_status("No files selected", transient=True)
            return
        self._last_folder = paths[0].parent
        self._import(paths)

    def _add_folder(self) -> None:
        """Open the native folder picker and import every PDF inside it."""
        if self._busy_guard():
            return
        folder = ask_folder(self, "Select a folder containing PDFs", self._last_folder)
        if folder is None:
            self.controller.set_status("No folder selected", transient=True)
            return
        self._last_folder = folder

        pdfs = discover_pdfs(folder)
        if not pdfs:
            show_message(
                self,
                "No PDFs found",
                f"'{folder.name}' does not contain any PDF files.\n\n"
                "Sub-folders are not searched.",
                "warning",
            )
            return
        self._import(pdfs)

    def _handle_drop(self, paths: list[Path]) -> None:
        """Handle files or folders dropped onto the window."""
        if self._busy_guard():
            return
        candidates: list[Path] = []
        for path in paths:
            if path.is_dir():
                candidates.extend(discover_pdfs(path))
            elif is_pdf(path):
                candidates.append(path)

        ignored = len(paths) - sum(1 for path in paths if path.is_dir() or is_pdf(path))
        if not candidates:
            show_message(
                self,
                "Nothing to add",
                "None of the dropped items are PDF files.",
                "warning",
            )
            return
        if ignored:
            self.controller.set_status(
                f"Ignored {ignored} non-PDF item{'s' if ignored != 1 else ''}", transient=True
            )
        self._import(candidates)

    def _import(self, paths: list[Path]) -> None:
        """Inspect ``paths`` on a worker thread and add the valid ones."""
        unique = deduplicate(paths)
        known = self._list.keys()
        pending = [path for path in unique if str(path.resolve()).lower() not in known]
        duplicates = len(unique) - len(pending)

        if not pending:
            show_message(
                self,
                "Already added",
                "Every file you selected is already in the list.",
                "info",
            )
            return

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(0, PAD.md))
        self._progress.start(f"Reading {len(pending)} file{'s' if len(pending) != 1 else ''}…")

        def job(
            progress: CallbackProgress, token: CancellationToken
        ) -> tuple[list[PdfFileInfo], list[str]]:
            """Validate each candidate, collecting per-file problems."""
            collected: list[PdfFileInfo] = []
            problems: list[str] = []
            total = len(pending)
            for index, path in enumerate(pending, start=1):
                token.raise_if_cancelled()
                progress.update(index - 1, total, f"Reading {path.name}")
                try:
                    collected.append(inspect_pdf(path))
                except PdfToolkitError as exc:
                    problems.append(str(exc))
                progress.update(index, total, f"Checked {path.name}")
            return collected, problems

        self._token = self._import_runner.start(
            job,
            on_success=lambda result: self._import_done(result, duplicates),
            on_error=self._job_failed,
            on_progress=self._progress.update_progress,
            on_cancelled=self._job_cancelled,
        )

    def _import_done(
        self, result: tuple[list[PdfFileInfo], list[str]], duplicates: int
    ) -> None:
        """Add the successfully inspected files and report any problems."""
        collected, problems = result
        added = self._list.add_files(collected)
        self._finish_job()

        notes: list[str] = []
        if added:
            notes.append(f"Added {added} file{'s' if added != 1 else ''}")
        if duplicates:
            notes.append(f"{duplicates} already in the list")
        if problems:
            notes.append(f"{len(problems)} could not be read")
        self.controller.set_status(
            " · ".join(notes) or "Nothing was added",
            transient=True,
            tone="warning" if problems else "muted",
        )

        if problems:
            listed = "\n\n".join(problems[:6])
            extra = f"\n\n…and {len(problems) - 6} more." if len(problems) > 6 else ""
            show_message(
                self,
                "Some files were skipped",
                f"{len(problems)} file(s) could not be added:\n\n{listed}{extra}",
                "warning",
            )

    # ----------------------------------------------------------------- #
    # Ordering actions
    # ----------------------------------------------------------------- #
    def _list_action(self, method: str):
        """Return a callback invoking ``method`` on the list panel."""
        def action() -> None:
            if self._busy_guard() or not self._require_files(1):
                return
            getattr(self._list, method)()
            self.controller.set_status("Order updated", transient=True)

        return action

    def _sort(self) -> None:
        """Sort the list alphabetically."""
        if self._busy_guard() or not self._require_files(1):
            return
        self._list.sort_alphabetically()
        self.controller.set_status("Sorted A–Z", transient=True)

    def _reset_order(self) -> None:
        """Restore the order in which the files were originally added."""
        if self._busy_guard() or not self._require_files(1):
            return
        files = self._list.files
        files.sort(key=lambda info: self._added_order.get(info.key, 0))
        self._list.set_files(files)
        self.controller.set_status("Original order restored", transient=True)

    def _move(self, delta: int) -> None:
        """Move the selected row up (``-1``) or down (``1``)."""
        if self._busy_guard():
            return
        if not self._list.move_selected(delta):
            self.controller.set_status(
                "Select a file first, then use Move up / Move down", transient=True
            )

    def _remove_selected(self) -> None:
        """Remove the highlighted file."""
        if self._busy_guard():
            return
        if not self._list.remove_selected():
            self.controller.set_status("Select a file to remove", transient=True)

    def _clear_all(self) -> None:
        """Empty the list after confirmation."""
        if self._busy_guard() or not self._require_files(1):
            return
        if ask_confirm(
            self,
            "Clear the list?",
            f"Remove all {self._list.count} files from the list?\n"
            "The files themselves are not deleted.",
            confirm_label="Clear list",
            destructive=True,
        ):
            self._list.clear()
            self.controller.set_status("List cleared", transient=True)

    # ----------------------------------------------------------------- #
    # Merging
    # ----------------------------------------------------------------- #
    def _merge(self) -> None:
        """Validate, ask for a destination and run the merge."""
        if self._busy_guard() or not self._require_files(2):
            return

        files = self._list.files
        missing = [info.name for info in files if not info.path.exists()]
        if missing:
            show_error(
                self,
                PdfReadError(
                    "These files are no longer available:\n"
                    + "\n".join(f"  • {name}" for name in missing[:8])
                    + "\n\nRemove them from the list and try again."
                ),
            )
            return

        destination = ask_save_pdf(self, self._last_folder, DEFAULT_MERGE_FILENAME)
        if destination is None:
            self.controller.set_status("Merge cancelled", transient=True)
            return
        self._last_folder = destination.parent

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(0, PAD.md))
        self._progress.start(f"Merging {len(files)} documents…")

        def job(progress: CallbackProgress, token: CancellationToken) -> OperationResult:
            """Run the merge tool on the worker thread."""
            return self._tool.merge(files, destination, progress, token)

        self._token = self._merge_runner.start(
            job,
            on_success=self._merge_done,
            on_error=self._job_failed,
            on_progress=self._progress.update_progress,
            on_cancelled=self._job_cancelled,
        )

    def _merge_done(self, result: OperationResult) -> None:
        """Report success and offer to reveal the output."""
        self._progress.finish("Merge complete")
        self._finish_job()
        self.controller.set_status("Merge completed successfully", transient=True, tone="success")

        output = result.output_paths[0]
        if show_success(
            self,
            "Merge completed successfully",
            f"{result.message}\n\nSaved to:\n{output}",
        ):
            open_in_file_manager(output)

    # ----------------------------------------------------------------- #
    # Job lifecycle helpers
    # ----------------------------------------------------------------- #
    def _cancel_current(self) -> None:
        """Cancel whichever job is currently running."""
        self._import_runner.cancel()
        self._merge_runner.cancel()
        self.controller.set_status("Cancelling…", tone="warning")

    def _job_cancelled(self) -> None:
        """Restore the UI after a cancelled job."""
        self._finish_job()
        self.controller.set_status("Cancelled", transient=True, tone="warning")

    def _job_failed(self, error: Exception) -> None:
        """Restore the UI and show the error."""
        self._finish_job()
        self.controller.set_status("The operation failed", transient=True, tone="error")
        show_error(self, error)

    def _finish_job(self) -> None:
        """Hide the progress panel and re-enable the controls."""
        self._set_busy(False)
        self._progress.pack_forget()
        self._token = None

    def _set_busy(self, busy: bool) -> None:
        """Enable or disable the primary action while work is in flight."""
        self._merge_button.configure(state="disabled" if busy else "normal")

    def _busy_guard(self) -> bool:
        """Return ``True`` (and warn) when a job is already running."""
        if self._import_runner.busy or self._merge_runner.busy:
            self.controller.set_status("Please wait for the current task to finish", transient=True)
            return True
        return False

    def _require_files(self, minimum: int) -> bool:
        """Warn when fewer than ``minimum`` files are in the list."""
        if self._list.count >= minimum:
            return True
        if minimum <= 1:
            self.controller.set_status("Add some PDF files first", transient=True)
        else:
            show_message(
                self,
                "Not enough files",
                f"Select at least {minimum} PDF files to merge.\n"
                f"Currently in the list: {self._list.count}.",
                "warning",
            )
        return False

    # ----------------------------------------------------------------- #
    # Summary / state
    # ----------------------------------------------------------------- #
    @property
    def _added_order(self) -> dict[str, int]:
        """Insertion order of each file, used by *Reset order*."""
        for info in self._list.files:
            self._added_order_map.setdefault(info.key, len(self._added_order_map))
        return self._added_order_map

    def _refresh_summary(self) -> None:
        """Update the footer summary and the status bar detail."""
        count = self._list.count
        _ = self._added_order  # record insertion order for newly added files
        if count == 0:
            self._summary.configure(text="No files selected")
            self.controller.set_status_detail("")
            return
        text = (
            f"{count} file{'s' if count != 1 else ''}  ·  "
            f"{self._list.total_pages} pages  ·  "
            f"{human_readable_size(self._list.total_size)}"
        )
        self._summary.configure(text=text)
        self.controller.set_status_detail(text)

    # ----------------------------------------------------------------- #
    # View lifecycle
    # ----------------------------------------------------------------- #
    def on_show(self) -> None:
        """Bind shortcuts and refresh the summary."""
        super().on_show()
        self._refresh_summary()
        for sequence, action in SHORTCUTS:
            self.winfo_toplevel().bind(sequence, self._shortcut(action), add="+")

    def on_hide(self) -> None:
        """Release the shortcut bindings."""
        for sequence, _action in SHORTCUTS:
            try:
                self.winfo_toplevel().unbind(sequence)
            except Exception:  # noqa: BLE001 - binding may not exist
                pass
        self.controller.set_status_detail("")

    def can_leave(self) -> bool:
        """Block navigation while a merge is running."""
        if self._merge_runner.busy or self._import_runner.busy:
            return ask_confirm(
                self,
                "Task in progress",
                "A task is still running. Cancel it and leave this screen?",
                confirm_label="Cancel task",
                destructive=True,
            ) and self._force_stop()
        return True

    def _force_stop(self) -> bool:
        """Cancel every runner and allow navigation."""
        self._cancel_current()
        return True

    def _request_back(self) -> None:
        """Return to the home screen."""
        self.go_home()

    def _shortcut(self, action: str):
        """Map a shortcut name to its handler."""
        handlers = {
            "add_files": self._add_files,
            "add_folder": self._add_folder,
            "remove_selected": self._remove_selected,
            "move_up": lambda: self._move(-1),
            "move_down": lambda: self._move(1),
            "merge": self._merge,
            "back": self._request_back,
        }

        def handler(_event: object) -> str:
            handlers[action]()
            return "break"

        return handler

    def destroy(self) -> None:
        """Stop background workers before tearing the view down."""
        self._import_runner.shutdown()
        self._merge_runner.shutdown()
        self._list.shutdown()
        super().destroy()
