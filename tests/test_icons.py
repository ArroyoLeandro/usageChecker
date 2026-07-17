"""Characterization tests for claude_usage_tray.icons.

These assertions were originally written and run against
`claude_usage_tray.app`'s `_make_icon`/`_window_icon_image` (tasks.md 5.1),
*before* `icons.py` existed -- golden SHA-256 digests were captured by
actually executing the then-current `app.py` code, not guessed. Task 5.3
re-points the same assertion bodies at the new module: `tray_icon`/
`window_icon` take an explicit `palette: Palette` argument (`theme.DARK`,
whose hex fields are byte-for-byte the same literals `app.py` used) instead
of reading module-level color constants, which is the one signature
difference the design calls for; every digest is unchanged, proving the
move altered no pixel.

A whole-image digest (size + mode + raw pixel bytes) is used instead of
hand-picked pixel samples alone: it is a strictly stronger characterization,
since it would catch a change to any pixel, not just the ones a human
thought to sample. One pixel spot-check is kept alongside for a legible
failure signal if a digest ever breaks.
"""

from __future__ import annotations

import hashlib

from PIL import ImageColor

from claude_usage_tray.icons import tray_icon, window_icon
from claude_usage_tray.theme import DARK


def _digest(img) -> str:
    return hashlib.sha256(img.tobytes() + str(img.size).encode() + img.mode.encode()).hexdigest()


def test_tray_icon_error_bundle_matches_golden_digest():
    bundle = {"primary": {"error": "boom"}}
    img = tray_icon(bundle, DARK)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"
    assert _digest(img) == "37ba4e1fe52b002de2d9ba58974c720d4f39866eee9717c6358705c00add63e0"


def test_tray_icon_high_availability_matches_golden_digest():
    bundle = {"primary": {"session": {"utilization": 10}, "weekly": {"utilization": 5}}}
    img = tray_icon(bundle, DARK)
    assert _digest(img) == "61b1c97bcd6955d159914e5c8a9b25c5f996b8e9eb55cca851097130cf063cc0"


def test_tray_icon_medium_availability_matches_golden_digest():
    bundle = {"primary": {"session": {"utilization": 60}}}
    img = tray_icon(bundle, DARK)
    assert _digest(img) == "351ccf57678307f1045475e2c7ab671cfe0ed23b53ad0a0bc776ce710321ec96"


def test_tray_icon_active_bar_pixel_is_accent_colored():
    # Full availability lights up all four bars in `palette.accent`.
    # x=50, not the bar's exact center (53): the icon's outer border stroke
    # (drawn last, at x=53) passes directly through bar 4's center column.
    bundle = {"primary": {"session": {"utilization": 10}, "weekly": {"utilization": 5}}}
    img = tray_icon(bundle, DARK)
    expected = ImageColor.getcolor(DARK.accent, "RGBA")
    for x in (17, 29, 41, 50):
        assert img.getpixel((x, 45)) == expected


def test_window_icon_matches_golden_digest():
    img = window_icon(DARK)
    assert img.size == (32, 32)
    assert img.mode == "RGBA"
    assert _digest(img) == "e9fc14c2c6ea1d4c7b36e91d11f8ab0fd562b54fbec0ec54d7971d931a4476a7"


def test_window_icon_respects_custom_size():
    img = window_icon(DARK, size=64)
    assert img.size == (64, 64)
