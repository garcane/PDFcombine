"""Re-orderable PDF list used by the merge review screen.

The list widget owns the ordering and is the single source of truth for the
merge operation - the order shown here is always the order written out, never
the order the operating system happened to hand the files over in.

Re-ordering works two ways:

* drag a row by any part of it (a drop indicator follows the pointer), and
* the **Move up** / **Move down** buttons on each row, plus keyboard shortcuts.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import customtkinter as ctk

from core.constants import THUMBNAIL_SIZE
from core.models import PdfFileInfo
from core.thumbnails import thumbnail_service
from core.utils import truncate_middle
from ui.theme import COLORS, PAD, fonts

logger = logging.getLogger(__name__)

ROW_HEIGHT = 68


class FileRow(ctk.CTkFrame):
    """A single file entry: thumbnail, name, metadata and row actions."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        info: PdfFileInfo,
        index: int,
        on_select: Callable[["FileRow"], None],
        on_remove: Callable[["FileRow"], None],
        on_move: Callable[["FileRow", int], None],
        on_drag_start: Callable[["FileRow", object], None],
        on_drag_motion: Callable[["FileRow", object], None],
        on_drag_end: Callable[["FileRow", object], None],
    ) -> None:
        """Build the row for ``info`` at 0-based ``index``."""
        super().__init__(
            master,
            fg_color=COLORS.surface_alt,
            corner_radius=8,
            border_width=1,
            border_color=COLORS.surface_alt,
            height=ROW_HEIGHT,
        )
        self.info = info
        self._on_select = on_select
        self._selected = False

        self.grid_propagate(False)
        self.grid_columnconfigure(3, weight=1)

        self._position = ctk.CTkLabel(
            self,
            text=f"{index + 1}",
            width=30,
            font=fonts().small_bold,
            text_color=COLORS.text_disabled,
        )
        self._position.grid(row=0, column=0, rowspan=2, padx=(PAD.sm, 0), pady=PAD.sm)

        self._handle = ctk.CTkLabel(
            self, text="⠿", width=18, font=fonts().body, text_color=COLORS.text_disabled
        )
        self._handle.grid(row=0, column=1, rowspan=2, padx=(0, PAD.xs))
        self._handle.configure(cursor="fleur")

        self._thumbnail = ctk.CTkLabel(self, text="", width=THUMBNAIL_SIZE[0])
        self._thumbnail.grid(row=0, column=2, rowspan=2, padx=(PAD.xs, PAD.md), pady=PAD.sm)

        self._name = ctk.CTkLabel(
            self,
            text=truncate_middle(info.name, 60),
            font=fonts().body_bold,
            text_color=COLORS.text,
            anchor="w",
        )
        self._name.grid(row=0, column=3, sticky="ew", pady=(PAD.sm, 0))

        self._meta = ctk.CTkLabel(
            self,
            text=f"{info.page_label}  ·  {info.size_label}  ·  {info.modified_label}",
            font=fonts().small,
            text_color=COLORS.text_muted,
            anchor="w",
        )
        self._meta.grid(row=1, column=3, sticky="ew", pady=(0, PAD.sm))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=4, rowspan=2, padx=(PAD.sm, PAD.sm))

        self._make_action(actions, "▲", "Move up", lambda: on_move(self, -1)).pack(side="left")
        self._make_action(actions, "▼", "Move down", lambda: on_move(self, 1)).pack(
            side="left", padx=PAD.xs
        )
        self._make_action(
            actions, "✕", "Remove", lambda: on_remove(self), danger=True
        ).pack(side="left")

        for widget in (self, self._position, self._handle, self._thumbnail, self._name, self._meta):
            widget.bind("<Button-1>", lambda event: self._begin(event, on_drag_start))
            widget.bind("<B1-Motion>", lambda event: on_drag_motion(self, event))
            widget.bind("<ButtonRelease-1>", lambda event: on_drag_end(self, event))
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    # ----------------------------------------------------------------- #
    # Appearance
    # ----------------------------------------------------------------- #
    def set_index(self, index: int) -> None:
        """Update the 1-based position badge."""
        self._position.configure(text=f"{index + 1}")

    def set_thumbnail(self, image: ctk.CTkImage) -> None:
        """Attach a rendered thumbnail image."""
        self._thumbnail.configure(image=image)
        self._thumbnail.image = image  # keep a reference alive

    def set_selected(self, selected: bool) -> None:
        """Toggle the selected highlight."""
        self._selected = selected
        self.configure(
            border_color=COLORS.accent if selected else COLORS.surface_alt,
            fg_color=COLORS.surface_hover if selected else COLORS.surface_alt,
        )

    def set_dragging(self, dragging: bool) -> None:
        """Show or clear the "being dragged" appearance."""
        self.configure(
            border_color=COLORS.accent if dragging else (
                COLORS.accent if self._selected else COLORS.surface_alt
            ),
            fg_color=COLORS.surface_hover if dragging or self._selected else COLORS.surface_alt,
        )

    def _on_enter(self, _event: object) -> None:
        """Hover highlight."""
        if not self._selected:
            self.configure(fg_color=COLORS.surface_hover)

    def _on_leave(self, _event: object) -> None:
        """Remove the hover highlight."""
        if not self._selected:
            self.configure(fg_color=COLORS.surface_alt)

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    def _begin(self, event: object, on_drag_start: Callable[["FileRow", object], None]) -> None:
        """Select the row and arm a potential drag."""
        self._on_select(self)
        on_drag_start(self, event)

    def _make_action(
        self,
        parent: ctk.CTkFrame,
        glyph: str,
        tooltip: str,
        command: Callable[[], None],
        danger: bool = False,
    ) -> ctk.CTkButton:
        """Create one of the compact per-row action buttons."""
        return ctk.CTkButton(
            parent,
            text=glyph,
            width=30,
            height=28,
            corner_radius=6,
            command=command,
            fg_color=COLORS.neutral_button,
            hover_color=COLORS.danger if danger else COLORS.accent,
            text_color=COLORS.text,
            font=fonts().small,
        )


