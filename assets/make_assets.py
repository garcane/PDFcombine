"""Generate the application logo and Windows icon.

Run once (or after changing the palette) to regenerate ``assets/logo.png`` and
``assets/app.ico``::

    python assets/make_assets.py

Keeping the branding as generated art means the repository has no binary
dependencies that cannot be rebuilt from source.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent
ACCENT = "#3b82f6"
ACCENT_DARK = "#1d4ed8"
PAPER = "#f8fafc"
FOLD = "#cbd5e1"
INK = "#0f172a"


def draw_logo(size: int = 512) -> Image.Image:
    """Draw the logo: a rounded accent tile holding a folded page glyph."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = size // 16
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 6,
        fill=ACCENT,
        outline=ACCENT_DARK,
        width=max(2, size // 128),
    )

    # Page body with a folded top-right corner.
    left = size * 0.28
    right = size * 0.72
    top = size * 0.24
    bottom = size * 0.76
    fold = size * 0.16

    draw.polygon(
        [
            (left, top),
            (right - fold, top),
            (right, top + fold),
            (right, bottom),
            (left, bottom),
        ],
        fill=PAPER,
    )
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=FOLD,
    )

    # Three text lines and the merge arrow beneath them.
    line_x0 = left + size * 0.06
    line_x1 = right - size * 0.06
    for index in range(3):
        y = top + fold + size * (0.08 + index * 0.09)
        draw.rounded_rectangle(
            (line_x0, y, line_x1, y + size * 0.035), radius=size // 90, fill=FOLD
        )

    arrow_y = bottom - size * 0.14
    centre = (left + right) / 2
    draw.line((centre, arrow_y - size * 0.09, centre, arrow_y), fill=ACCENT_DARK, width=size // 40)
    draw.polygon(
        [
            (centre - size * 0.05, arrow_y),
            (centre + size * 0.05, arrow_y),
            (centre, arrow_y + size * 0.07),
        ],
        fill=ACCENT_DARK,
    )
    return image


def main() -> None:
    """Write the PNG logo and the multi-resolution ICO."""
    logo = draw_logo()
    logo.save(ASSETS / "logo.png")

    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    logo.save(ASSETS / "app.ico", sizes=icon_sizes)

    icons_dir = ASSETS / "icons"
    icons_dir.mkdir(exist_ok=True)
    logo.resize((64, 64), Image.LANCZOS).save(icons_dir / "app-64.png")
    print(f"Wrote {ASSETS / 'logo.png'} and {ASSETS / 'app.ico'}")


if __name__ == "__main__":
    main()
