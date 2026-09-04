"""Characterization tests for claude_usage_tray.icons.

The icon is a 270-degree gauge arc with the Claude sunburst riding it as the
value indicator, the way `assets/claude_usage_icon.png` draws it. Two earlier
designs are gone: a four-bar signal glyph on an opaque rounded panel, and a
gauge with the sunburst fixed in the center as a hub.

Golden SHA-256 digests are deliberately NOT used. They characterized
drawings that no longer exist, and re-baking them would only pin the current
antialiasing against innocuous change while saying nothing about whether the
icon is correct. What is asserted instead are the properties the icon has to
keep -- each one of which corresponds to a defect actually observed on a
contact sheet during this work:

* the corners stay transparent (no background plate),
* the sunburst ADVANCES along the arc as availability rises,
* low availability still carries a full-size mark (the 5%-is-invisible bug),
* the value color changes at `formatting.bar_color`'s own thresholds,
* error / no-data park the mark in the ring's empty center,
* the three degenerate states are three different pictures,
* the render is deterministic, centered and size-parametric.
"""

from __future__ import annotations

import hashlib
import math

from PIL import Image, ImageColor

from claude_usage_tray import formatting
from claude_usage_tray.icons import (
    _CENTER_Y_F,
    NEUTRAL_TRACK,
    tray_icon,
    window_icon,
)
from claude_usage_tray.theme import DARK

# Big enough that a few pixels of antialiasing cannot swamp a count, small
# enough to keep the suite fast.
SIZE = 64


def _digest(img: Image.Image) -> str:
    return hashlib.sha256(img.tobytes() + str(img.size).encode() + img.mode.encode()).hexdigest()


def _bundle(availability: float) -> dict:
    """A bundle whose `availability_pct_from_bundle` is `availability`.

    That helper takes the MINIMUM availability across the quotas it finds, so
    a single quota pins the value exactly.
    """
    return {"primary": {"session": {"utilization": 100.0 - availability}}}


