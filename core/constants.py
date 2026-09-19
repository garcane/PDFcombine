"""Application-wide configurable constants.

Everything that a maintainer might reasonably want to tweak (window sizes,
colour palette, file-name templates, limits) lives here so that no magic
values are scattered through the UI or the processing layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
APP_NAME: Final[str] = "PDF Toolkit"
APP_VERSION: Final[str] = "1.0.0"
APP_TAGLINE: Final[str] = "Merge, split and convert your documents"
ORGANISATION: Final[str] = "PDF Toolkit"

# --------------------------------------------------------------------------- #
# Filesystem locations
# --------------------------------------------------------------------------- #
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
ASSETS_DIR: Final[Path] = PROJECT_ROOT / "assets"
ICONS_DIR: Final[Path] = ASSETS_DIR / "icons"
LOGO_PATH: Final[Path] = ASSETS_DIR / "logo.png"
ICON_PATH: Final[Path] = ASSETS_DIR / "app.ico"

#: Per-user directory used for logs and persisted settings.
USER_DATA_DIR: Final[Path] = Path.home() / ".pdf_toolkit"
LOG_FILE: Final[Path] = USER_DATA_DIR / "pdf_toolkit.log"
LOG_MAX_BYTES: Final[int] = 1_000_000
LOG_BACKUP_COUNT: Final[int] = 3

# --------------------------------------------------------------------------- #
# Window geometry
# --------------------------------------------------------------------------- #
WINDOW_WIDTH: Final[int] = 1180
WINDOW_HEIGHT: Final[int] = 760
WINDOW_MIN_WIDTH: Final[int] = 940
WINDOW_MIN_HEIGHT: Final[int] = 660

# --------------------------------------------------------------------------- #
# PDF handling
# --------------------------------------------------------------------------- #
PDF_EXTENSIONS: Final[tuple[str, ...]] = (".pdf",)
PDF_FILETYPES: Final[tuple[tuple[str, str], ...]] = (
    ("PDF documents", "*.pdf"),
    ("All files", "*.*"),
)

# --------------------------------------------------------------------------- #
# Word handling
# --------------------------------------------------------------------------- #
DOCX_EXTENSIONS: Final[tuple[str, ...]] = (".docx",)
DOCX_FILETYPES: Final[tuple[tuple[str, str], ...]] = (
    ("Word documents", "*.docx"),
    ("All files", "*.*"),
)

#: Word writes lock files named ``~$document.docx`` next to open documents.
#: They are not real documents and must never be picked up by a folder scan.
LOCK_FILE_PREFIX: Final[str] = "~$"

#: Default name for a collated conversion output (extension added by the tool).
DEFAULT_COMBINED_NAME: Final[str] = "Combined"

#: Templates used when naming generated files. ``{stem}`` is the source file
#: name without extension, indices are 1-based and zero padded.
PAGE_FILENAME_TEMPLATE: Final[str] = "Page_{index:03d}.pdf"
RANGE_FILENAME_TEMPLATE: Final[str] = "{stem}_Pages_{start}-{end}.pdf"
EXTRACT_FILENAME_TEMPLATE: Final[str] = "{stem}_Extracted.pdf"
DEFAULT_MERGE_FILENAME: Final[str] = "Merged.pdf"

# --------------------------------------------------------------------------- #
# PDF to Image
# --------------------------------------------------------------------------- #
#: Raster formats the render tool can produce.
IMAGE_FORMATS: Final[tuple[str, ...]] = ("png", "jpg")
IMAGE_FILENAME_TEMPLATE: Final[str] = "{stem}_Page_{index:03d}.{ext}"
DEFAULT_RENDER_DPI: Final[int] = 150
MIN_RENDER_DPI: Final[int] = 36
MAX_RENDER_DPI: Final[int] = 600

# --------------------------------------------------------------------------- #
# Batch: text/CSV to PDF
# --------------------------------------------------------------------------- #
#: Extensions treated as "plain text" input for the batch-to-PDF tool. ``.sql``
#: is included so query/script files render the same way as any other text
#: file, rather than needing a dedicated tool.
TEXT_EXTENSIONS: Final[tuple[str, ...]] = (".txt", ".log", ".sql", ".md")
CSV_EXTENSIONS: Final[tuple[str, ...]] = (".csv",)
BATCH_INPUT_EXTENSIONS: Final[tuple[str, ...]] = TEXT_EXTENSIONS + CSV_EXTENSIONS
BATCH_FILETYPES: Final[tuple[tuple[str, str], ...]] = (
    ("Text and CSV files", "*.txt *.log *.sql *.md *.csv"),
    ("All files", "*.*"),
)
DEFAULT_BATCH_COMBINED_NAME: Final[str] = "Batch_Combined.pdf"

#: Hard ceiling used by the range parser to reject absurd input early.
MAX_PAGE_NUMBER: Final[int] = 1_000_000

#: Size (px) of the first-page thumbnails shown in the merge review list.
THUMBNAIL_SIZE: Final[tuple[int, int]] = (46, 60)
PREVIEW_SIZE: Final[tuple[int, int]] = (210, 272)

# --------------------------------------------------------------------------- #
# Behaviour
# --------------------------------------------------------------------------- #
#: Minimum interval (ms) between two status-bar refreshes triggered by a
#: background worker. Keeps the UI responsive on very large batches.
PROGRESS_THROTTLE_MS: Final[int] = 40
