"""claude_usage_tray.ui.scroll -- viewport geometry, as pure logic.

The settings window grew to four sections plus nine colour rows, and the
alerts section grows one row per profile. Measured on a 2560x1050 work area:
font 9 wants 801px and fits, font 12 wants 1038px, font 16 wants 1061px --
and font 16 is a size `specs/user-settings/spec.md` explicitly grants ("Entre
7 y 16"). Past the work area the window's own "Cerrar" and "Aplicar umbrales"
sit below the desktop, so the user can neither save nor dismiss. Profile
count is the same bug by another road: twenty profiles overflow at *any* font
size.

So the window height is clamped to the desktop and the content scrolls
inside it. This module is the arithmetic half of that -- no tkinter, ever,
the same split `dismiss.py` and `hover.py` keep and for the same reason:
tkinter is not installed in this dev/CI environment, so anything that must be
*decided* correctly has to be decidable without a display. `widgets.py`'s
`ScrollableView` is the widget half, and holds no arithmetic of its own.

Every function here is total: no exception, and a defined answer for the
degenerate inputs a real window hands you (a zero-height track before the
first `<Configure>`, a `WorkArea` of `None` on a platform that cannot know
one, a `??` where tkinter leaves an event field unset).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..platform import WorkArea


# The window chrome -- title bar plus resize border -- that Tk's `geometry()`
# height excludes but the desktop still has to find room for. It cannot be
# measured before the window is mapped, and by then the size is already
# chosen, so it is a constant rather than a reading: ~31px of Windows title
# bar at 100% scaling, with room left for the larger bar a scaled desktop
# draws.
#
# Deliberately generous. Overshooting costs a scrollbar on a window that
# would have fitted without one; undershooting costs the user the "Cerrar"
# button. Those are not comparable prices, so this errs at the cheap end.
WINDOW_CHROME_HEIGHT = 48

# Breathing room kept at the top and bottom edges of the work area.
WORK_AREA_MARGIN = 12

# The scrollbar is drawn, not instantiated -- see `widgets.ScrollableView`.
SCROLLBAR_WIDTH = 10
SCROLLBAR_GAP = 6

# What a visible scrollbar adds to a window's width. The callers size their
# window from the content's own request, so the bar has to be paid for
# explicitly or it would eat the rightmost column of every row.
SCROLLBAR_SPAN = SCROLLBAR_WIDTH + SCROLLBAR_GAP

# A thumb proportional to a very long list shrinks to a few unusable pixels.
MIN_THUMB_LENGTH = 24

# Windows reports `<MouseWheel>` deltas in multiples of this; macOS reports
# small counts directly; X11 reports no delta at all and sends button 4/5.
_WHEEL_NOTCH = 120
_WHEEL_UP_BUTTON = 4
_WHEEL_DOWN_BUTTON = 5


@dataclass(frozen=True)
class Fit:
    """A window height, and whether that height had to cut the content off.

    `scrolls` is not a preference -- it is the fact that `height` is less
    than what was asked for, and therefore that something has to carry the
    remainder.
    """

    height: int
    scrolls: bool


def available_height(work_area: "WorkArea | None", screen_height: int) -> int:
    """The tallest client area a window may claim and still fit the desktop.

    `work_area` is the platform seam's answer and may be `None` -- an honest
    "cannot know" on this platform, not an error. The screen height is the
    fallback, which is wrong by exactly the height of whatever panel the
    desktop reserves; that is still far better than not clamping at all, and
    the only platform that answers `None` here is also the one this app does
    not ship a tray for.
    """
    if work_area is None:
        top, bottom = 0, screen_height
    else:
        top, bottom = work_area.top, work_area.bottom
    return bottom - top - WINDOW_CHROME_HEIGHT - 2 * WORK_AREA_MARGIN


def fit_height(natural: int, available: int) -> Fit:
    """Clamp a content height to `available`, reporting whether it was cut.

    A non-positive `available` means the arithmetic above had nothing usable
    to work from (a screen height of 0, which Tk reports for an unmapped
    window). Clamping to it would produce a window of negative height, so
    this yields the natural size unclamped: the pre-existing behaviour, which
    is at worst the bug we already had, rather than a new and stranger one.
    """
    if available <= 0 or natural <= available:
        return Fit(height=natural, scrolls=False)
    return Fit(height=available, scrolls=True)


def top_placement(work_area: "WorkArea | None", screen_height: int, height: int) -> int:
    """The `y` for a window of `height`, in the work area's upper third.

    Keeps the old `(screen_height - height) // 3` feel, but anchored to the
    work area's top rather than the screen's, and never letting the window's
    bottom leave the work area -- the two ways the old expression put a
    clamped window back off the desktop it was just clamped to fit.
    """
    if work_area is None:
        top, bottom = 0, screen_height
    else:
        top, bottom = work_area.top, work_area.bottom
    slack = bottom - top - height - WINDOW_CHROME_HEIGHT
    return top + max(min(slack // 3, slack - WORK_AREA_MARGIN), WORK_AREA_MARGIN)


def wheel_units(delta: Any, num: Any = 0) -> int:
    """Scroll units for a wheel event, positive meaning "toward the end".

    Three wheel dialects reach one handler. Windows and macOS send
    `<MouseWheel>` with a `delta` -- in multiples of 120 on Windows, in small
    counts on macOS -- and X11 sends `<Button-4>`/`<Button-5>` with no delta
    at all. Tk's sign convention is the inverse of Tk's own `yview_scroll`
    (wheel away from the user is a positive delta and a *negative* scroll),
    which is exactly the sort of thing worth deciding once, here, where it
    can be tested.

    `delta` is typed `Any` and checked rather than trusted: tkinter fills
    unset event fields with the string `"??"`, so a `<Button-4>` handler that
    assumed an int would raise inside a callback -- where Tk prints the
    traceback and carries on, leaving a window that silently will not scroll.
    """
    if num == _WHEEL_UP_BUTTON:
        return -1
    if num == _WHEEL_DOWN_BUTTON:
        return 1
    if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
        return 0
    direction = -1 if delta > 0 else 1
    return direction * (abs(delta) // _WHEEL_NOTCH or 1)


def thumb_span(
    first: float,
    last: float,
    track_length: int,
    *,
    min_length: int = MIN_THUMB_LENGTH,
) -> tuple[int, int]:
    """Pixel `(top, bottom)` of the thumb for a `yview()` fraction pair.

    `first`/`last` come straight off Tk's `yscrollcommand`, which is free to
    hand back fractions slightly outside `0..1`; they are clamped rather than
    trusted. A thumb shorter than `min_length` is grown to it and pinned
    inside the track, so the bottom of the content stays reachable by drag
    however long the content gets.
    """
    if track_length <= 0:
        return (0, 0)
    first = min(max(first, 0.0), 1.0)
    last = min(max(last, first), 1.0)
    length = max(round((last - first) * track_length), min(min_length, track_length))
    top = min(round(first * track_length), track_length - length)
    top = max(top, 0)
    return (top, top + length)


def drag_fraction(pointer_y: int, grab_offset: int, track_length: int, visible: float) -> float:
    """The `yview_moveto` fraction for a thumb dragged to `pointer_y`.

    `grab_offset` is where inside the thumb the drag started, so the thumb
    does not jump to centre itself under the cursor on the first pixel of
    movement. The result is clamped to `0 .. 1 - visible`: past that the
    content would scroll off its own end and Tk would snap it back, which
    reads as a stuck scrollbar.
    """
    if track_length <= 0:
        return 0.0
    first = (pointer_y - grab_offset) / track_length
    return min(max(first, 0.0), max(0.0, 1.0 - visible))
