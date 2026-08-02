"""Progress reporting and cancellation primitives for long running jobs.

Core tools accept a :class:`ProgressReporter`; the UI supplies an
implementation that marshals updates back onto the Tk main loop. This keeps
the processing modules completely free of UI imports.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol

from core.exceptions import OperationCancelled


class ProgressReporter(Protocol):
    """Callback interface used by every core tool to report progress."""

    def update(self, completed: int, total: int, message: str) -> None:
        """Report that ``completed`` of ``total`` steps are done."""


class NullProgress:
    """A :class:`ProgressReporter` that discards everything.

    Used as the default so core functions can be called from scripts or tests
    without wiring up a UI.
    """

    def update(self, completed: int, total: int, message: str) -> None:  # noqa: D102
        return None


class CallbackProgress:
    """Adapter turning a plain function into a :class:`ProgressReporter`."""

    __slots__ = ("_callback",)

    def __init__(self, callback: Callable[[int, int, str], None]) -> None:
        self._callback = callback

    def update(self, completed: int, total: int, message: str) -> None:  # noqa: D102
        self._callback(completed, total, message)


class CancellationToken:
    """Thread-safe cancellation flag shared between the UI and a worker."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. Safe to call from any thread."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """``True`` once :meth:`cancel` has been called."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Abort the current operation if cancellation has been requested.

        Raises:
            OperationCancelled: When :meth:`cancel` has been called.
        """
        if self._event.is_set():
            raise OperationCancelled("The operation was cancelled.")
