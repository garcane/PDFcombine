"""Core package: all PDF processing logic.

Nothing in this package imports Tkinter or CustomTkinter, which keeps the
tools scriptable, unit-testable and reusable by future front-ends.

Adding a new tool (compress, rotate, watermark, OCR …) means adding one module
here that exposes a class with a ``display_name`` attribute and a method
returning an :class:`core.models.OperationResult`, then a matching view under
:mod:`ui`.
"""

from core.batch import TextToPdfTool
from core.exceptions import (
    OperationCancelled,
    PdfReadError,
    PdfToolkitError,
    PdfWriteError,
    ValidationError,
)
from core.merger import PdfMergeTool
from core.models import OperationResult, PdfFileInfo
from core.progress import CallbackProgress, CancellationToken, NullProgress
from core.render import PdfToImageTool
from core.splitter import PdfSplitTool, SplitMode

__all__ = [
    "CallbackProgress",
    "CancellationToken",
    "NullProgress",
    "OperationCancelled",
    "OperationResult",
    "PdfFileInfo",
    "PdfMergeTool",
    "PdfReadError",
    "PdfSplitTool",
    "PdfToImageTool",
    "PdfToolkitError",
    "PdfWriteError",
    "SplitMode",
    "TextToPdfTool",
    "ValidationError",
]
