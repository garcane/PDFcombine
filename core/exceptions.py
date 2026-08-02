"""Exception hierarchy shared by every tool in the :mod:`core` package.

The UI layer only ever needs to catch :class:`PdfToolkitError`; the message of
any subclass is written to be safe to show directly to an end user.
"""

from __future__ import annotations


class PdfToolkitError(Exception):
    """Base class for all recoverable, user-facing errors."""

    #: Short title used by error dialogs.
    title: str = "Error"


class ValidationError(PdfToolkitError):
    """Raised when user supplied input cannot be used as-is."""

    title = "Invalid input"


class PdfReadError(PdfToolkitError):
    """Raised when a PDF is missing, unreadable, encrypted or corrupt."""

    title = "Cannot read PDF"


class PdfWriteError(PdfToolkitError):
    """Raised when an output file or folder cannot be written."""

    title = "Cannot write file"


class OperationCancelled(PdfToolkitError):
    """Raised inside a worker when the user cancels a long running job."""

    title = "Cancelled"
