"""Convert view: turn Word documents into Markdown or PDF.

Handles all three shapes of the job in one screen - a single document, a batch
of documents, or a batch collated into one output file - because they share
the same inputs and only differ by two switches.
"""

from __future__ import annotations

import logging
from pathlib import Path

import customtkinter as ctk

from core.constants import DEFAULT_COMBINED_NAME
from core.docx_converter import (
    DocxConvertTool,
    OutputFormat,
    PdfEngine,
    available_pdf_engines,
)
from core.exceptions import PdfToolkitError
from core.models import DocxFileInfo, OperationResult
from core.progress import CallbackProgress, CancellationToken
from core.utils import deduplicate, discover_docx, human_readable_size, is_docx, open_in_file_manager
from core.validator import inspect_docx
from ui.base_view import BaseView
from ui.dialogs import (
    ask_confirm,
    ask_docx_files,
    ask_folder,
    show_error,
    show_message,
    show_success,
)
from ui.dnd import DND_AVAILABLE, enable_file_drop
from ui.file_list import FileListPanel
from ui.theme import (
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

_MODE_SEPARATE = "One file per document"
_MODE_COMBINED = "Combine into one file"

#: Shortcuts registered while this view is on screen.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("<Control-o>", "add_files"),
    ("<Control-O>", "add_files"),
    ("<Control-d>", "add_folder"),
    ("<Delete>", "remove_selected"),
    ("<Alt-Up>", "move_up"),
    ("<Alt-Down>", "move_down"),
    ("<Control-Return>", "convert"),
    ("<Escape>", "back"),
)


