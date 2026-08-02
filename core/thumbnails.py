"""First-page thumbnail rendering.

Rendering a PDF page to a bitmap needs a rasteriser, which is *optional* for
this application: if none is installed the service falls back to a drawn
placeholder so the UI always has something to show. Backends are tried in
order of preference and the first importable one wins.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class ThumbnailService:
    """Renders and caches first-page thumbnails.

    The cache is keyed by ``(resolved path, mtime, size)`` so a file that is
    modified on disk is re-rendered rather than served stale.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, float, tuple[int, int]], Image.Image] = {}
        self._lock = threading.Lock()
        self._backend = self._detect_backend()

    @property
    def backend(self) -> str:
        """Name of the active rasteriser, or ``"placeholder"``."""
        return self._backend

    def get(self, path: Path, size: tuple[int, int]) -> Image.Image:
        """Return a thumbnail of the first page of ``path``, never raising."""
        try:
            key = (str(path.resolve()).lower(), path.stat().st_mtime, size)
        except OSError:
            return self._placeholder(size)

        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        image = self._render(path, size) or self._placeholder(size)
        with self._lock:
            self._cache[key] = image
        return image

    def clear(self) -> None:
        """Drop every cached thumbnail."""
        with self._lock:
            self._cache.clear()

    # ----------------------------------------------------------------- #
    # Backends
    # ----------------------------------------------------------------- #
    @staticmethod
    def _detect_backend() -> str:
        """Return the name of the first available rasteriser."""
        try:
            import pypdfium2  # noqa: F401

            return "pypdfium2"
        except ImportError:
            pass
        try:
            import fitz  # noqa: F401  (PyMuPDF)

            return "pymupdf"
        except ImportError:
            pass
        logger.info("No PDF rasteriser installed - using placeholder thumbnails.")
        return "placeholder"

    def _render(self, path: Path, size: tuple[int, int]) -> Image.Image | None:
        """Rasterise the first page, returning ``None`` when unsupported."""
        try:
            if self._backend == "pypdfium2":
                return self._render_pdfium(path, size)
            if self._backend == "pymupdf":
                return self._render_pymupdf(path, size)
        except Exception as exc:  # noqa: BLE001 - thumbnails must never break the UI
            logger.debug("Thumbnail rendering failed for %s: %s", path, exc)
        return None

    @staticmethod
    def _render_pdfium(path: Path, size: tuple[int, int]) -> Image.Image:
        """Render using pypdfium2."""
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            page = document[0]
            scale = max(size) / max(page.get_size()) * 2  # 2x for crisp downscale
            bitmap = page.render(scale=max(scale, 0.1))
            image = bitmap.to_pil().convert("RGB")
        finally:
            document.close()
        return _fit(image, size)

    @staticmethod
    def _render_pymupdf(path: Path, size: tuple[int, int]) -> Image.Image:
        """Render using PyMuPDF."""
        import fitz

        with fitz.open(str(path)) as document:
            page = document.load_page(0)
            zoom = max(size) / max(page.rect.width, page.rect.height) * 2
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return _fit(image, size)

    @staticmethod
    def _placeholder(size: tuple[int, int]) -> Image.Image:
        """Draw a simple page glyph used when rendering is unavailable."""
        width, height = size
        image = Image.new("RGB", size, "#2b2f36")
        draw = ImageDraw.Draw(image)
        draw.rectangle((1, 1, width - 2, height - 2), fill="#f2f3f5", outline="#9aa3ad")
        fold = max(8, width // 4)
        draw.polygon(
            [(width - 2 - fold, 1), (width - 2, 1 + fold), (width - 2 - fold, 1 + fold)],
            fill="#c9ced6",
        )
        line_left, line_right = 5, width - 6
        for offset in range(fold + 8, height - 8, 6):
            draw.line((line_left, offset, line_right, offset), fill="#b8bec7", width=1)
        return image


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale ``image`` to fit inside ``size`` and centre it on a neutral card."""
    thumb = image.copy()
    thumb.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGB", size, "#2b2f36")
    canvas.paste(thumb, ((size[0] - thumb.width) // 2, (size[1] - thumb.height) // 2))
    return canvas


#: Shared instance - thumbnails are cheap to cache and expensive to re-render.
thumbnail_service = ThumbnailService()
