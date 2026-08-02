"""Reusable presentational widgets shared by the views."""

from __future__ import annotations

import logging
from typing import Callable

import customtkinter as ctk

from ui.theme import (
    CARD_RADIUS,
    COLORS,
    CORNER_RADIUS,
    PAD,
    fonts,
    small_button_style,
)

logger = logging.getLogger(__name__)


class PageHeader(ctk.CTkFrame):
    """Title bar shown at the top of a tool view, with an optional back button."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        title: str,
        subtitle: str = "",
        on_back: Callable[[], None] | None = None,
    ) -> None:
        """Create the header.

        Args:
            master: Parent widget.
            title: Large heading text.
            subtitle: Muted explanatory line under the heading.
            on_back: When given, a "Back" button is shown and calls this.
        """
        super().__init__(master, fg_color="transparent")

        if on_back is not None:
            ctk.CTkButton(
                self,
                text="‹  Back",
                width=90,
                command=on_back,
                **small_button_style(),
            ).pack(side="left", padx=(0, PAD.md), anchor="n", pady=(PAD.xs, 0))

        text_column = ctk.CTkFrame(self, fg_color="transparent")
        text_column.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            text_column,
            text=title,
            font=fonts().title,
            text_color=COLORS.text,
            anchor="w",
        ).pack(fill="x")

        if subtitle:
            ctk.CTkLabel(
                text_column,
                text=subtitle,
                font=fonts().body,
                text_color=COLORS.text_muted,
                anchor="w",
            ).pack(fill="x", pady=(2, 0))


class Card(ctk.CTkFrame):
    """A padded surface panel with an optional heading."""

    def __init__(self, master: ctk.CTkBaseClass, title: str = "", **kwargs: object) -> None:
        """Create the card; extra ``kwargs`` are forwarded to ``CTkFrame``."""
        options: dict[str, object] = {
            "fg_color": COLORS.surface,
            "corner_radius": CARD_RADIUS,
            "border_width": 1,
            "border_color": COLORS.border,
        }
        options.update(kwargs)
        super().__init__(master, **options)  # type: ignore[arg-type]

        self.body = ctk.CTkFrame(self, fg_color="transparent")

        if title:
            ctk.CTkLabel(
                self,
                text=title,
                font=fonts().subheading,
                text_color=COLORS.text,
                anchor="w",
            ).pack(fill="x", padx=PAD.lg, pady=(PAD.md, 0))

        self.body.pack(fill="both", expand=True, padx=PAD.lg, pady=PAD.md)


class ToolCard(ctk.CTkFrame):
    """Large clickable card used on the home screen to launch a tool."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        icon: str,
        title: str,
        description: str,
        command: Callable[[], None],
        accent: str | None = None,
    ) -> None:
        """Create the card and wire hover/click behaviour on every child."""
        super().__init__(
            master,
            fg_color=COLORS.surface,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=COLORS.border,
            cursor="hand2",
        )
        self._command = command
        self._accent = accent or COLORS.accent

        badge = ctk.CTkLabel(
            self,
            text=icon,
            width=64,
            height=64,
            corner_radius=18,
            fg_color=COLORS.surface_alt,
            text_color=self._accent,
            font=fonts().icon,
        )
        badge.pack(padx=PAD.xl, pady=(PAD.xl, PAD.md))

        title_label = ctk.CTkLabel(
            self, text=title, font=fonts().heading, text_color=COLORS.text
        )
        title_label.pack(padx=PAD.xl)

        description_label = ctk.CTkLabel(
            self,
            text=description,
            font=fonts().small,
            text_color=COLORS.text_muted,
            wraplength=250,
            justify="center",
        )
        description_label.pack(padx=PAD.xl, pady=(PAD.xs, PAD.md))

        self._action = ctk.CTkButton(
            self,
            text="Open",
            width=140,
            command=command,
            fg_color=self._accent,
            hover_color=COLORS.accent_hover,
            text_color="#ffffff",
            corner_radius=CORNER_RADIUS,
            height=38,
            font=fonts().body_bold,
        )
        self._action.pack(pady=(0, PAD.xl))

        for widget in (self, badge, title_label, description_label):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _on_click(self, _event: object) -> None:
        """Invoke the card's command."""
        self._command()

    def _on_enter(self, _event: object) -> None:
        """Highlight the card border on hover."""
        self.configure(border_color=self._accent, fg_color=COLORS.surface_alt)

    def _on_leave(self, _event: object) -> None:
        """Restore the resting appearance."""
        self.configure(border_color=COLORS.border, fg_color=COLORS.surface)


