"""Background job execution for the UI layer.

Tk is not thread-safe, so worker threads never touch widgets directly: they
push events onto a queue that is drained on the main loop by a periodic
``after`` callback.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Generic, TypeVar

import customtkinter as ctk

from core.constants import PROGRESS_THROTTLE_MS
from core.exceptions import OperationCancelled
from core.progress import CallbackProgress, CancellationToken

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Job signature: receives a progress reporter and a cancellation token.
Job = Callable[[CallbackProgress, CancellationToken], T]


class JobRunner(Generic[T]):
    """Runs one :data:`Job` on a worker thread and reports back on the UI thread.

    A runner handles a single job at a time; :meth:`busy` tells callers whether
    a job is currently in flight.
    """

    def __init__(self, widget: ctk.CTkBaseClass | ctk.CTk) -> None:
        """Bind the runner to ``widget``, whose ``after`` loop drains events."""
        self._widget = widget
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._token: CancellationToken | None = None
        self._poll_id: str | None = None
        self._on_progress: Callable[[int, int, str], None] | None = None
        self._on_success: Callable[[T], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None
        self._on_cancelled: Callable[[], None] | None = None

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #
    @property
    def busy(self) -> bool:
        """``True`` while a job is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        job: Job[T],
        on_success: Callable[[T], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[int, int, str], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> CancellationToken:
        """Start ``job`` on a worker thread.

        Args:
            job: Callable receiving ``(progress, token)`` and returning a result.
            on_success: Called on the UI thread with the job's return value.
            on_error: Called on the UI thread with any raised exception.
            on_progress: Called on the UI thread as ``(completed, total, message)``.
            on_cancelled: Called on the UI thread when the job was cancelled.

        Returns:
            The token that can be used to cancel the job.

        Raises:
            RuntimeError: A job is already running on this runner.
        """
        if self.busy:
            raise RuntimeError("A job is already running.")

        self._on_progress = on_progress
        self._on_success = on_success
        self._on_error = on_error
        self._on_cancelled = on_cancelled

        token = CancellationToken()
        self._token = token
        progress = CallbackProgress(
            lambda completed, total, message: self._queue.put(
                ("progress", (completed, total, message))
            )
        )

        def worker() -> None:
            """Thread body: run the job and post exactly one terminal event."""
            try:
                result = job(progress, token)
            except OperationCancelled:
                self._queue.put(("cancelled", None))
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a dialog
                logger.exception("Background job failed")
                self._queue.put(("error", exc))
            else:
                self._queue.put(("success", result))

        self._thread = threading.Thread(target=worker, name="pdf-toolkit-job", daemon=True)
        self._thread.start()
        self._schedule_poll()
        return token

    def cancel(self) -> None:
        """Request cancellation of the running job, if any."""
        if self._token is not None:
            self._token.cancel()

    def shutdown(self) -> None:
        """Stop polling. Called when the owning view is destroyed."""
        self.cancel()
        if self._poll_id is not None:
            try:
                self._widget.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001 - widget may already be gone
                pass
            self._poll_id = None

    # ----------------------------------------------------------------- #
    # Event pump
    # ----------------------------------------------------------------- #
    def _schedule_poll(self) -> None:
        """Queue the next drain of the event queue."""
        self._poll_id = self._widget.after(PROGRESS_THROTTLE_MS, self._drain)

    def _drain(self) -> None:
        """Deliver queued events to the callbacks on the UI thread."""
        finished = False
        last_progress: tuple[int, int, str] | None = None

        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                last_progress = payload  # coalesce: only the newest matters
                continue

            finished = True
            self._dispatch_terminal(kind, payload, last_progress)
            last_progress = None
            break

        if last_progress is not None and self._on_progress is not None:
            self._safe(self._on_progress, *last_progress)

        if not finished:
            self._schedule_poll()
        else:
            self._poll_id = None
            self._thread = None

    def _dispatch_terminal(
        self,
        kind: str,
        payload: Any,
        last_progress: tuple[int, int, str] | None,
    ) -> None:
        """Flush the final progress update, then fire the terminal callback."""
        if last_progress is not None and self._on_progress is not None:
            self._safe(self._on_progress, *last_progress)

        if kind == "success" and self._on_success is not None:
            self._safe(self._on_success, payload)
        elif kind == "error" and self._on_error is not None:
            self._safe(self._on_error, payload)
        elif kind == "cancelled" and self._on_cancelled is not None:
            self._safe(self._on_cancelled)

    @staticmethod
    def _safe(callback: Callable[..., None], *args: Any) -> None:
        """Invoke ``callback``, logging (never propagating) UI errors."""
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the pump
            logger.exception("UI callback raised")
