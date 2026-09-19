"""Modal dialogs and native file-picker wrappers.

All dialogs are themed CustomTkinter windows rather than the default Tk
message boxes, so they match the rest of the application. File pickers use
``tkinter.filedialog``, which maps to the native Windows common dialogs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tkinter import filedialog
from typing import Literal, Sequence

import customtkinter as ctk

from core.constants import BATCH_FILETYPES, DEFAULT_MERGE_FILENAME, DOCX_FILETYPES, PDF_FILETYPES
from core.exceptions import PdfToolkitError
from ui.theme import (
    COLORS,
    PAD,
    danger_button_style,
    fonts,
    primary_button_style,
    secondary_button_style,
)

logger = logging.getLogger(__name__)

DialogKind = Literal["info", "success", "warning", "error", "question"]

_ICONS: dict[DialogKind, str] = {
    "info": "ℹ",
    "success": "✓",
    "warning": "⚠",
    "error": "✕",
    "question": "?",
}

_ACCENTS: dict[DialogKind, str] = {
    "info": COLORS.accent,
    "success": COLORS.success,
    "warning": COLORS.warning,
    "error": COLORS.danger,
    "question": COLORS.accent,
}


class ModalDialog(ctk.CTkToplevel):
    """A themed, application-modal dialog with configurable buttons.

    The chosen button's value is stored in :attr:`result`; closing the window
    or pressing *Escape* yields ``None``.
    """

    def __init__(
        self,
        parent: ctk.CTkBaseClass | ctk.CTk,
        title: str,
        message: str,
        kind: DialogKind = "info",
        buttons: Sequence[tuple[str, object, str]] = (("OK", True, "primary"),),
        width: int = 460,
    ) -> None:
        """Build the dialog.

        Args:
            parent: Window the dialog is modal to.
            title: Window title and heading.
            message: Body text; newlines are preserved.
            kind: Controls the icon and accent colour.
            buttons: ``(label, value, style)`` triples rendered right-aligned.
                ``style`` is one of ``primary``, ``secondary`` or ``danger``.
            width: Dialog width in pixels.
        """
        super().__init__(parent)
        self.result: object | None = None

        self.title(title)
        self.configure(fg_color=COLORS.surface)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._build(title, message, kind, buttons, width)
        self.update_idletasks()
        self._centre_on(parent)

        self.bind("<Escape>", lambda _event: self._choose(None))
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(None))
        self.after(10, self._grab)

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    def _build(
        self,
        title: str,
        message: str,
        kind: DialogKind,
        buttons: Sequence[tuple[str, object, str]],
        width: int,
    ) -> None:
        """Lay out the icon, text and button row."""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.lg, pady=PAD.lg)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x")

        badge = ctk.CTkLabel(
            header,
            text=_ICONS[kind],
            width=44,
            height=44,
            corner_radius=22,
            fg_color=_ACCENTS[kind],
            text_color="#0d1117",
            font=fonts().heading,
        )
        badge.pack(side="left", padx=(0, PAD.md), anchor="n")

        text_column = ctk.CTkFrame(header, fg_color="transparent")
        text_column.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            text_column,
            text=title,
            font=fonts().subheading,
            text_color=COLORS.text,
            anchor="w",
            justify="left",
        ).pack(fill="x")

        ctk.CTkLabel(
            text_column,
            text=message,
            font=fonts().body,
            text_color=COLORS.text_muted,
            anchor="w",
            justify="left",
            wraplength=width - 130,
        ).pack(fill="x", pady=(PAD.xs, 0))

        button_row = ctk.CTkFrame(container, fg_color="transparent")
        button_row.pack(fill="x", pady=(PAD.lg, 0))

        styles = {
            "primary": primary_button_style,
            "secondary": secondary_button_style,
            "danger": danger_button_style,
        }
        first: ctk.CTkButton | None = None
        for label, value, style in reversed(list(buttons)):
            button = ctk.CTkButton(
                button_row,
                text=label,
                width=110,
                command=lambda v=value: self._choose(v),
                **styles.get(style, secondary_button_style)(),
            )
            button.pack(side="right", padx=(PAD.sm, 0))
            first = first or button

        if first is not None:
            first.focus_set()
            self.bind("<Return>", lambda _event: first.invoke())

    def _centre_on(self, parent: ctk.CTkBaseClass | ctk.CTk) -> None:
        """Position the dialog over the middle of ``parent``."""
        root = parent.winfo_toplevel()
        width, height = self.winfo_width(), self.winfo_height()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _grab(self) -> None:
        """Take the modal grab once the window is mapped."""
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:  # noqa: BLE001 - window may already be closing
            logger.debug("Could not grab dialog focus", exc_info=True)

    def _choose(self, value: object | None) -> None:
        """Record ``value`` and close."""
        self.result = value
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001 - grab may never have been taken
            pass
        self.destroy()

    def show(self) -> object | None:
        """Display the dialog modally and return the chosen value."""
        self.wait_window()
        return self.result


# --------------------------------------------------------------------------- #
# Convenience wrappers
# --------------------------------------------------------------------------- #
def show_message(
    parent: ctk.CTkBaseClass | ctk.CTk,
    title: str,
    message: str,
    kind: DialogKind = "info",
) -> None:
    """Show a single-button notification dialog."""
    ModalDialog(parent, title, message, kind, buttons=(("OK", True, "primary"),)).show()


def show_error(parent: ctk.CTkBaseClass | ctk.CTk, error: Exception, title: str | None = None) -> None:
    """Show a friendly error dialog for ``error`` and log the details."""
    if isinstance(error, PdfToolkitError):
        heading = title or error.title
        body = str(error)
    else:
        heading = title or "Something went wrong"
        body = (
            f"{error.__class__.__name__}: {error}\n\n"
            "The action was stopped safely - no files were damaged."
        )
    logger.error("%s: %s", heading, error, exc_info=not isinstance(error, PdfToolkitError))
    ModalDialog(parent, heading, body, "error", buttons=(("Close", True, "primary"),)).show()


def ask_confirm(
    parent: ctk.CTkBaseClass | ctk.CTk,
    title: str,
    message: str,
    confirm_label: str = "Continue",
    cancel_label: str = "Cancel",
    destructive: bool = False,
) -> bool:
    """Ask a yes/no question. Returns ``True`` when confirmed."""
    dialog = ModalDialog(
        parent,
        title,
        message,
        "warning" if destructive else "question",
        buttons=(
            (cancel_label, False, "secondary"),
            (confirm_label, True, "danger" if destructive else "primary"),
        ),
    )
    return dialog.show() is True


def ask_overwrite(parent: ctk.CTkBaseClass | ctk.CTk, path: Path) -> bool | None:
    """Ask what to do about an existing output file.

    Returns:
        ``True`` to replace, ``False`` to keep both (auto-rename) or ``None``
        when the user cancels.
    """
    dialog = ModalDialog(
        parent,
        "File already exists",
        f"'{path.name}' already exists in:\n{path.parent}\n\nWhat would you like to do?",
        "warning",
        buttons=(
            ("Cancel", None, "secondary"),
            ("Keep both", False, "secondary"),
            ("Replace", True, "danger"),
        ),
        width=500,
    )
    return dialog.show()  # type: ignore[return-value]


def show_success(
    parent: ctk.CTkBaseClass | ctk.CTk,
    title: str,
    message: str,
    open_label: str = "Open output folder",
) -> bool:
    """Show a completion dialog. Returns ``True`` if the user wants the folder."""
    dialog = ModalDialog(
        parent,
        title,
        message,
        "success",
        buttons=(("Close", False, "secondary"), (open_label, True, "primary")),
        width=520,
    )
    return dialog.show() is True


# --------------------------------------------------------------------------- #
# Native file pickers
# --------------------------------------------------------------------------- #
def ask_pdf_files(parent: ctk.CTkBaseClass | ctk.CTk, initial_dir: Path | None = None) -> list[Path]:
    """Open the native multi-select file picker restricted to ``*.pdf``."""
    selection = filedialog.askopenfilenames(
        parent=parent.winfo_toplevel(),
        title="Select PDF files",
        filetypes=list(PDF_FILETYPES),
        initialdir=str(initial_dir) if initial_dir else None,
    )
    return [Path(item) for item in selection]


def ask_pdf_file(parent: ctk.CTkBaseClass | ctk.CTk, initial_dir: Path | None = None) -> Path | None:
    """Open the native single-select file picker restricted to ``*.pdf``."""
    selection = filedialog.askopenfilename(
        parent=parent.winfo_toplevel(),
        title="Select a PDF file",
        filetypes=list(PDF_FILETYPES),
        initialdir=str(initial_dir) if initial_dir else None,
    )
    return Path(selection) if selection else None


def ask_docx_files(parent: ctk.CTkBaseClass | ctk.CTk, initial_dir: Path | None = None) -> list[Path]:
    """Open the native multi-select file picker restricted to ``*.docx``."""
    selection = filedialog.askopenfilenames(
        parent=parent.winfo_toplevel(),
        title="Select Word documents",
        filetypes=list(DOCX_FILETYPES),
        initialdir=str(initial_dir) if initial_dir else None,
    )
    return [Path(item) for item in selection]


def ask_batch_files(parent: ctk.CTkBaseClass | ctk.CTk, initial_dir: Path | None = None) -> list[Path]:
    """Open the native multi-select file picker for text/CSV batch input."""
    selection = filedialog.askopenfilenames(
        parent=parent.winfo_toplevel(),
        title="Select text or CSV files",
        filetypes=list(BATCH_FILETYPES),
        initialdir=str(initial_dir) if initial_dir else None,
    )
    return [Path(item) for item in selection]


def ask_folder(
    parent: ctk.CTkBaseClass | ctk.CTk,
    title: str = "Select a folder",
    initial_dir: Path | None = None,
) -> Path | None:
    """Open the native folder picker."""
    selection = filedialog.askdirectory(
        parent=parent.winfo_toplevel(),
        title=title,
        initialdir=str(initial_dir) if initial_dir else None,
        mustexist=True,
    )
    return Path(selection) if selection else None


def ask_save_pdf(
    parent: ctk.CTkBaseClass | ctk.CTk,
    initial_dir: Path | None = None,
    initial_name: str = DEFAULT_MERGE_FILENAME,
) -> Path | None:
    """Open the native *Save As* dialog for a PDF destination.

    The dialog performs its own overwrite confirmation, so callers do not need
    to ask again for this path.
    """
    selection = filedialog.asksaveasfilename(
        parent=parent.winfo_toplevel(),
        title="Save merged PDF as",
        defaultextension=".pdf",
        filetypes=list(PDF_FILETYPES),
        initialfile=initial_name,
        initialdir=str(initial_dir) if initial_dir else None,
    )
    if not selection:
        return None
    path = Path(selection)
    return path if path.suffix.lower() == ".pdf" else path.with_suffix(".pdf")