class FileListPanel(ctk.CTkFrame):
    """Scrollable, re-orderable list of :class:`PdfFileInfo` objects."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_change: Callable[[], None],
        empty_widget_factory: Callable[[ctk.CTkBaseClass], ctk.CTkBaseClass] | None = None,
    ) -> None:
        """Create the panel.

        Args:
            master: Parent widget.
            on_change: Called whenever the list contents or order change.
            empty_widget_factory: Builds the placeholder shown when empty.
        """
        super().__init__(master, fg_color="transparent")

        self._files: list[PdfFileInfo] = []
        self._rows: list[FileRow] = []
        self._on_change = on_change
        self._selected: FileRow | None = None
        self._drag_row: FileRow | None = None
        self._drag_active = False
        self._drag_origin_y = 0

        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS.surface,
            corner_radius=10,
            border_width=1,
            border_color=COLORS.border,
            scrollbar_button_color=COLORS.border_strong,
            scrollbar_button_hover_color=COLORS.accent,
        )
        self._scroll.pack(fill="both", expand=True)

        self._empty_factory = empty_widget_factory
        self._empty_widget: ctk.CTkBaseClass | None = None

        self._thumbnail_queue: queue.Queue[tuple[FileRow, PdfFileInfo] | None] = queue.Queue()
        self._thumbnail_thread = threading.Thread(
            target=self._thumbnail_worker, name="pdf-thumbnails", daemon=True
        )
        self._thumbnail_thread.start()

        self._render()

    # ----------------------------------------------------------------- #
    # Data access
    # ----------------------------------------------------------------- #
    @property
    def files(self) -> list[PdfFileInfo]:
        """The files in their current display order (a copy)."""
        return list(self._files)

    @property
    def count(self) -> int:
        """Number of files in the list."""
        return len(self._files)

    @property
    def total_pages(self) -> int:
        """Combined page count of every file."""
        return sum(info.page_count for info in self._files)

    @property
    def total_size(self) -> int:
        """Combined size in bytes of every file."""
        return sum(info.size_bytes for info in self._files)

    def keys(self) -> set[str]:
        """Identity keys of the current files, for duplicate detection."""
        return {info.key for info in self._files}

    # ----------------------------------------------------------------- #
    # Mutations
    # ----------------------------------------------------------------- #
    def set_files(self, files: list[PdfFileInfo]) -> None:
        """Replace the whole list."""
        self._files = list(files)
        self._render()
        self._on_change()

    def add_files(self, files: list[PdfFileInfo]) -> int:
        """Append ``files``, skipping ones already present.

        Returns:
            The number of files actually added.
        """
        existing = self.keys()
        added = 0
        for info in files:
            if info.key in existing:
                continue
            existing.add(info.key)
            self._files.append(info)
            added += 1
        if added:
            self._render()
            self._on_change()
        return added

    def remove(self, info: PdfFileInfo) -> None:
        """Remove one file from the list."""
        self._files = [item for item in self._files if item.key != info.key]
        self._render()
        self._on_change()

    def remove_selected(self) -> bool:
        """Remove the highlighted row. Returns ``True`` when something went."""
        if self._selected is None:
            return False
        self.remove(self._selected.info)
        return True

    def clear(self) -> None:
        """Empty the list."""
        self._files = []
        self._render()
        self._on_change()

    def sort_alphabetically(self) -> None:
        """Sort by file name using a natural (``file2`` < ``file10``) order."""
        from core.utils import natural_sort_key

        self._files.sort(key=lambda info: natural_sort_key(info.name))
        self._render()
        self._on_change()

    def reverse(self) -> None:
        """Reverse the current order."""
        self._files.reverse()
        self._render()
        self._on_change()

    def move_selected(self, delta: int) -> bool:
        """Move the highlighted row by ``delta`` positions."""
        if self._selected is None:
            return False
        return self._move_info(self._selected.info, delta)

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #
    def _render(self) -> None:
        """Rebuild every row from :attr:`_files`."""
        selected_key = self._selected.info.key if self._selected else None
        for row in self._rows:
            row.destroy()
        self._rows.clear()
        self._selected = None

        if self._empty_widget is not None:
            self._empty_widget.destroy()
            self._empty_widget = None

        if not self._files:
            if self._empty_factory is not None:
                self._empty_widget = self._empty_factory(self._scroll)
                self._empty_widget.pack(fill="both", expand=True, pady=PAD.xl)
            return

        for index, info in enumerate(self._files):
            row = FileRow(
                self._scroll,
                info=info,
                index=index,
                on_select=self._select_row,
                on_remove=lambda r: self.remove(r.info),
                on_move=lambda r, delta: self._move_info(r.info, delta),
                on_drag_start=self._drag_start,
                on_drag_motion=self._drag_motion,
                on_drag_end=self._drag_end,
            )
            row.pack(fill="x", padx=PAD.sm, pady=(PAD.xs if index else PAD.sm, PAD.xs))
            self._rows.append(row)
            self._thumbnail_queue.put((row, info))
            if info.key == selected_key:
                self._select_row(row)

    def _repack(self) -> None:
        """Re-apply pack order and position badges after a re-order."""
        for index, row in enumerate(self._rows):
            row.pack_forget()
            row.pack(fill="x", padx=PAD.sm, pady=(PAD.xs if index else PAD.sm, PAD.xs))
            row.set_index(index)

    def _select_row(self, row: FileRow) -> None:
        """Highlight ``row`` and clear any previous selection."""
        if self._selected is row:
            return
        if self._selected is not None:
            self._selected.set_selected(False)
        self._selected = row
        row.set_selected(True)

    def _move_info(self, info: PdfFileInfo, delta: int) -> bool:
        """Move ``info`` by ``delta`` positions, clamped to the list bounds."""
        index = next((i for i, item in enumerate(self._files) if item.key == info.key), None)
        if index is None:
            return False
        target = index + delta
        if not 0 <= target < len(self._files):
            return False
        self._files.insert(target, self._files.pop(index))
        self._rows.insert(target, self._rows.pop(index))
        self._repack()
        self._on_change()
        return True

    # ----------------------------------------------------------------- #
    # Drag re-ordering
    # ----------------------------------------------------------------- #
    def _drag_start(self, row: FileRow, event: object) -> None:
        """Arm a drag; it only activates once the pointer actually moves."""
        self._drag_row = row
        self._drag_active = False
        self._drag_origin_y = self._pointer_y()

    def _drag_motion(self, _row: FileRow, _event: object) -> None:
        """Re-order live as the pointer passes other rows."""
        if self._drag_row is None or self._drag_row not in self._rows:
            return
        pointer_y = self._pointer_y()
        if not self._drag_active:
            if abs(pointer_y - self._drag_origin_y) < 6:
                return
            self._drag_active = True
            self._drag_row.set_dragging(True)

        current = self._rows.index(self._drag_row)
        target = self._index_at(pointer_y, default=current)
        if target != current:
            self._files.insert(target, self._files.pop(current))
            self._rows.insert(target, self._rows.pop(current))
            self._repack()

    def _drag_end(self, _row: FileRow, _event: object) -> None:
        """Finish the drag and notify listeners if the order changed."""
        if self._drag_row is not None and self._drag_active:
            self._drag_row.set_dragging(False)
            self._on_change()
        self._drag_row = None
        self._drag_active = False

    def _pointer_y(self) -> int:
        """Pointer position in the coordinate space of the scrolling frame."""
        inner = self._scroll  # CTkScrollableFrame proxies geometry to its canvas
        try:
            return inner.winfo_pointery() - inner.winfo_rooty()
        except Exception:  # noqa: BLE001 - widget may be unmapped mid-drag
            return 0

    def _index_at(self, pointer_y: int, default: int) -> int:
        """Return the row index whose vertical span contains ``pointer_y``."""
        for index, row in enumerate(self._rows):
            try:
                top = row.winfo_y()
                bottom = top + row.winfo_height()
            except Exception:  # noqa: BLE001 - defensive
                continue
            if top <= pointer_y <= bottom:
                return index
        if self._rows:
            first_top = self._rows[0].winfo_y()
            last = self._rows[-1]
            if pointer_y < first_top:
                return 0
            if pointer_y > last.winfo_y() + last.winfo_height():
                return len(self._rows) - 1
        return default

    # ----------------------------------------------------------------- #
    # Thumbnails
    # ----------------------------------------------------------------- #
    def _thumbnail_worker(self) -> None:
        """Render thumbnails off the UI thread until the panel is destroyed."""
        while True:
            item = self._thumbnail_queue.get()
            if item is None:
                return
            row, info = item
            try:
                image = thumbnail_service.get(info.path, THUMBNAIL_SIZE)
            except Exception:  # noqa: BLE001 - thumbnails are decorative
                logger.debug("Thumbnail failed for %s", info.path, exc_info=True)
                continue
            self._apply_thumbnail_later(row, image)

    def _apply_thumbnail_later(self, row: FileRow, image: object) -> None:
        """Schedule the thumbnail update on the UI thread."""
        def apply() -> None:
            if not row.winfo_exists():
                return
            row.set_thumbnail(
                ctk.CTkImage(light_image=image, dark_image=image, size=THUMBNAIL_SIZE)  # type: ignore[arg-type]
            )

        try:
            self.after(0, apply)
        except Exception:  # noqa: BLE001 - panel destroyed while rendering
            pass

    def shutdown(self) -> None:
        """Stop the thumbnail worker. Called when the view is destroyed."""
        self._thumbnail_queue.put(None)
