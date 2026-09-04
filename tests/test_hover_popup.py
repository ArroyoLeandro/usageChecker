"""Unit tests for the hover popup's placement arithmetic.

`_place` is the one part of `hover_popup.py` that can be wrong in a way tests
can catch: everything else it does is build widgets, and a widget tree either
renders or it does not. Placement fails quietly -- the window opens, on the
right screen, with the right content, in the wrong place -- which is exactly
the failure a human reviewing a diff will not see.

No Tk root is created here. `_place` only talks to its argument through a
handful of `winfo_*` accessors and `geometry`, so a recording stand-in
exercises the arithmetic with no display, the same headless bargain
`hover.py`'s tests strike. The module-level `import tkinter` is skipped past
where the interpreter has no tkinter at all (CI on a bare box), since
importing the module is unavoidable to reach the function.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tkinter")

from claude_usage_tray.platform import Rect, WorkArea  # noqa: E402
from claude_usage_tray.ui.hover_popup import _place  # noqa: E402

# A menu-bar item: the full height of the macOS menu bar, at the top of the
# screen. The numbers are the ones measured against a real `NSStatusItem`.
MENU_BAR_ICON = Rect(left=1253, top=0, right=1291, bottom=33)

# A Windows notify icon: bottom right, above the taskbar.
TRAY_ICON = Rect(left=1850, top=1040, right=1874, bottom=1064)

MACOS_WORK_AREA = WorkArea(left=0, top=37, right=1728, bottom=1117)
WINDOWS_WORK_AREA = WorkArea(left=0, top=0, right=1920, bottom=1032)


class _FakePopup:
    """A stand-in that answers geometry questions and records the result.

    `winfo_width`/`winfo_height` return 1 on purpose. They are the accessors
    `_place` must not use, and answering something obviously wrong is how
    this catches a regression that starts using them: an unmapped window
    reports 1 under Aqua Tk, and a mapped one reports the size it has from
    the *previous* show, which is just as wrong for placing this one.
    """

    def __init__(self, *, req_width: int, req_height: int, screen=(1728, 1117)) -> None:
        self._req = (req_width, req_height)
        self._screen = screen
        self.geometry_calls: list[str] = []

    def update_idletasks(self) -> None:
        pass

    def winfo_width(self) -> int:
        return 1

    def winfo_height(self) -> int:
        return 1

    def winfo_reqwidth(self) -> int:
        return self._req[0]

    def winfo_reqheight(self) -> int:
        return self._req[1]

    def winfo_screenwidth(self) -> int:
        return self._screen[0]

    def winfo_screenheight(self) -> int:
        return self._screen[1]

    def geometry(self, spec: str) -> None:
        self.geometry_calls.append(spec)

    @property
    def placed(self) -> tuple[int, int, int, int]:
        """The last geometry as `(width, height, x, y)`."""
        size, x, y = self.geometry_calls[-1].split("+")
        width, height = size.split("x")
        return int(width), int(height), int(x), int(y)


def test_the_popup_is_sized_from_its_requested_geometry():
    """The bug this guards: the window's *current* size is never the answer.

    `_place` runs before the popup is made visible, with the content for this
    show already built but not yet sized into the window. Under Aqua Tk an
    unmapped window answers 1 to both `winfo_width` and `winfo_height`, and
    sizing from those pinned the popup to a 1x1 window: on screen, correctly
    positioned, and completely invisible. Measured on macOS 26. The window is
    kept mapped now, so those accessors answer the *previous* show's size
    instead -- a different wrong number for the same reason. The requested
    size is what the geometry managers computed for the content that is
    actually there.
    """
    popup = _FakePopup(req_width=240, req_height=173)

    _place(popup, MENU_BAR_ICON, MACOS_WORK_AREA)

    width, height, _, _ = popup.placed
    assert (width, height) == (240, 173)


def test_a_menu_bar_icon_puts_the_popup_below_the_bar():
    # There is never room above an icon whose rect starts at y=0, so the
    # popup has to fall through to the other side. This is the macOS case,
    # and getting it wrong is what pins the popup to the top edge.
    popup = _FakePopup(req_width=240, req_height=173)

    _place(popup, MENU_BAR_ICON, MACOS_WORK_AREA)

    _, _, _, y = popup.placed
    assert y == MENU_BAR_ICON.bottom + 8
    assert y >= MACOS_WORK_AREA.top


def test_a_taskbar_icon_puts_the_popup_above_it():
    # The Windows case, unchanged: there is room above a tray icon sitting on
    # the taskbar, so the popup goes there. The icon's own rect is *below*
    # the work area -- that is what a taskbar is -- so the popup also gets
    # pulled back inside it, and both halves matter.
    popup = _FakePopup(req_width=240, req_height=173, screen=(1920, 1080))

    _place(popup, TRAY_ICON, WINDOWS_WORK_AREA)

    _, height, _, y = popup.placed
    assert y + height <= TRAY_ICON.top
    assert y + height <= WINDOWS_WORK_AREA.bottom


def test_the_popup_is_centred_on_the_icon():
    popup = _FakePopup(req_width=240, req_height=173)

    _place(popup, MENU_BAR_ICON, MACOS_WORK_AREA)

    width, _, x, _ = popup.placed
    assert x + width // 2 == MENU_BAR_ICON.left + MENU_BAR_ICON.width // 2


def test_an_icon_near_the_edge_does_not_push_the_popup_off_screen():
    # A menu-bar item lives at the right-hand end of the bar, so a popup
    # centred on it would hang off the screen more often than not.
    edge_icon = Rect(left=1700, top=0, right=1728, bottom=33)
    popup = _FakePopup(req_width=240, req_height=173)

    _place(popup, edge_icon, MACOS_WORK_AREA)

    width, _, x, _ = popup.placed
    assert x + width <= MACOS_WORK_AREA.right
    assert x >= MACOS_WORK_AREA.left


def test_an_unknown_work_area_falls_back_to_the_whole_screen():
    # `work_area_bounds()` is allowed to say "I don't know"; the popup still
    # has to land somewhere sane.
    popup = _FakePopup(req_width=240, req_height=173)

    _place(popup, MENU_BAR_ICON, None)

    width, height, x, y = popup.placed
    assert 0 <= x and x + width <= 1728
    assert 0 <= y and y + height <= 1117


# --- displays that are not the primary one -------------------------------
#
# Tk's coordinate space has its origin at the top left of the *primary*
# display, so a monitor placed above it has negative y throughout -- and the
# menu-bar icon on that monitor comes back with a negative `top`. Measured on
# a real three-display arrangement: the primary at 1728x1117, a 2560x1080
# above it, and a 1920x1080 above and to the left, which reported the icon at
# y=-1080. Every clamp below has to keep working in that space, and a `min`
# or `max` written for non-negative screens is exactly what silently drags
# the popup back to the primary display.

ABOVE_PRIMARY = WorkArea(left=0, top=-1080, right=2560, bottom=0)
ABOVE_AND_LEFT = WorkArea(left=-1920, top=-1080, right=0, bottom=0)


def test_a_menu_bar_icon_on_a_display_above_the_primary_keeps_the_popup_there():
    icon = Rect(left=2052, top=-1080, right=2090, bottom=-1050)
    popup = _FakePopup(req_width=113, req_height=155)

    _place(popup, icon, ABOVE_PRIMARY)

    width, height, x, y = popup.placed
    assert ABOVE_PRIMARY.top <= y and y + height <= ABOVE_PRIMARY.bottom
    assert ABOVE_PRIMARY.left <= x and x + width <= ABOVE_PRIMARY.right
    # Below the icon: there is no room above a rect flush with the top of its
    # own display, negative coordinates or not.
    assert y == icon.bottom + 8


def test_a_display_left_of_the_primary_keeps_the_popup_in_negative_x():
    icon = Rect(left=-1728, top=-1080, right=-1690, bottom=-1050)
    popup = _FakePopup(req_width=113, req_height=155)

    _place(popup, icon, ABOVE_AND_LEFT)

    width, _, x, y = popup.placed
    assert x < 0, "a popup clamped into positive x has been dragged to the primary display"
    assert ABOVE_AND_LEFT.left <= x and x + width <= ABOVE_AND_LEFT.right
    assert y == icon.bottom + 8


def test_the_geometry_string_survives_a_negative_offset():
    """Tk reads a bare `-1042` in a geometry string as an offset from the
    *right/bottom* edge, so a negative position has to arrive as `+-1042`.
    That is what `+{x}+{y}` produces, and this pins it: the same string was
    read back unchanged from a real Toplevel, whose NSWindow landed on the
    display above the primary.
    """
    icon = Rect(left=2052, top=-1080, right=2090, bottom=-1050)
    popup = _FakePopup(req_width=113, req_height=155)

    _place(popup, icon, ABOVE_PRIMARY)

    assert popup.geometry_calls[-1] == "113x155+2014+-1042"
