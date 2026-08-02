"""Split view: choose a PDF, a split mode and an output folder."""

from __future__ import annotations

import logging
from pathlib import Path

import customtkinter as ctk

from core.constants import PREVIEW_SIZE
from core.exceptions import PdfToolkitError, ValidationError
from core.models import OperationResult, PdfFileInfo
from core.progress import CallbackProgress, CancellationToken
from core.splitter import PdfSplitTool, SplitMode
from core.thumbnails import thumbnail_service
from core.utils import is_pdf, open_in_file_manager
from core.validator import inspect_pdf, parse_page_list, parse_page_ranges
from ui.base_view import BaseView
from ui.dialogs import ask_confirm, ask_folder, ask_pdf_file, show_error, show_message, show_success
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
from ui.widgets import Card, PageHeader, ProgressPanel
from ui.workers import JobRunner

logger = logging.getLogger(__name__)

#: Example text shown under the page-selection entry for each mode.
_PLACEHOLDERS: dict[SplitMode, str] = {
    SplitMode.EVERY_PAGE: "Not needed for this mode",
    SplitMode.RANGES: "1-5, 6-10, 11-20",
    SplitMode.EXTRACT: "1, 4, 7, 10",
}


class SplitView(BaseView):
    """Workflow for breaking a single PDF into smaller documents."""

    view_name = "split"
    status_hint = "Choose a PDF to split"

    def build(self) -> None:
        """Create every widget in the view."""
        self._tool = PdfSplitTool()
        self._runner: JobRunner[OperationResult] = JobRunner(self)
        self._source: PdfFileInfo | None = None
        self._output_folder: Path | None = None
        self._last_folder: Path | None = None
        self._preview_ctk_image: ctk.CTkImage | None = None
        self._mode = ctk.StringVar(value=SplitMode.EVERY_PAGE.value)
        self._overwrite = ctk.BooleanVar(value=False)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.xl, pady=PAD.lg)

        PageHeader(
            container,
            "Split PDF",
            "Break one document into single pages, page ranges, or a custom selection.",
            on_back=self.go_home,
        ).pack(fill="x", pady=(0, PAD.md))

        # The footer is packed before the body so it always keeps its space,
        # however small the window gets.
        self._progress = ProgressPanel(container, on_cancel=self._cancel)
        self._build_footer(container)

        body = ctk.CTkFrame(container, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3, uniform="split")
        body.grid_columnconfigure(1, weight=2, uniform="split")
        body.grid_rowconfigure(0, weight=1)

        # Scrollable so every step stays reachable on short screens.
        left = ctk.CTkScrollableFrame(
            body,
            fg_color="transparent",
            scrollbar_button_color=COLORS.border_strong,
            scrollbar_button_hover_color=COLORS.accent,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD.md))
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_source(left)
        self._build_modes(left)
        # Step 3 lives in the right-hand column so all three steps stay visible
        # at the default window size.
        self._build_output(right)
        self._build_preview(right)

        enable_file_drop(self, self._handle_drop)
        self._sync_state()

    # ----------------------------------------------------------------- #
    # Layout sections
    # ----------------------------------------------------------------- #
    def _build_source(self, parent: ctk.CTkFrame) -> None:
        """Source-file chooser and its metadata line."""
        card = Card(parent, "1.  Choose the PDF")
        card.pack(fill="x")

        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkButton(
            row, text="🗎   Select PDF", width=170, command=self._choose_source, **secondary_button_style()
        ).pack(side="left")

        self._source_label = ctk.CTkLabel(
            row,
            text="No file selected",
            font=fonts().body,
            text_color=COLORS.text_muted,
            anchor="w",
            justify="left",
        )
        self._source_label.pack(side="left", fill="x", expand=True, padx=PAD.md)

    def _build_modes(self, parent: ctk.CTkFrame) -> None:
        """Radio cards for the three split strategies plus the page entry."""
        card = Card(parent, "2.  Choose how to split")
        card.pack(fill="x", pady=PAD.md)

        for mode in SplitMode:
            option = ctk.CTkFrame(
                card.body, fg_color=COLORS.surface_alt, corner_radius=CARD_RADIUS
            )
            option.pack(fill="x", pady=(0, PAD.xs))

            ctk.CTkRadioButton(
                option,
                text=mode.label,
                value=mode.value,
                variable=self._mode,
                command=self._sync_state,
                font=fonts().body_bold,
                text_color=COLORS.text,
                fg_color=COLORS.accent,
                hover_color=COLORS.accent_hover,
                border_color=COLORS.border_strong,
                radiobutton_width=18,
                radiobutton_height=18,
            ).pack(anchor="w", padx=PAD.md, pady=(PAD.sm, 0))

            ctk.CTkLabel(
                option,
                text=mode.description,
                font=fonts().small,
                text_color=COLORS.text_muted,
                anchor="w",
                justify="left",
                wraplength=430,
            ).pack(anchor="w", padx=(PAD.xxl, PAD.md), pady=(0, PAD.sm))

        entry_row = ctk.CTkFrame(card.body, fg_color="transparent")
        entry_row.pack(fill="x", pady=(PAD.sm, 0))

        ctk.CTkLabel(
            entry_row, text="Pages", font=fonts().small_bold, text_color=COLORS.text_muted, anchor="w"
        ).pack(fill="x")

        self._pages_entry = ctk.CTkEntry(
            entry_row, placeholder_text=_PLACEHOLDERS[SplitMode.RANGES], **entry_style()
        )
        self._pages_entry.pack(fill="x", pady=(PAD.xs, 0))
        self._pages_entry.bind("<KeyRelease>", lambda _event: self._validate_selection())
        self._pages_entry.bind("<Return>", lambda _event: self._split())

        self._hint = ctk.CTkLabel(
            entry_row, text="", font=fonts().small, text_color=COLORS.text_disabled, anchor="w"
        )
        self._hint.pack(fill="x", pady=(PAD.xs, 0))

    def _build_output(self, parent: ctk.CTkFrame) -> None:
        """Destination folder chooser and the overwrite switch."""
        card = Card(parent, "3.  Choose where to save")
        card.pack(side="bottom", fill="x", pady=(PAD.md, 0))

        ctk.CTkButton(
            card.body,
            text="📁   Output folder",
            command=self._choose_output,
            **secondary_button_style(),
        ).pack(fill="x")

        self._output_label = ctk.CTkLabel(
            card.body,
            text="Defaults to the folder of the source PDF",
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

    def _build_preview(self, parent: ctk.CTkFrame) -> None:
        """First-page preview and document details."""
        card = Card(parent, "Preview")
        card.pack(fill="both", expand=True)

        # Details are packed first, against the bottom, so they are never
        # squeezed out by the image when the window is short.
        self._details = ctk.CTkLabel(
            card.body,
            text="Select a PDF to see its details here.",
            font=fonts().small,
            text_color=COLORS.text_muted,
            justify="left",
            anchor="w",
            wraplength=280,
        )
        self._details.pack(side="bottom", fill="x", pady=(PAD.md, 0))

        self._preview_image = ctk.CTkLabel(
            card.body,
            text="",
            fg_color=COLORS.surface_alt,
            corner_radius=8,
            width=PREVIEW_SIZE[0],
            height=PREVIEW_SIZE[1],
        )
        self._preview_image.pack(fill="both", expand=True)
        self._preview_image.bind("<Configure>", self._fit_preview)

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        """Summary text and the primary split action."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(PAD.md, 0))

        self._summary = ctk.CTkLabel(
            footer, text="", font=fonts().body, text_color=COLORS.text_muted, anchor="w"
        )
        self._summary.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            footer, text="Reset", width=110, command=self._reset, **small_button_style()
        ).pack(side="right", padx=(PAD.sm, 0))

        self._split_button = ctk.CTkButton(
            footer, text="Split PDF", width=200, command=self._split, **primary_button_style()
        )
        self._split_button.pack(side="right")

    # ----------------------------------------------------------------- #
    # Source selection
    # ----------------------------------------------------------------- #
    def _choose_source(self) -> None:
        """Pick the PDF to split with the native file dialog."""
        if self._busy_guard():
            return
        path = ask_pdf_file(self, self._last_folder)
        if path is None:
            self.controller.set_status("No file selected", transient=True)
            return
        self._load_source(path)

    def _handle_drop(self, paths: list[Path]) -> None:
        """Accept a single PDF dropped onto the window."""
        if self._busy_guard():
            return
        pdfs = [path for path in paths if is_pdf(path) and path.is_file()]
        if not pdfs:
            show_message(self, "Nothing to open", "Drop a single PDF file here.", "warning")
            return
        if len(pdfs) > 1:
            self.controller.set_status(
                f"Using '{pdfs[0].name}' - the split tool works on one file at a time",
                transient=True,
                tone="warning",
            )
        self._load_source(pdfs[0])

    def _load_source(self, path: Path) -> None:
        """Validate ``path`` and populate the source, preview and defaults."""
        try:
            info = inspect_pdf(path)
        except PdfToolkitError as exc:
            show_error(self, exc)
            return

        self._source = info
        self._last_folder = path.parent
        if self._output_folder is None:
            self._output_folder = path.parent

        self._source_label.configure(
            text=f"{info.name}\n{info.page_label}  ·  {info.size_label}  ·  {info.modified_label}",
            text_color=COLORS.text,
        )
        self._details.configure(
            text=(
                f"Name:  {info.name}\n"
                f"Pages:  {info.page_count}\n"
                f"Size:  {info.size_label}\n"
                f"Modified:  {info.modified_label}\n"
                f"Folder:  {info.path.parent}"
            )
        )
        self._update_preview(info)
        self._sync_state()
        self.controller.set_status(f"Loaded '{info.name}'", transient=True, tone="success")

    def _update_preview(self, info: PdfFileInfo) -> None:
        """Render and show the first page of ``info``."""
        try:
            image = thumbnail_service.get(info.path, PREVIEW_SIZE)
            self._preview_ctk_image = ctk.CTkImage(
                light_image=image, dark_image=image, size=PREVIEW_SIZE
            )
            self._preview_image.configure(image=self._preview_ctk_image, text="")
            self._fit_preview()
        except Exception:  # noqa: BLE001 - preview is decorative
            logger.debug("Preview rendering failed for %s", info.path, exc_info=True)

    def _fit_preview(self, _event: object = None) -> None:
        """Scale the preview so it always fits the space the card can spare."""
        image = getattr(self, "_preview_ctk_image", None)
        if image is None:
            return
        available_h = max(self._preview_image.winfo_height() - PAD.sm, 60)
        available_w = max(self._preview_image.winfo_width() - PAD.sm, 60)
        ratio = PREVIEW_SIZE[0] / PREVIEW_SIZE[1]
        height = min(available_h, PREVIEW_SIZE[1])
        width = min(int(height * ratio), available_w)
        height = int(width / ratio)
        if abs(width - image.cget("size")[0]) > 4:
            image.configure(size=(width, height))

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
    @property
    def _current_mode(self) -> SplitMode:
        """The currently selected split mode."""
        return SplitMode(self._mode.get())

    def _sync_state(self) -> None:
        """Enable/disable the page entry and refresh all derived labels."""
        mode = self._current_mode
        needs_pages = mode is not SplitMode.EVERY_PAGE

        self._pages_entry.configure(
            state="normal" if needs_pages else "disabled",
            placeholder_text=_PLACEHOLDERS[mode],
        )
        if self._output_folder is not None:
            self._output_label.configure(text=str(self._output_folder), text_color=COLORS.text)

        self._validate_selection()
        self._refresh_summary()

    def _validate_selection(self) -> str | None:
        """Validate the page expression, updating the hint line.

        Returns:
            The error message, or ``None`` when the input is acceptable.
        """
        mode = self._current_mode
        if mode is SplitMode.EVERY_PAGE:
            pages = self._source.page_count if self._source else 0
            self._hint.configure(
                text=f"Will create {pages} files, one per page." if pages else "",
                text_color=COLORS.text_disabled,
            )
            return None

        text = self._pages_entry.get().strip()
        if not text:
            self._hint.configure(
                text=f"Example: {_PLACEHOLDERS[mode]}", text_color=COLORS.text_disabled
            )
            return "Enter the pages you want to keep."
        if self._source is None:
            self._hint.configure(text="Select a PDF first.", text_color=COLORS.text_disabled)
            return "Select a PDF first."

        try:
            if mode is SplitMode.RANGES:
                ranges = parse_page_ranges(text, self._source.page_count)
                total = sum(end - start + 1 for start, end in ranges)
                summary = (
                    f"✓ {len(ranges)} file{'s' if len(ranges) != 1 else ''} "
                    f"covering {total} pages."
                )
            else:
                pages = parse_page_list(text, self._source.page_count)
                summary = f"✓ 1 file containing {len(pages)} pages."
        except ValidationError as exc:
            self._hint.configure(text=str(exc).replace("\n", "  "), text_color=COLORS.danger)
            return str(exc)

        self._hint.configure(text=summary, text_color=COLORS.success)
        return None

    def _refresh_summary(self) -> None:
        """Update the footer summary line."""
        if self._source is None:
            self._summary.configure(text="No file selected")
            return
        self._summary.configure(
            text=f"{self._source.name}  ·  {self._source.page_label}  →  {self._current_mode.label}"
        )

    def _reset(self) -> None:
        """Clear the whole form."""
        if self._busy_guard():
            return
        self._source = None
        self._output_folder = None
        self._pages_entry.delete(0, "end")
        self._mode.set(SplitMode.EVERY_PAGE.value)
        self._overwrite.set(False)
        self._source_label.configure(text="No file selected", text_color=COLORS.text_muted)
        self._output_label.configure(
            text="Defaults to the folder of the source PDF", text_color=COLORS.text_muted
        )
        self._details.configure(text="Select a PDF to see its details here.")
        self._preview_ctk_image = None
        self._preview_image.configure(image=None, text="")
        self._sync_state()
        self.controller.set_status("Reset", transient=True)

    # ----------------------------------------------------------------- #
    # Splitting
    # ----------------------------------------------------------------- #
    def _split(self) -> None:
        """Validate everything, confirm, then run the split job."""
        if self._busy_guard():
            return
        if self._source is None:
            show_message(self, "No file selected", "Choose the PDF you want to split first.", "warning")
            return
        if not self._source.path.exists():
            show_error(
                self,
                PdfToolkitError(f"'{self._source.name}' is no longer available. Select it again."),
            )
            return

        error = self._validate_selection()
        if error:
            show_message(self, "Check the page selection", error, "warning")
            self._pages_entry.focus_set()
            return

        folder = self._output_folder or self._source.path.parent
        selection = self._pages_entry.get().strip()
        mode = self._current_mode

        expected = self._expected_output_count(mode, selection)
        if expected > 50 and not ask_confirm(
            self,
            "Create many files?",
            f"This will create {expected} PDF files in:\n{folder}\n\nContinue?",
            confirm_label="Create files",
        ):
            return

        self._set_busy(True)
        self._progress.pack(side="bottom", fill="x", pady=(PAD.md, 0))
        self._progress.start("Splitting document…")

        source_path = self._source.path
        overwrite = bool(self._overwrite.get())

        def job(progress: CallbackProgress, token: CancellationToken) -> OperationResult:
            """Run the split tool on the worker thread."""
            return self._tool.split(
                source=source_path,
                output_folder=folder,
                mode=mode,
                selection=selection,
                overwrite=overwrite,
                progress=progress,
                token=token,
            )

        self._runner.start(
            job,
            on_success=self._split_done,
            on_error=self._job_failed,
            on_progress=self._progress.update_progress,
            on_cancelled=self._job_cancelled,
        )

    def _expected_output_count(self, mode: SplitMode, selection: str) -> int:
        """Number of files the current settings would produce."""
        if self._source is None:
            return 0
        if mode is SplitMode.EVERY_PAGE:
            return self._source.page_count
        if mode is SplitMode.EXTRACT:
            return 1
        try:
            return len(parse_page_ranges(selection, self._source.page_count))
        except ValidationError:
            return 0

    def _split_done(self, result: OperationResult) -> None:
        """Report success and offer to open the output folder."""
        self._progress.finish("Split complete")
        self._finish_job()
        self.controller.set_status("Split completed successfully", transient=True, tone="success")

        preview = "\n".join(f"  • {path.name}" for path in result.output_paths[:5])
        if len(result.output_paths) > 5:
            preview += f"\n  …and {len(result.output_paths) - 5} more"

        if show_success(
            self,
            "Split completed successfully",
            f"{result.message}\n\nSaved to:\n{result.output_folder}\n\n{preview}",
        ):
            open_in_file_manager(result.output_folder)

    # ----------------------------------------------------------------- #
    # Job lifecycle helpers
    # ----------------------------------------------------------------- #
    def _cancel(self) -> None:
        """Cancel the running split."""
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
        self._split_button.configure(state="disabled" if busy else "normal")

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
        top.bind("<Control-o>", lambda _event: self._choose_source(), add="+")
        top.bind("<Control-Return>", lambda _event: self._split(), add="+")
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
        """Block navigation while a split is running."""
        if self._runner.busy:
            if not ask_confirm(
                self,
                "Task in progress",
                "A split is still running. Cancel it and leave this screen?",
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
