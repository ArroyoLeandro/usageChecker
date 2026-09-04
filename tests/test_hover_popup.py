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

    `winfo_width`/`winfo_height` return 1 on purpose: that is what Aqua Tk
    reports for a window that is still withdrawn, which the popup always is
    when `_place` runs.
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
    """The bug this guards: an unmapped window has no size yet.

    `_place` runs while the popup is still withdrawn -- deliberately, so it
    cannot flash into view before it is positioned -- and under Aqua Tk both
    `winfo_width` and `winfo_height` answer 1 until the window is mapped.
    Sizing from those pinned the popup to a 1x1 window: on screen, correctly
    positioned, and completely invisible. Measured on macOS 26.
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
