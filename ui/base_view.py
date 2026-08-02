"""Base class every tool view inherits from."""

from __future__ import annotations

from typing import Protocol

import customtkinter as ctk

from ui.theme import COLORS


class AppController(Protocol):
    """The subset of the application window that views are allowed to use."""

    def show_view(self, name: str) -> None:
        """Bring the named view to the front."""

    def set_status(self, message: str, transient: bool = False, tone: str = "muted") -> None:
        """Write a message to the status bar."""

    def set_status_detail(self, message: str) -> None:
        """Write the right-aligned status bar text."""

    def request_exit(self) -> None:
        """Close the application, asking for confirmation when work is running."""


class BaseView(ctk.CTkFrame):
    """Common behaviour for full-window views.

    Subclasses build their widgets in :meth:`build` and may override
    :meth:`on_show`, :meth:`on_hide` and :meth:`can_leave`.
    """

    #: Name used with :meth:`AppController.show_view`.
    view_name: str = "view"
    #: Text written to the status bar when the view is shown.
    status_hint: str = "Ready"

    def __init__(self, master: ctk.CTkBaseClass | ctk.CTk, controller: AppController) -> None:
        """Create the view and immediately build its widgets."""
        super().__init__(master, fg_color=COLORS.background, corner_radius=0)
        self.controller = controller
        self.build()

    def build(self) -> None:
        """Create the widgets for this view. Overridden by subclasses."""

    def on_show(self) -> None:
        """Called each time the view becomes visible."""
        self.controller.set_status(self.status_hint)

    def on_hide(self) -> None:
        """Called each time the view is hidden."""

    def can_leave(self) -> bool:
        """Return ``False`` to veto navigation away from this view."""
        return True

    def go_home(self) -> None:
        """Navigate back to the home screen."""
        self.controller.show_view("home")
