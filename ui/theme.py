"""Centralised colours, fonts and spacing.

Every widget in the application pulls its styling from here, so the whole look
can be re-tuned in one place. ``CTkFont`` objects require an existing Tk root,
so fonts are created lazily through :func:`fonts` after the root window exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import customtkinter as ctk


# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Palette:
    """Colour tokens for the dark theme."""

    background: str = "#14171c"
    surface: str = "#1b1f26"
    surface_alt: str = "#232830"
    surface_hover: str = "#2a303a"
    border: str = "#2f3641"
    border_strong: str = "#3d4652"

    text: str = "#e7eaf0"
    text_muted: str = "#98a2b3"
    text_disabled: str = "#6b7480"

    accent: str = "#3b82f6"
    accent_hover: str = "#2f6ad9"
    accent_soft: str = "#1e3a5f"

    success: str = "#22c55e"
    success_hover: str = "#16a34a"
    warning: str = "#f59e0b"
    danger: str = "#ef4444"
    danger_hover: str = "#dc2626"

    neutral_button: str = "#2b313b"
    neutral_button_hover: str = "#353d49"


COLORS: Final[Palette] = Palette()


# --------------------------------------------------------------------------- #
# Spacing / geometry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Spacing:
    """Consistent padding steps (px)."""

    xs: int = 4
    sm: int = 8
    md: int = 14
    lg: int = 20
    xl: int = 30
    xxl: int = 44


PAD: Final[Spacing] = Spacing()

CORNER_RADIUS: Final[int] = 10
CARD_RADIUS: Final[int] = 14
BUTTON_HEIGHT: Final[int] = 42
BUTTON_HEIGHT_SM: Final[int] = 34
BUTTON_HEIGHT_LG: Final[int] = 52


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Fonts:
    """Font objects used across the application."""

    display: ctk.CTkFont
    title: ctk.CTkFont
    heading: ctk.CTkFont
    subheading: ctk.CTkFont
    body: ctk.CTkFont
    body_bold: ctk.CTkFont
    small: ctk.CTkFont
    small_bold: ctk.CTkFont
    mono: ctk.CTkFont
    icon: ctk.CTkFont


_FONTS: Fonts | None = None
_FAMILY: Final[str] = "Segoe UI"
_MONO_FAMILY: Final[str] = "Consolas"


def apply_theme() -> None:
    """Set the global CustomTkinter appearance. Call before creating the root."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def init_fonts() -> Fonts:
    """Create the font singleton. Must be called after the Tk root exists."""
    global _FONTS
    if _FONTS is None:
        _FONTS = Fonts(
            display=ctk.CTkFont(family=_FAMILY, size=34, weight="bold"),
            title=ctk.CTkFont(family=_FAMILY, size=24, weight="bold"),
            heading=ctk.CTkFont(family=_FAMILY, size=18, weight="bold"),
            subheading=ctk.CTkFont(family=_FAMILY, size=15, weight="bold"),
            body=ctk.CTkFont(family=_FAMILY, size=14),
            body_bold=ctk.CTkFont(family=_FAMILY, size=14, weight="bold"),
            small=ctk.CTkFont(family=_FAMILY, size=12),
            small_bold=ctk.CTkFont(family=_FAMILY, size=12, weight="bold"),
            mono=ctk.CTkFont(family=_MONO_FAMILY, size=13),
            icon=ctk.CTkFont(family="Segoe UI Emoji", size=30),
        )
    return _FONTS


def fonts() -> Fonts:
    """Return the font singleton, creating it on first use."""
    return _FONTS or init_fonts()


# --------------------------------------------------------------------------- #
# Reusable widget style presets
# --------------------------------------------------------------------------- #
def primary_button_style() -> dict[str, object]:
    """Keyword arguments for the main call-to-action button."""
    return {
        "fg_color": COLORS.accent,
        "hover_color": COLORS.accent_hover,
        "text_color": "#ffffff",
        "corner_radius": CORNER_RADIUS,
        "height": BUTTON_HEIGHT,
        "font": fonts().body_bold,
    }


def secondary_button_style() -> dict[str, object]:
    """Keyword arguments for neutral, secondary actions."""
    return {
        "fg_color": COLORS.neutral_button,
        "hover_color": COLORS.neutral_button_hover,
        "text_color": COLORS.text,
        "corner_radius": CORNER_RADIUS,
        "height": BUTTON_HEIGHT,
        "font": fonts().body,
    }


def small_button_style() -> dict[str, object]:
    """Keyword arguments for compact toolbar buttons."""
    return {
        "fg_color": COLORS.neutral_button,
        "hover_color": COLORS.neutral_button_hover,
        "text_color": COLORS.text,
        "corner_radius": 8,
        "height": BUTTON_HEIGHT_SM,
        "font": fonts().small,
    }


def danger_button_style() -> dict[str, object]:
    """Keyword arguments for destructive actions."""
    return {
        "fg_color": COLORS.danger,
        "hover_color": COLORS.danger_hover,
        "text_color": "#ffffff",
        "corner_radius": CORNER_RADIUS,
        "height": BUTTON_HEIGHT,
        "font": fonts().body_bold,
    }


def success_button_style() -> dict[str, object]:
    """Keyword arguments for confirm/finish actions."""
    return {
        "fg_color": COLORS.success,
        "hover_color": COLORS.success_hover,
        "text_color": "#0b1a10",
        "corner_radius": CORNER_RADIUS,
        "height": BUTTON_HEIGHT,
        "font": fonts().body_bold,
    }


def entry_style() -> dict[str, object]:
    """Keyword arguments for text entries."""
    return {
        "fg_color": COLORS.surface_alt,
        "border_color": COLORS.border,
        "text_color": COLORS.text,
        "placeholder_text_color": COLORS.text_disabled,
        "corner_radius": 8,
        "height": BUTTON_HEIGHT,
        "font": fonts().body,
    }
