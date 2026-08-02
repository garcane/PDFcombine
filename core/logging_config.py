"""Application logging setup."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from core.constants import LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES, USER_DATA_DIR

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(level: int = logging.INFO, console: bool = True) -> None:
    """Configure the root logger with a rotating file handler.

    Logs are written to :data:`core.constants.LOG_FILE`. If that location is
    not writable (locked-down machine, read-only profile) the application still
    starts - it simply logs to the console only.

    Args:
        level: Minimum level to record.
        console: Also mirror records to stderr when a console is attached.
    """
    root = logging.getLogger()
    if root.handlers:  # already configured
        return
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    try:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - depends on the host machine
        pass

    if console and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if not root.handlers:  # pragma: no cover - defensive
        root.addHandler(logging.NullHandler())
