"""Generate the kai extension icons (16, 32, 48, 128 px).

The icon is drawn in code so it can be re-made without design tools:

    uv run --with pillow python chrome-extension/scripts/generate_icons.py

Each size is drawn on a large canvas and scaled down, which keeps edges
smooth. The 16 px size uses thicker strokes so the "k" stays readable in
the toolbar.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

CANVAS = 1024
SIZES = (16, 32, 48, 128)
OUT_DIR = Path(__file__).resolve().parent.parent / "icons"

# Violet that matches the popup accent but is saturated enough to stand
# out on both light and dark toolbars.
TOP = (167, 112, 255)
BOTTOM = (99, 46, 214)
GLYPH = (255, 255, 255)


def _gradient_tile(margin: int, radius: int) -> Image.Image:
    """Rounded square with a top-to-bottom violet gradient."""
    gradient = Image.new("RGBA", (CANVAS, CANVAS))
    pixels = gradient.load()
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True))
        for x in range(CANVAS):
            pixels[x, y] = (*row, 255)

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, CANVAS - 1 - margin, CANVAS - 1 - margin),
        radius=radius,
        fill=255,
    )
    gradient.putalpha(mask)
    return gradient


def _glyph_mask(stroke: int) -> Image.Image:
    """Geometric lowercase-style "k": one stem and two arms.

    Arms are drawn longer than needed, then clipped to the glyph's box so
    their ends are flat instead of slanted.
    """
    top, bottom = 230, 794
    stem_left = 300
    stem_right = stem_left + stroke
    arm_right = 760
    junction_y = 560

    lines = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(lines)
    draw.rectangle((stem_left, top, stem_right, bottom), fill=255)
    # Upper arm: from the stem up to the top right.
    draw.line(
        (stem_right - 20, junction_y + 40, arm_right + 120, top - 160), fill=255, width=stroke
    )
    # Lower arm: from the middle of the upper arm down to the bottom right.
    draw.line(
        (stem_right + 90, junction_y - 70, arm_right + 140, bottom + 200), fill=255, width=stroke
    )

    box = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(box).rectangle((stem_left, top, arm_right, bottom), fill=255)
    return ImageChops.multiply(lines, box)


def render(size: int) -> Image.Image:
    small = size <= 16
    margin = 0 if small else CANVAS // 32
    tile = _gradient_tile(margin=margin, radius=CANVAS * 22 // 100)
    glyph = _glyph_mask(stroke=170 if small else 140)
    tile.paste(Image.new("RGBA", (CANVAS, CANVAS), (*GLYPH, 255)), mask=glyph)
    return tile.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for size in SIZES:
        render(size).save(OUT_DIR / f"icon-{size}.png", optimize=True)


if __name__ == "__main__":
    main()