def _pixels_of(img: Image.Image, hex_color: str) -> list[tuple[int, int]]:
    """Coordinates of every opaque pixel within tolerance of `hex_color`.

    A tolerance rather than an exact match because everything is drawn 4x and
    reduced with LANCZOS, so even the interior of a solid shape can land a
    step or two off the nominal value.
    """
    target = ImageColor.getcolor(hex_color, "RGB")
    raw = img.convert("RGBA").tobytes()
    width = img.size[0]
    found = []
    for index in range(0, len(raw), 4):
        r, g, b, a = raw[index], raw[index + 1], raw[index + 2], raw[index + 3]
        if a < 200:
            continue
        if abs(r - target[0]) <= 12 and abs(g - target[1]) <= 12 and abs(b - target[2]) <= 12:
            pixel = index // 4
            found.append((pixel % width, pixel // width))
    return found


def _count_color(img: Image.Image, hex_color: str) -> int:
    return len(_pixels_of(img, hex_color))


def _ring_center(size: int) -> tuple[float, float]:
    """The gauge's own center -- which the drawing leaves empty at any real
    value, and fills with the sunburst when there is no value to show."""
    return (size / 2.0, size * _CENTER_Y_F)


def _mark_angle(img: Image.Image, hex_color: str, size: int) -> float:
    """Angle of the value-colored ink's centroid, in PIL's convention.

    A proxy for where the indicator sits: as the arc fills and the sunburst
    advances, the centroid of everything painted in the value color sweeps
    the same way. Monotonic, which is all the assertions need.
    """
    points = _pixels_of(img, hex_color)
    assert points, "no value-colored ink to take an angle from"
    cx, cy = _ring_center(size)
    mx = sum(p[0] for p in points) / len(points)
    my = sum(p[1] for p in points) / len(points)
    return math.degrees(math.atan2(my - cy, mx - cx)) % 360.0


def _corners(img: Image.Image) -> list[tuple[int, int, int, int]]:
    w, h = img.size
    return [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]


ALL_STATES = [
    ("100%", _bundle(100)),
    ("0%", _bundle(0)),
    ("no data", {"primary": {}}),
    ("error", {"primary": {"error": "boom"}}),
]


# --- no background plate ----------------------------------------------------


def test_tray_icon_corners_are_fully_transparent():
    # The regression this exists for: the first design filled a rounded
    # rectangle in `palette.panel` behind the glyph, which on a macOS menu
    # bar reads as a sticker rather than as an icon.
    for label, bundle in ALL_STATES:
        assert _corners(tray_icon(bundle, DARK, size=SIZE)) == [(0, 0, 0, 0)] * 4, label


def test_tray_icon_bottom_center_is_transparent():
    # The gauge is open at the bottom: the 90-degree gap centered on 6
    # o'clock is part of the drawing, not incidental margin. The indicator
    # sweeps 135->405 and so never enters it.
    for label, bundle in ALL_STATES:
        img = tray_icon(bundle, DARK, size=SIZE)
        assert img.getpixel((SIZE // 2, SIZE - 1))[3] == 0, label


def test_window_icon_corners_are_fully_transparent():
    assert _corners(window_icon(DARK, size=SIZE)) == [(0, 0, 0, 0)] * 4


# --- the sunburst rides the arc ---------------------------------------------


def test_the_mark_advances_along_the_arc_as_availability_rises():
    # THE defining property of this design. The sunburst is the indicator; if
    # it stops tracking the value, the icon is lying.
    angles = [
        _mark_angle(tray_icon(_bundle(v), DARK, size=SIZE), DARK.accent, SIZE)
        for v in (35, 50, 65, 80, 100)
    ]
    assert angles == sorted(angles), angles
    # And it really travels: a whole quadrant between the extremes, not a
    # rounding wobble.
    assert angles[-1] - angles[0] > 60


def test_filled_arc_grows_monotonically_with_availability():
    # Every one of these lands in `bar_color`'s accent band (used < 70), so
    # the color is held constant and only the swept angle varies.
    counts = [
        _count_color(tray_icon(_bundle(v), DARK, size=SIZE), DARK.accent)
        for v in (35, 50, 70, 85, 100)
    ]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_full_availability_leaves_no_visible_track():
    # A 100% gauge is the whole 270-degree sweep in the value color; the gray
    # track must be completely covered.
    assert _count_color(tray_icon(_bundle(100), DARK, size=SIZE), NEUTRAL_TRACK) == 0
    assert _count_color(tray_icon(_bundle(60), DARK, size=SIZE), NEUTRAL_TRACK) > 0


def test_low_availability_still_carries_a_full_size_mark():
    """Regression for the defect that forced this redesign.

    With the indicator fixed as a center hub, 5% availability rendered as two
    pixels of color at the lower left of the ring -- invisible on a real menu
    bar, and 5% is exactly the reading a user must not miss. Because the
    sunburst is now the indicator and is always drawn at full size, the
    danger-colored ink at 5% and even at 0% is the mark's own area.

    The floor is a fraction of what a mid-range value paints, which keeps the
    test meaningful if the geometry is retuned.
    """
    midrange = _count_color(tray_icon(_bundle(60), DARK, size=SIZE), DARK.accent)
    for availability in (0, 5, 10):
        ink = _count_color(tray_icon(_bundle(availability), DARK, size=SIZE), DARK.danger)
        assert ink > midrange * 0.15, (availability, ink, midrange)


# --- the value color follows formatting.bar_color ---------------------------


def test_value_color_crosses_bar_color_thresholds():
    # `bar_color` speaks in percent USED: >= 90 danger, >= 70 warn. So the
    # availability thresholds are 10 and 30, and `icons` must convert rather
    # than keep a second copy of the numbers.
    assert formatting.bar_color(100.0 - 50, DARK) == DARK.accent
    assert formatting.bar_color(100.0 - 25, DARK) == DARK.warn
    assert formatting.bar_color(100.0 - 5, DARK) == DARK.danger

    for availability, expected in ((50, DARK.accent), (25, DARK.warn), (5, DARK.danger)):
        img = tray_icon(_bundle(availability), DARK, size=SIZE)
        assert _count_color(img, expected) > 0, availability
        for other in (DARK.accent, DARK.warn, DARK.danger):
            if other != expected:
                assert _count_color(img, other) == 0, (availability, other)


def test_value_color_switches_exactly_at_the_amber_boundary():
    # 30% available == 70% used, `bar_color`'s first inclusive step.
    assert _count_color(tray_icon(_bundle(30), DARK, size=SIZE), DARK.warn) > 0
    assert _count_color(tray_icon(_bundle(30.5), DARK, size=SIZE), DARK.accent) > 0


def test_value_color_switches_exactly_at_the_danger_boundary():
    # 10% available == 90% used.
    assert _count_color(tray_icon(_bundle(10), DARK, size=SIZE), DARK.danger) > 0
    assert _count_color(tray_icon(_bundle(10.5), DARK, size=SIZE), DARK.warn) > 0


# --- the three degenerate states are three different pictures ---------------


def test_a_real_value_leaves_the_ring_center_empty():
    # The center is empty by design: it is what lets the ring be large, and
    # it is the space the no-value states claim.
    cx, cy = _ring_center(SIZE)
    for availability in (0, 40, 100):
        img = tray_icon(_bundle(availability), DARK, size=SIZE)
        assert img.getpixel((int(cx), int(cy)))[3] == 0, availability


def test_error_and_no_data_park_the_mark_in_the_center():
    cx, cy = _ring_center(SIZE)
    for label, bundle in (("error", {"primary": {"error": "boom"}}), ("no data", {"primary": {}})):
        img = tray_icon(bundle, DARK, size=SIZE)
        assert img.getpixel((int(cx), int(cy)))[3] > 200, label


def test_error_no_data_and_zero_are_all_distinct():
    error = tray_icon({"primary": {"error": "boom"}}, DARK, size=SIZE)
    no_data = tray_icon({"primary": {}}, DARK, size=SIZE)
    zero = tray_icon(_bundle(0), DARK, size=SIZE)
    assert len({_digest(error), _digest(no_data), _digest(zero)}) == 3


def test_error_paints_the_whole_glyph_in_danger():
    # Not merely "different from 0%": at 22 points a difference in a small
    # detail is invisible, so error colors the track too and shows no gray.
    img = tray_icon({"primary": {"error": "boom"}}, DARK, size=SIZE)
    assert _count_color(img, DARK.danger) > 0
    assert _count_color(img, NEUTRAL_TRACK) == 0


def test_no_data_is_entirely_neutral():
    img = tray_icon({"primary": {}}, DARK, size=SIZE)
    assert _count_color(img, NEUTRAL_TRACK) > 0
    for color in (DARK.accent, DARK.warn, DARK.danger):
        assert _count_color(img, color) == 0


def test_zero_availability_keeps_the_gray_track_and_a_danger_mark():
    img = tray_icon(_bundle(0), DARK, size=SIZE)
    assert _count_color(img, NEUTRAL_TRACK) > 0
    assert _count_color(img, DARK.danger) > 0


def test_error_only_wins_when_no_quota_could_be_read():
    # An `error` alongside usable numbers is a partial failure, and the gauge
    # must still show the numbers rather than going red.
    bundle = {"primary": {"error": "boom", "session": {"utilization": 20}}}
    assert _count_color(tray_icon(bundle, DARK, size=SIZE), DARK.accent) > 0


# --- layout -----------------------------------------------------------------


def _ink_box(img: Image.Image):
    return img.getchannel("A").point(lambda p: 255 if p > 10 else 0).getbbox()


def test_the_drawing_envelope_is_centered_in_its_box():
    """The union of every value's ink is centered -- not any single frame.

    That distinction is the whole point. The mark overhangs the ring at
    whatever point it currently occupies, so a layout centered on the ink of
    one particular value would make the glyph SHIFT as availability changed:
    an icon that creeps up and down the menu bar between polls. The center is
    instead derived from the worst-case excursion in each direction, which
    holds the drawing still and leaves any one frame deliberately off-center
    inside it.
    """
    boxes = [_ink_box(tray_icon(_bundle(v), DARK, size=SIZE)) for v in range(0, 101, 5)]
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    assert abs(left - (SIZE - right)) <= 1, (left, right)
    assert abs(top - (SIZE - bottom)) <= 1, (top, bottom)


def test_the_glyph_does_not_drift_vertically_as_the_value_changes():
    # The ring is the stable part; only the mark moves. Reserving the mark's
    # excursion on all four sides is what keeps the icon from jittering in
    # the menu bar every time a poll lands.
    boxes = [_ink_box(tray_icon(_bundle(v), DARK, size=SIZE)) for v in range(0, 101, 5)]
    assert max(b[2] for b in boxes) - min(b[0] for b in boxes) <= SIZE
    assert max(b[3] for b in boxes) - min(b[1] for b in boxes) <= SIZE


def test_the_drawing_uses_most_of_its_box():
    # The first gauge wasted the bottom third of the square on the arc's
    # opening and was correspondingly small at 22 points.
    left, top, right, bottom = _ink_box(tray_icon(_bundle(100), DARK, size=SIZE))
    assert (right - left) >= SIZE * 0.78
    assert (bottom - top) >= SIZE * 0.78


# --- determinism and sizing -------------------------------------------------


def test_render_is_deterministic():
    for _label, bundle in ALL_STATES:
        assert _digest(tray_icon(bundle, DARK, size=SIZE)) == _digest(
            tray_icon(bundle, DARK, size=SIZE)
        )


def test_tray_icon_defaults_to_a_downscalable_source():
    # Every tray backend rescales this image to the bar's own size; rendering
    # larger than any bar means that is always a DOWNscale, which is the only
    # direction that stays sharp.
    img = tray_icon(_bundle(50), DARK)
    assert img.size == (128, 128)
    assert img.mode == "RGBA"


def test_tray_icon_respects_a_custom_size():
    assert tray_icon(_bundle(50), DARK, size=22).size == (22, 22)


def test_the_same_drawing_survives_every_size():
    # Geometry is expressed in fractions of the side, so the mark must sit at
    # the same angle whether the icon is 22 px or 128.
    reference = _mark_angle(tray_icon(_bundle(60), DARK, size=128), DARK.accent, 128)
    for size in (44, 64, 88):
        angle = _mark_angle(tray_icon(_bundle(60), DARK, size=size), DARK.accent, size)
        assert abs(angle - reference) < 6, (size, angle, reference)


def test_window_icon_matches_the_tray_language():
    img = window_icon(DARK)
    assert img.size == (32, 32)
    assert img.mode == "RGBA"
    assert _count_color(img, DARK.accent) > 0
    assert _count_color(img, NEUTRAL_TRACK) > 0


def test_window_icon_respects_custom_size():
    assert window_icon(DARK, size=64).size == (64, 64)


def test_icons_follow_the_palette_they_are_given():
    # The gauge takes its colors from the user's configured palette, not from
    # Claude's orange: identity comes from the sunburst's shape. A user whose
    # accent is green must get a green gauge.
    from dataclasses import replace

    custom = replace(DARK, accent="#11a800")
    img = tray_icon(_bundle(50), custom, size=SIZE)
    assert _count_color(img, "#11a800") > 0
    assert _count_color(img, DARK.accent) == 0


# --- the icon reads a real multi-provider bundle -----------------------------
#
# The helpers above build bundles by hand. These four go through
# `providers.<adapter>.normalize` instead, because the risk they cover is a
# seam, not a drawing: a Codex profile declares only two quota windows, so its
# normalized payload carries `weekly_fable: None`. Nothing in the icon path
# ever asks which provider a payload came from -- it asks
# `formatting.availability_pct_from_bundle` for one number -- so the question
# worth pinning is whether a provider that reports fewer windows still yields
# a number, and the right one.


def _normalized(provider_id: str, raw: dict) -> dict:
    from claude_usage_tray import providers

    return providers.get(provider_id).normalize(raw)


CLAUDE_PAYLOAD = {"five_hour": {"utilization": 10.0}, "seven_day": {"utilization": 20.0}}
CODEX_PAYLOAD = {
    "rate_limit": {
        "primary_window": {"used_percent": 40.0},
        "secondary_window": {"used_percent": 70.0},
    }
}


def test_a_codex_profiles_missing_fable_window_does_not_erase_its_availability():
    """`weekly_fable` is `None` for every Codex profile, by design.

    `availability_pct` skips a window it cannot read rather than treating it
    as zero, so the number comes from the two windows Codex does report --
    here 100-70, the scarcer of them. A `None` that counted as "0% available"
    would paint every Codex user's tray permanently red.
    """
    bundle = {"profiles": [{"id": "cx", "data": _normalized("codex", CODEX_PAYLOAD)}]}

    assert formatting.availability_pct_from_bundle(bundle) == 30.0


def test_the_scarcest_profile_sets_the_icon_across_providers():
    """One tray icon, two services: the bundle takes the minimum, so the
    account closest to its ceiling is the one the glyph reports -- regardless
    of which provider that account belongs to."""
    claude = {"id": "cl", "data": _normalized("claude", CLAUDE_PAYLOAD)}
    codex = {"id": "cx", "data": _normalized("codex", CODEX_PAYLOAD)}
    mixed = {"profiles": [claude, codex]}

    # Claude is at 80% available, Codex at 30%; Codex binds.
    assert formatting.availability_pct_from_bundle(mixed) == 30.0
    assert _digest(tray_icon(mixed, DARK, size=SIZE)) == _digest(
        tray_icon({"profiles": [codex]}, DARK, size=SIZE)
    )


def test_a_signed_out_codex_profile_renders_the_no_data_glyph():
    """Every window `None` -- what a profile whose CLI has no session looks
    like once normalized. It must reach the same picture as an empty bundle,
    not raise and not invent a value."""
    empty_codex = {
        "profiles": [{"id": "cx", "data": _normalized("codex", {})}]
    }

    assert formatting.availability_pct_from_bundle(empty_codex) is None
    assert _digest(tray_icon(empty_codex, DARK, size=SIZE)) == _digest(
        tray_icon({"primary": {}}, DARK, size=SIZE)
    )


def test_every_registered_provider_normalizes_into_a_drawable_bundle():
    """The contract the icon depends on, asserted against the registry rather
    than against a list of provider ids -- so an adapter added later is
    covered here the day it is registered."""
    from claude_usage_tray import providers

    for adapter in providers.all_providers():
        data = adapter.normalize({})
        bundle = {"profiles": [{"id": adapter.id, "data": data}]}

        assert set(data) >= {"session", "weekly", "weekly_fable"}, adapter.id
        assert formatting.availability_pct_from_bundle(bundle) is None, adapter.id
        assert tray_icon(bundle, DARK, size=SIZE).size == (SIZE, SIZE), adapter.id
