"""Home screen: the launcher for every tool in the toolkit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image

from core.constants import APP_NAME, APP_TAGLINE, APP_VERSION, LOGO_PATH
from ui.base_view import BaseView
from ui.theme import COLORS, PAD, danger_button_style, fonts, secondary_button_style
from ui.widgets import ToolCard

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """One launcher tile on the home screen.

    Registering a future tool is a one-line addition to
    :meth:`HomeView.tool_entries`.
    """

    icon: str
    title: str
    description: str
    view_name: str
    accent: str


class HomeView(BaseView):
    """Landing page with the application identity and the tool tiles."""

    view_name = "home"
    status_hint = "Choose a tool to get started"

    def build(self) -> None:
        """Lay out the logo, title, tool cards and exit button."""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PAD.xxl, pady=PAD.xl)

        self._build_identity(container)
        self._build_tools(container)
        self._build_footer(container)

    # ----------------------------------------------------------------- #
    # Sections
    # ----------------------------------------------------------------- #
    def _build_identity(self, parent: ctk.CTkFrame) -> None:
        """Logo, application name and tagline."""
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(PAD.lg, PAD.xl))

        logo = self._load_logo()
        if logo is not None:
            ctk.CTkLabel(header, image=logo, text="").pack(pady=(0, PAD.md))
        else:
            ctk.CTkLabel(
                header,
                text="📄",
                font=ctk.CTkFont(family="Segoe UI Emoji", size=54),
                text_color=COLORS.accent,
            ).pack(pady=(0, PAD.sm))

        ctk.CTkLabel(
            header, text=APP_NAME, font=fonts().display, text_color=COLORS.text
        ).pack()
        ctk.CTkLabel(
            header, text=APP_TAGLINE, font=fonts().body, text_color=COLORS.text_muted
        ).pack(pady=(PAD.xs, 0))

    #: Maximum tool cards shown per row before wrapping to the next one.
    _MAX_COLUMNS = 3

    def _build_tools(self, parent: ctk.CTkFrame) -> None:
        """Grid of tool cards, wrapping to a new row once a row is full."""
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True)

        entries = self.tool_entries()
        columns = min(len(entries), self._MAX_COLUMNS) or 1
        for column in range(columns):
            grid.grid_columnconfigure(column, weight=1, uniform="tools")

        for index, entry in enumerate(entries):
            row, column = divmod(index, columns)
            grid.grid_rowconfigure(row, weight=1)
            card = ToolCard(
                grid,
                icon=entry.icon,
                title=entry.title,
                description=entry.description,
                command=self._navigator(entry.view_name),
                accent=entry.accent,
            )
            # "new" keeps the cards sized to their content and leaves any
            # spare vertical space below them.
            card.grid(row=row, column=column, padx=PAD.md, pady=PAD.md, sticky="new")

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        """Version label and the exit button."""
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(fill="x", pady=(PAD.lg, 0))

        ctk.CTkLabel(
            footer,
            text=f"Version {APP_VERSION}",
            font=fonts().small,
            text_color=COLORS.text_disabled,
        ).pack(side="left")

        ctk.CTkButton(
            footer,
            text="Exit",
            width=120,
            command=self.controller.request_exit,
            **danger_button_style(),
        ).pack(side="right")

        ctk.CTkButton(
            footer,
            text="About",
            width=120,
            command=self._show_about,
            **secondary_button_style(),
        ).pack(side="right", padx=(0, PAD.sm))

    # ----------------------------------------------------------------- #
    # Tool registry
    # ----------------------------------------------------------------- #
    @staticmethod
    def tool_entries() -> list[ToolEntry]:
        """Return the tools shown on the home screen, in display order."""
        return [
            ToolEntry(
                icon="🔗",
                title="Merge PDFs",
                description=(
                    "Combine several PDF files into a single document, in exactly "
                    "the order you choose."
                ),
                view_name="merge",
                accent=COLORS.accent,
            ),
            ToolEntry(
                icon="✂",
                title="Split PDF",
                description=(
                    "Break one PDF into separate files by page, by range, or "
                    "extract just the pages you need."
                ),
                view_name="split",
                accent="#a855f7",
            ),
            ToolEntry(
                icon="📝",
                title="Convert Word files",
                description=(
                    "Turn .docx documents into PDF or Markdown - singly, in "
                    "bulk, or collated into one file."
                ),
                view_name="convert",
                accent="#14b8a6",
            ),
            ToolEntry(
                icon="🖼",
                title="PDF to Image",
                description=(
                    "Export a PDF's pages as PNG or JPEG files at the "
                    "resolution you choose."
                ),
                view_name="render",
                accent="#f59e0b",
            ),
            ToolEntry(
                icon="📊",
                title="Batch to PDF",
                description=(
                    "Convert text, CSV, log and .sql files to PDF - singly, "
                    "or collated into one file."
                ),
                view_name="batch",
                accent="#3b82f6",
            ),
        ]

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _navigator(self, view_name: str) -> Callable[[], None]:
        """Return a callback that switches to ``view_name``."""
        return lambda: self.controller.show_view(view_name)

    @staticmethod
    def _load_logo(path: Path = LOGO_PATH) -> ctk.CTkImage | None:
        """Load the application logo, returning ``None`` when unavailable."""
        if not path.exists():
            return None
        try:
            image = Image.open(path)
            return ctk.CTkImage(light_image=image, dark_image=image, size=(72, 72))
        except Exception as exc:  # noqa: BLE001 - branding is optional
            logger.warning("Could not load logo %s: %s", path, exc)
            return None

    def _show_about(self) -> None:
        """Show the about dialog."""
        from ui.dialogs import show_message

        show_message(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            f"{APP_TAGLINE}.\n\n"
            "Built with Python, CustomTkinter, pypdf and PyMuPDF.\n"
            "All processing happens on this computer - no files are uploaded.",
            "info",
        )
