"""claude_usage_tray.icons -- PIL rendering of the tray and window icons.

Move-only extraction from `app.py` (`_make_icon`, `_window_icon_image`); no
pixel changed for any existing call site (see
`openspec/changes/foundations-refactor/design.md`'s characterize-then-move
sequence and `tests/test_icons.py` for the characterization evidence).

These functions take an explicit `palette: Palette` argument -- this is the
one module besides `ui/` allowed to import `theme.py` (see design.md's
layering table: `icons.py | PIL, theme, formatting, paths`).
`formatting.bar_color` reaches the same result from the other side: it stays
L1 and stdlib-only, so it duck-types the palette its caller passes in rather
than importing one (a `formatting` -> `theme` edge would point sideways, and
`tests/test_layering.py` forbids it).
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import formatting
from .theme import Palette


def tray_icon(bundle: dict[str, Any], palette: Palette) -> Image.Image:
    data = bundle.get("primary", bundle)
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    session = formatting.pct(data.get("session"))
    weekly = formatting.pct(data.get("weekly"))

    if data.get("error") and session is None and weekly is None:
        draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=palette.panel)
        draw.rounded_rectangle((28, 14, 36, 40), radius=4, fill=palette.danger)
        draw.ellipse((28, 46, 36, 54), fill=palette.danger)
        return img

    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=palette.panel)
    active_bars = formatting.tray_level(formatting.availability_pct_from_bundle(bundle))
    heights = [14, 22, 30, 38]
    left = 13
    bottom = 50
    bar_width = 8
    gap = 4

    for index, height in enumerate(heights, start=1):
        x1 = left + (index - 1) * (bar_width + gap)
        x2 = x1 + bar_width
        y1 = bottom - height
        draw.rounded_rectangle((x1, y1, x2, bottom), radius=3, fill=palette.track)
        if index <= active_bars:
            draw.rounded_rectangle((x1, y1, x2, bottom), radius=3, fill=palette.accent)

    draw.rounded_rectangle((11, 10, 53, 52), radius=10, outline=palette.border, width=2)
    return img


def window_icon(palette: Palette, size: int = 32) -> Image.Image:
    img = Image.new("RGBA", (size, size), palette.bg)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=7, fill=palette.panel, outline=palette.border, width=1)
    heights = [7, 11, 15, 19]
    left = 6
    bottom = size - 6
    bar_width = 4
    gap = 2
    for index, height in enumerate(heights):
        x1 = left + index * (bar_width + gap)
        x2 = x1 + bar_width
        y1 = bottom - height
        draw.rounded_rectangle((x1, y1, x2, bottom), radius=2, fill=palette.accent)
    return img