class ProgressPanel(ctk.CTkFrame):
    """Determinate progress bar with a headline, detail line and Cancel button."""

    def __init__(self, master: ctk.CTkBaseClass, on_cancel: Callable[[], None] | None = None) -> None:
        """Create the panel; it starts hidden until :meth:`start` is called."""
        super().__init__(
            master,
            fg_color=COLORS.surface_alt,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=COLORS.border,
        )
        self._on_cancel = on_cancel

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=PAD.lg, pady=(PAD.md, 0))

        self._headline = ctk.CTkLabel(
            top, text="Working…", font=fonts().body_bold, text_color=COLORS.text, anchor="w"
        )
        self._headline.pack(side="left", fill="x", expand=True)

        self._counter = ctk.CTkLabel(
            top, text="", font=fonts().small, text_color=COLORS.text_muted, anchor="e"
        )
        self._counter.pack(side="right")

        self._bar = ctk.CTkProgressBar(
            self, height=10, corner_radius=6, progress_color=COLORS.accent
        )
        self._bar.set(0)
        self._bar.pack(fill="x", padx=PAD.lg, pady=(PAD.sm, PAD.sm))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=PAD.lg, pady=(0, PAD.md))

        self._detail = ctk.CTkLabel(
            bottom, text="", font=fonts().small, text_color=COLORS.text_muted, anchor="w"
        )
        self._detail.pack(side="left", fill="x", expand=True)

        if on_cancel is not None:
            self._cancel_button = ctk.CTkButton(
                bottom, text="Cancel", width=90, command=self._cancel, **small_button_style()
            )
            self._cancel_button.pack(side="right", padx=(PAD.sm, 0))
        else:
            self._cancel_button = None  # type: ignore[assignment]

    def start(self, headline: str) -> None:
        """Reset the panel and show it."""
        self._headline.configure(text=headline)
        self._detail.configure(text="Preparing…")
        self._counter.configure(text="")
        self._bar.set(0)
        if self._cancel_button is not None:
            self._cancel_button.configure(state="normal", text="Cancel")

    def update_progress(self, completed: int, total: int, message: str) -> None:
        """Refresh the bar, counter and detail line."""
        fraction = (completed / total) if total else 0.0
        self._bar.set(min(max(fraction, 0.0), 1.0))
        self._counter.configure(text=f"{completed} of {total}" if total else "")
        self._detail.configure(text=message)

    def finish(self, message: str = "Done") -> None:
        """Fill the bar and show a closing message."""
        self._bar.set(1.0)
        self._detail.configure(text=message)
        if self._cancel_button is not None:
            self._cancel_button.configure(state="disabled")

    def _cancel(self) -> None:
        """Signal cancellation and reflect it in the UI."""
        if self._cancel_button is not None:
            self._cancel_button.configure(state="disabled", text="Cancelling…")
        self._detail.configure(text="Finishing the current file…")
        if self._on_cancel is not None:
            self._on_cancel()


class StatusBar(ctk.CTkFrame):
    """Thin bar at the bottom of the window showing transient status text."""

    def __init__(self, master: ctk.CTkBaseClass | ctk.CTk) -> None:
        """Create the status bar."""
        super().__init__(master, fg_color=COLORS.surface, corner_radius=0, height=32)
        self.pack_propagate(False)

        self._label = ctk.CTkLabel(
            self, text="Ready", font=fonts().small, text_color=COLORS.text_muted, anchor="w"
        )
        self._label.pack(side="left", padx=PAD.md)

        self._right = ctk.CTkLabel(
            self, text="", font=fonts().small, text_color=COLORS.text_disabled, anchor="e"
        )
        self._right.pack(side="right", padx=PAD.md)

        self._reset_id: str | None = None

    def set_status(self, message: str, transient: bool = False, tone: str = "muted") -> None:
        """Show ``message``.

        Args:
            message: Text to display.
            transient: When ``True`` the bar reverts to "Ready" after 6 seconds.
            tone: ``muted``, ``success``, ``warning`` or ``error``.
        """
        colours = {
            "muted": COLORS.text_muted,
            "success": COLORS.success,
            "warning": COLORS.warning,
            "error": COLORS.danger,
        }
        self._label.configure(text=message, text_color=colours.get(tone, COLORS.text_muted))

        if self._reset_id is not None:
            try:
                self.after_cancel(self._reset_id)
            except Exception:  # noqa: BLE001 - callback may have fired already
                pass
            self._reset_id = None
        if transient:
            self._reset_id = self.after(6000, lambda: self.set_status("Ready"))

    def set_detail(self, message: str) -> None:
        """Set the right-aligned secondary text."""
        self._right.configure(text=message)


class EmptyState(ctk.CTkFrame):
    """Centred placeholder shown when a list has no items."""

    def __init__(self, master: ctk.CTkBaseClass, icon: str, title: str, message: str) -> None:
        """Create the placeholder."""
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(self, text=icon, font=fonts().icon, text_color=COLORS.text_disabled).pack(
            pady=(PAD.xl, PAD.sm)
        )
        ctk.CTkLabel(self, text=title, font=fonts().subheading, text_color=COLORS.text_muted).pack()
        ctk.CTkLabel(
            self,
            text=message,
            font=fonts().small,
            text_color=COLORS.text_disabled,
            justify="center",
            wraplength=460,
        ).pack(pady=(PAD.xs, PAD.xl))
