"""PDF Toolkit - application entry point.

Run with::

    python app.py

The window owns the view stack, the status bar and global keyboard shortcuts;
all PDF work lives in the :mod:`core` package.
"""

from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType

import customtkinter as ctk

from core.constants import (
    APP_NAME,
    APP_VERSION,
    ICON_PATH,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
)
from core.logging_config import configure_logging
from ui.base_view import BaseView
from ui.dialogs import ask_confirm, show_message
from ui.dnd import bootstrap_root
from ui.home import HomeView
from ui.merge_view import MergeView
from ui.split_view import SplitView
from ui.theme import COLORS, apply_theme, init_fonts
from ui.widgets import StatusBar

logger = logging.getLogger(__name__)


class PdfToolkitApp(ctk.CTk):
    """The main application window and view controller."""

    def __init__(self) -> None:
        """Create the window, the views and the global bindings."""
        super().__init__()

        self._dnd_ready = bootstrap_root(self)
        init_fonts()

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.configure(fg_color=COLORS.background)
        self._apply_window_icon()
        self._centre_on_screen()

        # The status bar is packed first so it always keeps its strip at the
        # bottom, however small the window is made.
        self.status_bar = StatusBar(self)
        self.status_bar.pack(fill="x", side="bottom")

        self._container = ctk.CTkFrame(self, fg_color=COLORS.background, corner_radius=0)
        self._container.pack(fill="both", expand=True)

        self._views: dict[str, BaseView] = {}
        self._current: BaseView | None = None
        self._create_views()

        self.protocol("WM_DELETE_WINDOW", self.request_exit)
        self.bind("<Control-q>", lambda _event: self.request_exit())
        self.bind("<F1>", lambda _event: self._show_shortcuts())

        self.show_view("home")
        logger.info("%s %s started (drag-and-drop: %s)", APP_NAME, APP_VERSION, self._dnd_ready)

    # ----------------------------------------------------------------- #
    # View management
    # ----------------------------------------------------------------- #
    def _create_views(self) -> None:
        """Instantiate every view once and keep them stacked in the container.

        Registering a future tool means adding one line here.
        """
        for view_class in (HomeView, MergeView, SplitView):
            view = view_class(self._container, self)
            self._views[view.view_name] = view

    def show_view(self, name: str) -> None:
        """Bring the view registered under ``name`` to the front."""
        target = self._views.get(name)
        if target is None:
            logger.error("Unknown view requested: %s", name)
            return
        if target is self._current:
            return
        if self._current is not None:
            if not self._current.can_leave():
                return
            self._current.on_hide()
            self._current.pack_forget()

        target.pack(fill="both", expand=True)
        target.on_show()
        self._current = target

    # ----------------------------------------------------------------- #
    # Status bar (AppController protocol)
    # ----------------------------------------------------------------- #
    def set_status(self, message: str, transient: bool = False, tone: str = "muted") -> None:
        """Write ``message`` to the status bar."""
        self.status_bar.set_status(message, transient=transient, tone=tone)

    def set_status_detail(self, message: str) -> None:
        """Write the right-aligned status bar text."""
        self.status_bar.set_detail(message)

    # ----------------------------------------------------------------- #
    # Window helpers
    # ----------------------------------------------------------------- #
    def _apply_window_icon(self) -> None:
        """Set the taskbar/window icon when the asset is present."""
        if not ICON_PATH.exists():
            return
        try:
            self.iconbitmap(str(ICON_PATH))
        except Exception as exc:  # noqa: BLE001 - branding is optional
            logger.debug("Could not apply window icon: %s", exc)

    def _centre_on_screen(self) -> None:
        """Position the window in the middle of the primary display."""
        self.update_idletasks()
        x = max((self.winfo_screenwidth() - WINDOW_WIDTH) // 2, 0)
        y = max((self.winfo_screenheight() - WINDOW_HEIGHT) // 3, 0)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _show_shortcuts(self) -> None:
        """Display the keyboard shortcut reference."""
        show_message(
            self,
            "Keyboard shortcuts",
            "Ctrl + O        Select files (or a PDF to split)\n"
            "Ctrl + D        Select a folder\n"
            "Alt + ↑ / ↓     Move the selected file\n"
            "Delete          Remove the selected file\n"
            "Ctrl + Enter    Run the current tool\n"
            "Esc             Back to the home screen\n"
            "F1              This help\n"
            "Ctrl + Q        Exit",
            "info",
        )

    def request_exit(self) -> None:
        """Close the application, confirming when a task is still running."""
        if self._current is not None and not self._current.can_leave():
            return
        if not ask_confirm(
            self, f"Exit {APP_NAME}?", "Close the application?", confirm_label="Exit"
        ):
            return
        logger.info("Application closing")
        self.destroy()


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _install_exception_hook(app: ctk.CTk) -> None:
    """Route unhandled exceptions to a dialog instead of a silent crash."""

    def report(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Log the traceback and tell the user the app is still usable."""
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            show_message(
                app,
                "Unexpected error",
                f"{exc_type.__name__}: {exc}\n\n"
                "The application is still running - the details were written to the log file.",
                "error",
            )
        except Exception:  # noqa: BLE001 - the UI itself may be gone
            traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = report
    # Tk swallows exceptions raised inside callbacks unless this is overridden.
    app.report_callback_exception = report  # type: ignore[assignment]


def main() -> int:
    """Start the application. Returns the process exit code."""
    configure_logging()
    apply_theme()

    try:
        app = PdfToolkitApp()
    except Exception:  # noqa: BLE001 - startup failures must be diagnosable
        logger.critical("Failed to start the application", exc_info=True)
        traceback.print_exc()
        return 1

    _install_exception_hook(app)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
