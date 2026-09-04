"""claude_usage_tray.icons -- PIL rendering of the tray and window icons.

Both icons draw the object `assets/claude_usage_icon.png` draws: an open
gauge arc with the Claude sunburst riding it as the value indicator. The
asset puts the sunburst at the arc's end, not in the middle, and that is the
whole trick -- it leaves the center empty, so the ring can be large and
thick, and it gives the sunburst room of its own instead of making it
compete with the arc for the same pixels.

Four constraints shape every number below.

* **No background plate.** macOS menu-bar icons are drawn straight onto the
  bar; an opaque rounded rectangle behind the glyph reads as a sticker
  someone pasted on. The canvas stays fully transparent on every platform --
  Windows and Linux trays composite over their own backgrounds just as
  happily.
* **The track is a fixed neutral gray, not a palette color.** The menu bar is
  light under the Light appearance and dark under Dark, and this process is
  never told which. A palette-derived track would vanish against one of them.
  A mid gray is the one value with usable contrast against both -- the same
  answer the bundle asset already reached for its worn-arc color. Note that
  no color here is ever *derived* from a palette field by lightening or
  darkening: a user whose `panel` is pure black would get no delta from such
  a derivation, so every color is either taken whole or fixed.
* **The sunburst is drawn as a silhouette, not as a notched disc.** The asset
  builds its sunburst the way a 256 px asset can afford to -- a filled disc
  with white rays cut out of it. Reproducing that construction at menu-bar
  size fails, and not subtly: the notches eat so much of the disc that the
  mark loses density and washes out to a pale smudge (measured, at the ~9 px
  the mark actually occupies inside a 22-point icon). Drawing the same
  *silhouette* as a solid core plus tapered spokes keeps the ink and keeps
  the spiky outline, which is what identifies the mark. Same shape, opposite
  construction.
* **Identity comes from the shape, not from Claude's orange.** The gauge
  follows the user's configured `Palette` (accent -> warn -> danger), because
  a user who set a green accent has said what they want their app to look
  like. What makes the icon Claude's is the sunburst.

These functions take an explicit `palette: Palette` argument -- this is the
one module besides `ui/` allowed to import `theme.py` (see design.md's
layering table: `icons.py | PIL, theme, formatting, paths`).
`formatting.bar_color` reaches the same result from the other side: it stays
L1 and stdlib-only, so it duck-types the palette its caller passes in rather
than importing one (a `formatting` -> `theme` edge would point sideways, and
`tests/test_layering.py` forbids it). The value color therefore comes from
`bar_color` rather than from thresholds retyped here: one definition of
"green / amber / red", used by the popup's bars and by the tray alike.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw

from . import formatting
from .theme import Palette

#: The gauge's unfilled track, and the color of the whole glyph when there is
#: no data to show. Deliberately NOT a palette field: see the module
#: docstring -- the menu bar's own background is unknown at render time, so
#: this has to be the one gray that survives both. Matches macOS's
#: `systemGray`, which is the same neutral the bundle asset uses for the worn
#: part of its arc.
NEUTRAL_TRACK = "#8E8E93"

#: Supersampling factor. PIL's `arc`/`pieslice`/`ellipse`/`polygon` are not
#: antialiased, so every shape is drawn this many times larger and reduced
#: with LANCZOS. Without it the gauge is visibly staircased at 22 points,
#: which is the entire defect this icon exists to fix. 4 is where the
#: improvement stops being visible; 8 only costs time.
_SUPERSAMPLE = 4

# --- Gauge geometry, as fractions of the icon's side ------------------------
#
# Everything is a fraction so 22, 44, 66 and 88 px all render the same
# drawing rather than four hand-tuned ones.
#
# PIL's angles: 0 degrees is 3 o'clock and they increase CLOCKWISE, because
# y grows downward. A 270-degree sweep open at the bottom runs 135 -> 405: it
# starts at the lower left, passes 180 (9 o'clock), 270 (12 o'clock) and 360
# (3 o'clock), and stops at 405 = 45 degrees, the lower right. The remaining
# 90-degree gap sits centered on 6 o'clock.
_ARC_START_DEG = 135.0
_ARC_SWEEP_DEG = 270.0

#: Radius of the arc's centerline.
_RADIUS_F = 0.30
#: Arc stroke thickness. The center is empty in this layout, so the ring can
#: afford to be heavy -- and a heavy ring is what stays visible at 22 points.
_STROKE_F = 0.16
#: Outer radius of the sunburst indicator, centered ON the arc's centerline.
#:
#: Two numbers fight here and this is where they settled. The mark wants
#: roughly 9 px of apparent width to read as a sunburst rather than as a dot
#: (measured), which argues for a large value. But the mark overhangs the
#: ring at whatever single point it currently occupies, and the icon is
#: square and must be sized for that worst case -- so every unit of sunburst
#: is taken directly out of the RING, which is the glyph's dominant mass. At
#: 0.185 the mark is ~8 px wide at 22 points and the ring still spans 76% of
#: the box.
#:
#: The mark rides the CENTERLINE rather than the arc's inner edge, and that
#: is not cosmetic. Riding the inner edge buys a visibly larger ring (the
#: overhang shrinks), but the mark is then inside the ring's silhouette --
#: and since the mark and the filled span are always the same color, at 100%
#: availability the sunburst is swallowed by the arc it sits on and simply
#: disappears. Straddling the centerline keeps its spokes protruding past
#: the outer edge, so the mark reads at every value including the most
#: common one.
_SUNBURST_F = 0.185

#: `_RADIUS_F + _SUNBURST_F` is the glyph's half-width: how much of the
#: square the drawing actually uses.
_HALF_EXTENT_F = _RADIUS_F + _SUNBURST_F

#: The drawing is not vertically symmetric: it reaches `radius + mark` above
#: the center but only `radius * sin(45 deg) + mark` below it, because the
#: arc stops at the 45-degree diagonals. Centering the INK rather than the
#: geometric center is what keeps the glyph from floating high in the menu
#: bar. Derived, not guessed: equate the top and bottom margins and solve.
_CENTER_Y_F = 0.5 + _RADIUS_F * (1.0 - math.sin(math.radians(45.0))) / 2.0

#: Number of spokes on the sunburst. Chosen by measurement, not taste: at the
#: mark's real size inside a 22-point icon, 6 and 8 spokes stay distinct
#: while 10 and 12 round off into a blob. 8 is the denser of the two that
#: survive, and the closer to the asset's twelve.
_SUNBURST_RAYS = 8
#: Radius of the sunburst's solid core, as a fraction of `_SUNBURST_F`. The
#: core is what stops the mark from disappearing when the spokes get thin.
_SUNBURST_CORE_F = 0.42
#: Half-width of each spoke at its base, as a fraction of the angular pitch
#: between spokes. Above ~0.35 the spokes merge into a disc; below ~0.2 they
#: thin out and the mark loses density.
_SUNBURST_TIP_F = 0.30


def _polar(center: tuple[float, float], radius: float, degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return (center[0] + radius * math.cos(rad), center[1] + radius * math.sin(rad))


def _draw_arc_span(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    radius: float,
    stroke: float,
    start_deg: float,
    end_deg: float,
    color: str,
) -> None:
    """Stroke `start_deg` -> `end_deg` of the circle, with round caps.

    `ImageDraw.arc` has no line-cap concept: it always ends square. The round
    caps are therefore drawn as two discs of the stroke's own diameter,
    centered on the arc's centerline at each end.
    """
    cx, cy = center
    half = stroke / 2.0
    outer = radius + half
    if end_deg > start_deg:
        draw.arc(
            (cx - outer, cy - outer, cx + outer, cy + outer),
            start_deg,
            end_deg,
            fill=color,
            width=int(round(stroke)),
        )
    for angle in (start_deg, end_deg):
        px, py = _polar(center, radius, angle)
        draw.ellipse((px - half, py - half, px + half, py + half), fill=color)


def _draw_sunburst(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    radius: float,
    color: str,
) -> None:
    """The Claude sunburst: a solid core with `_SUNBURST_RAYS` tapered spokes.

    Built as a silhouette rather than as the asset's disc-with-notches, for
    the reason given in the module docstring: notches remove ink, and at
    menu-bar size there is none to spare. See `_SUNBURST_RAYS` for how the
    spoke count was picked.
    """
    cx, cy = center
    core = radius * _SUNBURST_CORE_F
    draw.ellipse((cx - core, cy - core, cx + core, cy + core), fill=color)

    pitch = 360.0 / _SUNBURST_RAYS
    half_w = pitch * _SUNBURST_TIP_F
    for index in range(_SUNBURST_RAYS):
        mid = index * pitch
        draw.polygon(
            [
                _polar(center, core, mid - half_w),
                _polar(center, radius, mid),
                _polar(center, core, mid + half_w),
            ],
            fill=color,
        )


def _render_gauge(
    size: int,
    *,
    track_color: str,
    mark_color: str,
    value: float | None = None,
) -> Image.Image:
    """Draw the gauge at `size` x `size` on a fully transparent canvas.

    `value` is 0.0-1.0 and does two jobs at once: it is how much of the arc
    is filled, and it is where the sunburst sits along that arc. `None` means
    there is no value to show -- no filled span, and the sunburst is parked
    in the ring's empty center, a silhouette nothing else in this icon's
    vocabulary produces.
    """
    scale = _SUPERSAMPLE
    ss = size * scale
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    center = (ss / 2.0, ss * _CENTER_Y_F)
    radius = ss * _RADIUS_F
    stroke = ss * _STROKE_F
    mark_radius = ss * _SUNBURST_F

    end = _ARC_START_DEG + _ARC_SWEEP_DEG
    _draw_arc_span(draw, center, radius, stroke, _ARC_START_DEG, end, track_color)

    if value is None:
        mark_center = center
    else:
        clamped = max(0.0, min(1.0, value))
        mark_angle = _ARC_START_DEG + _ARC_SWEEP_DEG * clamped
        _draw_arc_span(draw, center, radius, stroke, _ARC_START_DEG, mark_angle, mark_color)
        mark_center = _polar(center, radius, mark_angle)

    # No separating moat around the mark. An earlier version cut a
    # transparent ring so the sunburst would read as a distinct object, but
    # the moat has to be wider than the mark to show, which means wider than
    # the arc's stroke -- so it bit a conspicuous notch out of the ring just
    # ahead of the indicator. The asset does not separate them either: its
    # needle, its sunburst and its live arc are one continuous shape.
    _draw_sunburst(draw, mark_center, mark_radius, mark_color)

    return img.resize((size, size), Image.LANCZOS)


def tray_icon(bundle: dict[str, Any], palette: Palette, size: int = 128) -> Image.Image:
    """The menu-bar / system-tray icon for `bundle`.

    Rendered at 128 px rather than at the tray's own size on purpose: every
    tray backend rescales this image to whatever the bar is (22 points on
    macOS, times the display's backing scale factor -- so 44 or 66 real
    pixels), and downscaling from a comfortable source is the only one of
    those directions that stays sharp. Note that no drawing decision keys off
    this number: the sunburst is sized and shaped for how large the icon is
    *displayed*, which is ~22 points no matter how many pixels it is
    rasterised into.

    Three states, separated by silhouette AND by color so that neither has to
    carry the distinction alone at 22 points:

    * **error** -- the whole glyph in `palette.danger`, with the sunburst
      parked in the ring's empty center and no filled span.
    * **no data** -- the same centered-sunburst silhouette, entirely in the
      neutral gray: nothing known, as opposed to something bad.
    * **a real value** -- the gray track, the filled span, and the sunburst
      riding the arc at the value's own position.

    The moving sunburst is what makes low values legible. When the indicator
    was a fixed center hub, 5% availability rendered as two pixels of color
    at the lower left and was indistinguishable from an empty gauge -- which
    is precisely the reading a user must not miss. Now 5% puts a full-size
    danger-colored mark near the bottom left of the ring, visible whether or
    not the filled arc behind it is.
    """
    data = bundle.get("primary", bundle)
    session = formatting.pct(data.get("session"))
    weekly = formatting.pct(data.get("weekly"))

    if data.get("error") and session is None and weekly is None:
        return _render_gauge(size, track_color=palette.danger, mark_color=palette.danger)

    availability = formatting.availability_pct_from_bundle(bundle)
    if availability is None:
        return _render_gauge(size, track_color=NEUTRAL_TRACK, mark_color=NEUTRAL_TRACK)

    # `bar_color` speaks in percent *used* (>=90 danger, >=70 warn), while
    # `availability_pct_from_bundle` reports percent *available*. Converted
    # here, explicitly, so the tray and the popup's bars can never disagree
    # about where amber starts.
    color = formatting.bar_color(100.0 - availability, palette)
    return _render_gauge(
        size, track_color=NEUTRAL_TRACK, mark_color=color, value=availability / 100.0
    )


def window_icon(palette: Palette, size: int = 32) -> Image.Image:
    """The window/taskbar icon: the same gauge, as a static brand mark.

    There is no bundle to read here -- this icon identifies the app, it does
    not report a quota -- so the arc is held at the three-quarter position
    the bundle asset draws, in `palette.accent`. Transparent background for
    the same reason the tray icon has one: the window manager composites it
    over a surface this process does not choose.
    """
    return _render_gauge(
        size, track_color=NEUTRAL_TRACK, mark_color=palette.accent, value=0.75
    )