class ConvertView(BaseView):
    """Workflow for converting Word documents to Markdown or PDF."""

    view_name = "convert"
    status_hint = "Add Word documents to convert"

    def build(self) -> None:
        """Create every widget in the view."""
        self._tool = DocxConvertTool()
        self._import_runner: JobRunner[tuple[list[DocxFileInfo], list[str]]] = JobRunner(self)
        self._convert_runner: JobRunner[OperationResult] = JobRunner(self)
        self._last_folder: Path | None = None
        self._output_folder: Path | None = None
        self._added_order_map: dict[str, int] = {}

        self._format = ctk.StringVar(value=OutputFormat.MARKDOWN.label)
        self._mode = ctk.StringVar(value=_MODE_SEPARATE)
        self._recursive = ctk.BooleanVar(value=True)
        self._mirror = ctk.BooleanVar(value=True)
        self._overwrite = ctk.BooleanVar(value=False)
        self._engines = available_pdf_engines()
        self._engine = ctk.StringVar(value=PdfEngine.AUTO.label)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.xl, pady=PAD.lg)

        PageHeader(
            container,
            "Convert Word files",
            "Turn .docx documents into Markdown or PDF - one at a time, in bulk, "
            "or collated into a single file.",
            on_back=self.go_home,
        ).pack(fill="x", pady=(0, PAD.md))

        self._build_sources(container)

        # Footer and options are packed against the bottom first so they keep
        # their space however short the window is.
        self._progress = ProgressPanel(container, on_cancel=self._cancel_current)
        self._build_footer(container)
        self._build_options(container)

        self._build_toolbar(container)
        self._list = FileListPanel(
            container,
            on_change=self._refresh_summary,
            empty_widget_factory=self._empty_state,
            show_thumbnails=False,
        )
        self._list.pack(fill="both", expand=True, pady=(PAD.sm, PAD.md))

        if not enable_file_drop(self, self._handle_drop):
            logger.debug("Drag-and-drop not registered for the convert view.")
        self._sync_state()

    # ----------------------------------------------------------------- #
    # Layout sections
    # ----------------------------------------------------------------- #
    def _build_sources(self, parent: ctk.CTkFrame) -> None:
        """Input pickers and the recursive-scan switch."""
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

        ctk.CTkSwitch(
            row,
            text="Include sub-folders",
            variable=self._recursive,
            font=fonts().small,
            text_color=COLORS.text_muted,
            progress_color=COLORS.accent,
            button_color=COLORS.text_muted,
        ).pack(side="left", padx=PAD.md)

        hint = (
            "…or drag .docx files here"
            if DND_AVAILABLE
            else "Tip: Ctrl+O for files, Ctrl+D for a folder"
        )
        ctk.CTkLabel(row, text=hint, font=fonts().small, text_color=COLORS.text_disabled).pack(
            side="left"
        )

    def _build_toolbar(self, parent: ctk.CTkFrame) -> None:
        """Ordering controls - the order matters when collating."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(PAD.md, 0))

        ctk.CTkLabel(
            bar, text="Order", font=fonts().small_bold, text_color=COLORS.text_muted
        ).pack(side="left", padx=(PAD.xs, PAD.sm))

        for label, command in (
            ("Sort A–Z", self._sort),
            ("Reverse", self._reverse),
            ("Reset order", self._reset_order),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
        ):
            ctk.CTkButton(bar, text=label, width=94, command=command, **small_button_style()).pack(
                side="left", padx=(0, PAD.xs)
            )

        ctk.CTkButton(
            bar, text="Remove", width=94, command=self._remove_selected, **small_button_style()
        ).pack(side="right")
        ctk.CTkButton(
            bar, text="Clear all", width=94, command=self._clear_all, **small_button_style()
        ).pack(side="right", padx=(0, PAD.xs))

    def _build_options(self, parent: ctk.CTkFrame) -> None:
        """Output format, output mode, destination folder and switches."""
        card = Card(parent)
        card.pack(side="bottom", fill="x", pady=(0, PAD.md))

        grid = card.body
        grid.grid_columnconfigure(0, weight=1, uniform="options")
        grid.grid_columnconfigure(1, weight=1, uniform="options")

        # --- Convert to ------------------------------------------------ #
        left = ctk.CTkFrame(grid, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD.md))

        ctk.CTkLabel(
            left, text="Convert to", font=fonts().small_bold, text_color=COLORS.text_muted, anchor="w"
        ).pack(fill="x")

        ctk.CTkSegmentedButton(
            left,
            values=[fmt.label for fmt in OutputFormat],
            variable=self._format,
            command=lambda _value: self._sync_state(),
            font=fonts().body,
            selected_color=COLORS.accent,
            selected_hover_color=COLORS.accent_hover,
            unselected_color=COLORS.surface_alt,
            unselected_hover_color=COLORS.surface_hover,
            fg_color=COLORS.surface_alt,
            height=36,
        ).pack(fill="x", pady=(PAD.xs, 0))

        self._format_hint = ctk.CTkLabel(
            left, text="", font=fonts().small, text_color=COLORS.text_disabled, anchor="w"
        )
        self._format_hint.pack(fill="x", pady=(PAD.xs, 0))

        engine_row = ctk.CTkFrame(left, fg_color="transparent")
        engine_row.pack(fill="x", pady=(PAD.sm, 0))

        ctk.CTkLabel(
            engine_row,
            text="PDF engine",
            font=fonts().small_bold,
            text_color=COLORS.text_muted,
            anchor="w",
            width=80,
        ).pack(side="left")

        self._engine_menu = ctk.CTkOptionMenu(
            engine_row,
            values=self._engine_choices(),
            variable=self._engine,
            command=lambda _value: self._sync_state(),
            font=fonts().small,
            fg_color=COLORS.surface_alt,
            button_color=COLORS.neutral_button,
            button_hover_color=COLORS.neutral_button_hover,
            text_color=COLORS.text,
            height=30,
        )
        self._engine_menu.pack(side="left", fill="x", expand=True)

        # --- Output mode ----------------------------------------------- #
        right = ctk.CTkFrame(grid, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(
            right, text="Output", font=fonts().small_bold, text_color=COLORS.text_muted, anchor="w"
        ).pack(fill="x")

        ctk.CTkSegmentedButton(
            right,
            values=[_MODE_SEPARATE, _MODE_COMBINED],
            variable=self._mode,
            command=lambda _value: self._sync_state(),
            font=fonts().body,
            selected_color=COLORS.accent,
            selected_hover_color=COLORS.accent_hover,
            unselected_color=COLORS.surface_alt,
            unselected_hover_color=COLORS.surface_hover,
            fg_color=COLORS.surface_alt,
            height=36,
        ).pack(fill="x", pady=(PAD.xs, 0))

        name_row = ctk.CTkFrame(right, fg_color="transparent")
        name_row.pack(fill="x", pady=(PAD.xs, 0))

        ctk.CTkLabel(
            name_row,
            text="File name",
            font=fonts().small_bold,
            text_color=COLORS.text_muted,
            anchor="w",
            width=80,
        ).pack(side="left")

        self._name_entry = ctk.CTkEntry(
            name_row, placeholder_text=DEFAULT_COMBINED_NAME, **entry_style()
        )
        self._name_entry.configure(height=30)
        self._name_entry.pack(side="left", fill="x", expand=True)

        switches = ctk.CTkFrame(right, fg_color="transparent")
        switches.pack(fill="x", pady=(PAD.sm, 0))

        self._mirror_switch = ctk.CTkSwitch(
            switches,
            text="Recreate sub-folders",
            variable=self._mirror,
            font=fonts().small,
            text_color=COLORS.text_muted,
            progress_color=COLORS.accent,
            button_color=COLORS.text_muted,
        )
        self._mirror_switch.pack(side="left")

        ctk.CTkSwitch(
            switches,
            text="Replace existing files",
            variable=self._overwrite,
            font=fonts().small,
            text_color=COLORS.text_muted,
            progress_color=COLORS.accent,
            button_color=COLORS.text_muted,
        ).pack(side="left", padx=PAD.md)

        # --- Destination ------------------------------------------------ #
        destination = ctk.CTkFrame(grid, fg_color="transparent")
        destination.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(PAD.md, 0))

        ctk.CTkButton(
            destination,
            text="📂   Output folder",
            width=170,
            command=self._choose_output,
            **secondary_button_style(),
        ).pack(side="left")

        self._output_label = ctk.CTkLabel(
            destination,
            text="Defaults to the folder of the first document",
            font=fonts().small,
            text_color=COLORS.text_muted,
            anchor="w",
        )
        self._output_label.pack(side="left", fill="x", expand=True, padx=PAD.md)

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        """Summary line and the primary action."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(side="bottom", fill="x")

        self._summary = ctk.CTkLabel(
            footer, text="", font=fonts().body, text_color=COLORS.text_muted, anchor="w"
        )
        self._summary.pack(side="left", fill="x", expand=True)

        self._convert_button = ctk.CTkButton(
            footer, text="Convert", width=200, command=self._convert, **primary_button_style()
        )
        self._convert_button.pack(side="right")

    @staticmethod
    def _empty_state(master: ctk.CTkBaseClass) -> ctk.CTkBaseClass:
        """Placeholder shown while the list is empty."""
        message = (
            "Drag .docx files or a folder here, or use the buttons above."
            if DND_AVAILABLE
            else "Use Select folder or Select files above to add documents."
        )
        return EmptyState(master, "📝", "No Word documents selected yet", message)

    def _engine_choices(self) -> list[str]:
        """Engine names offered in the drop-down, best first."""
        return [PdfEngine.AUTO.label] + [engine.label for engine in self._engines]

    # ----------------------------------------------------------------- #
    # Adding files
    # ----------------------------------------------------------------- #
    def _add_files(self) -> None:
        """Open the native multi-select picker and import the result."""
        if self._busy_guard():
            return
        paths = ask_docx_files(self, self._last_folder)
        if not paths:
            self.controller.set_status("No files selected", transient=True)
            return
        self._last_folder = paths[0].parent
        self._import(paths, base_folder=None)

    def _add_folder(self) -> None:
        """Open the native folder picker and import every document inside."""
        if self._busy_guard():
            return
        folder = ask_folder(self, "Select a folder containing Word documents", self._last_folder)
        if folder is None:
            self.controller.set_status("No folder selected", transient=True)
            return
        self._last_folder = folder

        recursive = bool(self._recursive.get())
        documents = discover_docx(folder, recursive=recursive)
        if not documents:
            show_message(
                self,
                "No Word documents found",
                f"'{folder.name}' contains no .docx files"
                + ("." if recursive else ".\n\nSub-folders were not searched.")
                + "\n\nOlder .doc files must be saved as .docx in Word first.",
                "warning",
            )
            return
        self._import(documents, base_folder=folder)

    def _handle_drop(self, paths: list[Path]) -> None:
        """Handle documents or folders dropped onto the window."""
        if self._busy_guard():
            return
        recursive = bool(self._recursive.get())
        candidates: list[tuple[Path, Path | None]] = []
        for path in paths:
            if path.is_dir():
                candidates.extend(
                    (found, path) for found in discover_docx(path, recursive=recursive)
                )
            elif is_docx(path):
                candidates.append((path, None))

        if not candidates:
            show_message(
                self,
                "Nothing to add",
                "None of the dropped items are Word .docx documents.",
                "warning",
            )
            return

        # Group by the folder each file came from so relative paths stay right.
        by_base: dict[Path | None, list[Path]] = {}
        for file_path, base in candidates:
            by_base.setdefault(base, []).append(file_path)
        for base, files in by_base.items():
            self._import(files, base_folder=base, silent_duplicates=True)

    def _import(
        self,
        paths: list[Path],
        base_folder: Path | None,
        silent_duplicates: bool = False,
    ) -> None:
        """Inspect ``paths`` on a worker thread and add the valid ones."""
        unique = deduplicate(paths)
        known = self._list.keys()
        pending = [path for path in unique if str(path.resolve()).lower() not in known]
        duplicates = len(unique) - len(pending)

        if not pending:
            if not silent_duplicates:
                show_message(
                    self,
                    "Already added",
                    "Every document you selected is already in the list.",
                    "info",
                )
            return

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(0, PAD.md))
        self._progress.start(f"Reading {len(pending)} document{'s' if len(pending) != 1 else ''}…")

        def job(
            progress: CallbackProgress, token: CancellationToken
        ) -> tuple[list[DocxFileInfo], list[str]]:
            """Validate each candidate, collecting per-file problems."""
            collected: list[DocxFileInfo] = []
            problems: list[str] = []
            total = len(pending)
            for index, path in enumerate(pending, start=1):
                token.raise_if_cancelled()
                progress.update(index - 1, total, f"Reading {path.name}")
                try:
                    collected.append(inspect_docx(path, base_folder=base_folder))
                except PdfToolkitError as exc:
                    problems.append(str(exc))
                progress.update(index, total, f"Checked {path.name}")
            return collected, problems

        self._import_runner.start(
            job,
            on_success=lambda result: self._import_done(result, duplicates),
            on_error=self._job_failed,
            on_progress=self._progress.update_progress,
            on_cancelled=self._job_cancelled,
        )

    def _import_done(self, result: tuple[list[DocxFileInfo], list[str]], duplicates: int) -> None:
        """Add the successfully inspected documents and report problems."""
        collected, problems = result
        added = self._list.add_files(collected)
        if self._output_folder is None and collected:
            self._output_folder = collected[0].path.parent
        self._finish_job()

        notes: list[str] = []
        if added:
            notes.append(f"Added {added} document{'s' if added != 1 else ''}")
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
                "Some documents were skipped",
                f"{len(problems)} file(s) could not be added:\n\n{listed}{extra}",
                "warning",
            )

    # ----------------------------------------------------------------- #
    # Ordering actions
    # ----------------------------------------------------------------- #
    def _sort(self) -> None:
        """Sort the list alphabetically."""
        if self._busy_guard() or not self._require_files():
            return
        self._list.sort_alphabetically()
        self.controller.set_status("Sorted A–Z", transient=True)

    def _reverse(self) -> None:
        """Reverse the current order."""
        if self._busy_guard() or not self._require_files():
            return
        self._list.reverse()
        self.controller.set_status("Order reversed", transient=True)

    def _reset_order(self) -> None:
        """Restore the order in which the documents were added."""
        if self._busy_guard() or not self._require_files():
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
                "Select a document first, then use Move up / Move down", transient=True
            )

    def _remove_selected(self) -> None:
        """Remove the highlighted document."""
        if self._busy_guard():
            return
        if not self._list.remove_selected():
            self.controller.set_status("Select a document to remove", transient=True)

    def _clear_all(self) -> None:
        """Empty the list after confirmation."""
        if self._busy_guard() or not self._require_files():
            return
        if ask_confirm(
            self,
            "Clear the list?",
            f"Remove all {self._list.count} documents from the list?\n"
            "The files themselves are not deleted.",
            confirm_label="Clear list",
            destructive=True,
        ):
            self._list.clear()
            self.controller.set_status("List cleared", transient=True)

    # ----------------------------------------------------------------- #
    # Options / state
    # ----------------------------------------------------------------- #
    @property
    def _output_format(self) -> OutputFormat:
        """The selected output format."""
        label = self._format.get()
        return next(fmt for fmt in OutputFormat if fmt.label == label)

    @property
    def _combine(self) -> bool:
        """``True`` when everything is collated into a single file."""
        return self._mode.get() == _MODE_COMBINED

    @property
    def _selected_engine(self) -> PdfEngine:
        """The chosen PDF engine."""
        label = self._engine.get()
        return next((engine for engine in PdfEngine if engine.label == label), PdfEngine.AUTO)

    def _choose_output(self) -> None:
        """Pick the destination folder."""
        if self._busy_guard():
            return
        folder = ask_folder(self, "Select an output folder", self._output_folder or self._last_folder)
        if folder is None:
            return
        self._output_folder = folder
        self._sync_state()

    def _sync_state(self) -> None:
        """Enable the controls that apply to the current selections."""
        is_pdf = self._output_format is OutputFormat.PDF
        combine = self._combine

        self._format_hint.configure(text=self._output_format.description)
        self._engine_menu.configure(state="normal" if is_pdf else "disabled")
        self._name_entry.configure(state="normal" if combine else "disabled")
        self._mirror_switch.configure(state="disabled" if combine else "normal")

        if self._output_folder is not None:
            self._output_label.configure(text=str(self._output_folder), text_color=COLORS.text)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """Update the footer summary and the status bar detail."""
        count = self._list.count
        _ = self._added_order  # record insertion order for newly added files
        if count == 0:
            self._summary.configure(text="No documents selected")
            self.controller.set_status_detail("")
            return

        noun = "document" if count == 1 else "documents"
        if self._combine:
            target = f"1 {self._output_format.label} file"
        else:
            target = f"{count} {self._output_format.label} file{'s' if count != 1 else ''}"
        text = (
            f"{count} {noun}  ·  {human_readable_size(self._list.total_size)}  →  {target}"
        )
        self._summary.configure(text=text)
        self.controller.set_status_detail(f"{count} {noun}")

    @property
    def _added_order(self) -> dict[str, int]:
        """Insertion order of each document, used by *Reset order*."""
        for info in self._list.files:
            self._added_order_map.setdefault(info.key, len(self._added_order_map))
        return self._added_order_map

    def _require_files(self) -> bool:
        """Warn when the list is empty."""
        if self._list.count:
            return True
        self.controller.set_status("Add some Word documents first", transient=True)
        return False

    # ----------------------------------------------------------------- #
    # Converting
    # ----------------------------------------------------------------- #
    def _convert(self) -> None:
        """Validate the form, confirm if needed, then run the conversion."""
        if self._busy_guard():
            return
        if not self._list.count:
            show_message(
                self,
                "Nothing to convert",
                "Add at least one Word document to the list first.",
                "warning",
            )
            return

        files: list[DocxFileInfo] = self._list.files  # type: ignore[assignment]
        missing = [info.name for info in files if not info.path.exists()]
        if missing:
            show_error(
                self,
                PdfToolkitError(
                    "These documents are no longer available:\n"
                    + "\n".join(f"  • {name}" for name in missing[:8])
                    + "\n\nRemove them from the list and try again."
                ),
            )
            return

        folder = self._output_folder or files[0].path.parent
        output_format = self._output_format
        combine = self._combine
        combined_name = self._name_entry.get().strip() or DEFAULT_COMBINED_NAME
        mirror = bool(self._mirror.get())
        overwrite = bool(self._overwrite.get())
        engine = self._selected_engine

        if not combine and len(files) > 50 and not ask_confirm(
            self,
            "Create many files?",
            f"This will create {len(files)} files in:\n{folder}\n\nContinue?",
            confirm_label="Convert",
        ):
            return

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(0, PAD.md))
        self._progress.start(
            f"Converting {len(files)} document{'s' if len(files) != 1 else ''}…"
        )

        def job(progress: CallbackProgress, token: CancellationToken) -> OperationResult:
            """Run the converter on the worker thread."""
            return self._tool.convert(
                files=files,
                output_folder=folder,
                output_format=output_format,
                combine=combine,
                combined_name=combined_name,
                mirror_structure=mirror,
                overwrite=overwrite,
                engine=engine,
                progress=progress,
                token=token,
            )

        self._convert_runner.start(
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
        self.controller.set_status(
            "Conversion completed successfully", transient=True, tone="success"
        )

        preview = "\n".join(f"  • {path.name}" for path in result.output_paths[:5])
        if len(result.output_paths) > 5:
            preview += f"\n  …and {len(result.output_paths) - 5} more"
        body = f"{result.message}\n\nSaved to:\n{result.output_folder}\n\n{preview}"
        if result.warnings:
            body += f"\n\n{len(result.warnings)} document(s) were skipped - see the log for details."

        if show_success(self, "Conversion completed successfully", body):
            open_in_file_manager(result.output_folder)

    # ----------------------------------------------------------------- #
    # Job lifecycle helpers
    # ----------------------------------------------------------------- #
    def _cancel_current(self) -> None:
        """Cancel whichever job is running."""
        self._import_runner.cancel()
        self._convert_runner.cancel()
        self.controller.set_status("Cancelling…", tone="warning")

    def _job_cancelled(self) -> None:
        """Restore the UI after a cancelled job."""
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
        if self._import_runner.busy or self._convert_runner.busy:
            self.controller.set_status("Please wait for the current task to finish", transient=True)
            return True
        return False

    # ----------------------------------------------------------------- #
    # View lifecycle
    # ----------------------------------------------------------------- #
    def on_show(self) -> None:
        """Bind shortcuts and refresh derived labels."""
        super().on_show()
        self._sync_state()
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
        """Block navigation while a conversion is running."""
        if self._import_runner.busy or self._convert_runner.busy:
            if not ask_confirm(
                self,
                "Task in progress",
                "A task is still running. Cancel it and leave this screen?",
                confirm_label="Cancel task",
                destructive=True,
            ):
                return False
            self._cancel_current()
        return True

    def _shortcut(self, action: str):
        """Map a shortcut name to its handler."""
        handlers = {
            "add_files": self._add_files,
            "add_folder": self._add_folder,
            "remove_selected": self._remove_selected,
            "move_up": lambda: self._move(-1),
            "move_down": lambda: self._move(1),
            "convert": self._convert,
            "back": self.go_home,
        }

        def handler(_event: object) -> str:
            handlers[action]()
            return "break"

        return handler

    def destroy(self) -> None:
        """Stop background workers before tearing the view down."""
        self._import_runner.shutdown()
        self._convert_runner.shutdown()
        self._list.shutdown()
        super().destroy()
