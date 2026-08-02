"""Operating-system drag-and-drop support.

``tkinterdnd2`` is an optional dependency: when it is missing (or fails to
load its Tcl package) the application still works, it simply asks the user to
pick files with the buttons instead. Every entry point here degrades quietly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the host installation
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - missing package or missing Tcl library
    DND_FILES = "DND_Files"  # type: ignore[assignment]
    TkinterDnD = None  # type: ignore[assignment]
    DND_AVAILABLE = False
    logger.info("tkinterdnd2 unavailable - drag-and-drop from Explorer is disabled.")


def bootstrap_root(root: ctk.CTk) -> bool:
    """Attach the tkdnd Tcl package to ``root``.

    Must be called once, right after the root window is created, before any
    widget registers itself as a drop target.

    Returns:
        ``True`` when drag-and-drop is available.
    """
    if not DND_AVAILABLE:
        return False
    try:
        root.TkdndVersion = TkinterDnD._require(root)  # type: ignore[union-attr]
        return True
    except Exception as exc:  # noqa: BLE001 - Tcl library may be missing
        logger.warning("Could not initialise tkdnd: %s", exc)
        return False


def enable_file_drop(widget: Any, callback: Callable[[list[Path]], None]) -> bool:
    """Make ``widget`` accept files dropped from the file manager.

    Args:
        widget: Any Tk widget; its toplevel must have been bootstrapped.
        callback: Receives the dropped paths (may include folders).

    Returns:
        ``True`` when the drop target was registered.
    """
    if not DND_AVAILABLE:
        return False

    def on_drop(event: Any) -> str:
        """Translate the raw Tcl list into ``Path`` objects."""
        try:
            paths = [Path(item) for item in widget.tk.splitlist(event.data)]
        except Exception:  # noqa: BLE001 - malformed payload from another app
            logger.debug("Could not parse drop payload", exc_info=True)
            return "break"
        if paths:
            callback(paths)
        return "break"

    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", on_drop)
        return True
    except Exception as exc:  # noqa: BLE001 - widget type may not support it
        logger.debug("Could not register drop target: %s", exc)
        return False
