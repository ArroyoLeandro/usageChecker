"""Tests for claude_usage_tray.ui.scroll -- the viewport arithmetic.

No tkinter import anywhere here, and that is the point rather than a
concession: this module is the half of the scrolling fix that has to be
*right*, and tkinter is not installed in this dev/CI environment, so the only
way any of it is checkable at all is for the decisions to live somewhere a
headless box can reach. `widgets.ScrollableView` is the other half and is not
covered here -- it builds real widgets, and nothing on this box can measure
one. See this change's apply-progress for what that leaves unverified.

The heights below are real measurements taken on Windows against a real
`WorkArea(left=0, top=0, right=2560, bottom=1050)`, not invented numbers:
font 9 wanted 801px, font 12 wanted 1038px, font 16 wanted 1061px.
"""

from __future__ import annotations

import pytest

from claude_usage_tray.platform import WorkArea
from claude_usage_tray.ui.scroll import (
    MIN_THUMB_LENGTH,
    SCROLLBAR_SPAN,
    SCROLLBAR_WIDTH,
    WINDOW_CHROME_HEIGHT,
    WORK_AREA_MARGIN,
    available_height,
    drag_fraction,
    fit_height,
    thumb_span,
    top_placement,
    wheel_units,
)

# The desktop the overflow was measured against.
MEASURED_WORK_AREA = WorkArea(left=0, top=0, right=2560, bottom=1050)

# The settings window's measured natural height at each font size, from the
# bug report. Fonts 9/12/16 are measured; the spec allows 7..16.
MEASURED_HEIGHTS = {9: 801, 12: 1038, 16: 1061}


# ---------------------------------------------------------------------------
# available_height
# ---------------------------------------------------------------------------


def test_available_height_reserves_chrome_and_both_margins():
    assert available_height(MEASURED_WORK_AREA, 1050) == 1050 - WINDOW_CHROME_HEIGHT - 2 * WORK_AREA_MARGIN


def test_available_height_measures_the_work_area_not_the_screen():
    """A taskbar-reserved strip is not usable desktop, however tall the screen."""
    with_taskbar = WorkArea(left=0, top=0, right=2560, bottom=1050)
    full_screen_height = 1440
    assert available_height(with_taskbar, full_screen_height) < full_screen_height


def test_available_height_honours_a_work_area_that_does_not_start_at_zero():
    """A taskbar docked at the top pushes `top` down; only the span matters."""
    top_docked = WorkArea(left=0, top=48, right=2560, bottom=1098)
    assert available_height(top_docked, 1098) == available_height(MEASURED_WORK_AREA, 1050)


def test_available_height_falls_back_to_the_screen_when_work_area_is_unknown():
    assert available_height(None, 1050) == 1050 - WINDOW_CHROME_HEIGHT - 2 * WORK_AREA_MARGIN


# ---------------------------------------------------------------------------
# fit_height -- the bug itself
# ---------------------------------------------------------------------------


def test_font_9_still_fits_without_a_scrollbar():
    """The size that was never broken must not grow a vestigial scrollbar."""
    fit = fit_height(MEASURED_HEIGHTS[9], available_height(MEASURED_WORK_AREA, 1050))
    assert fit.scrolls is False
    assert fit.height == MEASURED_HEIGHTS[9]


@pytest.mark.parametrize("font_size", [12, 16])
def test_overflowing_font_sizes_are_clamped_and_scroll(font_size):
    """font 16 overflowed by 11px outright; font 12 overflowed once the title
    bar it never counted is counted. Both must now scroll rather than put
    "Cerrar" below the desktop."""
    available = available_height(MEASURED_WORK_AREA, 1050)
    fit = fit_height(MEASURED_HEIGHTS[font_size], available)
    assert fit.scrolls is True
    assert fit.height == available


@pytest.mark.parametrize("natural", [1, 801, 1038, 1061, 5000, 100_000])
def test_window_never_exceeds_the_work_area_whatever_the_content_wants(natural):
    """The invariant the whole fix exists for, including the twenty-profile
    case: a clamped window plus its chrome always fits the work area."""
    work_area_span = MEASURED_WORK_AREA.bottom - MEASURED_WORK_AREA.top
    fit = fit_height(natural, available_height(MEASURED_WORK_AREA, 1050))
    assert fit.height + WINDOW_CHROME_HEIGHT <= work_area_span


def test_content_that_exactly_fills_the_available_height_does_not_scroll():
    available = available_height(MEASURED_WORK_AREA, 1050)
    fit = fit_height(available, available)
    assert fit.scrolls is False
    assert fit.height == available


def test_one_pixel_over_scrolls():
    available = available_height(MEASURED_WORK_AREA, 1050)
    assert fit_height(available + 1, available).scrolls is True


def test_unusable_available_height_leaves_the_natural_size_alone():
    """Tk reports a screen height of 0 for an unmapped window; clamping to the
    negative number that falls out of that would be a worse bug than the one
    being fixed."""
    fit = fit_height(801, 0)
    assert fit == fit_height(801, -500)
    assert fit.height == 801
    assert fit.scrolls is False


# ---------------------------------------------------------------------------
# top_placement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("natural", [200, 801, 1038, 1061, 5000])
def test_placed_window_always_lands_inside_the_work_area(natural):
    available = available_height(MEASURED_WORK_AREA, 1050)
    fit = fit_height(natural, available)
    y = top_placement(MEASURED_WORK_AREA, 1050, fit.height)
    assert y >= MEASURED_WORK_AREA.top
    assert y + fit.height + WINDOW_CHROME_HEIGHT <= MEASURED_WORK_AREA.bottom


def test_placement_is_anchored_to_the_work_area_not_the_screen():
    """A top-docked taskbar means y=12 is *behind the taskbar*, not below it."""
    top_docked = WorkArea(left=0, top=48, right=2560, bottom=1098)
    y = top_placement(top_docked, 1098, 400)
    assert y >= top_docked.top


def test_a_clamped_window_sits_at_the_top_margin():
    available = available_height(MEASURED_WORK_AREA, 1050)
    y = top_placement(MEASURED_WORK_AREA, 1050, available)
    assert y == MEASURED_WORK_AREA.top + WORK_AREA_MARGIN


def test_a_short_window_sits_in_the_upper_third():
    y = top_placement(MEASURED_WORK_AREA, 1050, 300)
    assert y == (1050 - 300 - WINDOW_CHROME_HEIGHT) // 3


def test_placement_never_goes_negative_when_nothing_fits():
    """work_area None plus a screen smaller than the window: still on-screen."""
    assert top_placement(None, 200, 5000) == WORK_AREA_MARGIN


# ---------------------------------------------------------------------------
# wheel_units
# ---------------------------------------------------------------------------


def test_windows_wheel_notch_scrolls_one_unit_each_way():
    assert wheel_units(120) == -1  # away from the user == toward the start
    assert wheel_units(-120) == 1


def test_windows_fast_wheel_scrolls_proportionally():
    assert wheel_units(240) == -2
    assert wheel_units(-360) == 3


def test_macos_small_delta_still_scrolls_one_unit():
    """macOS reports counts directly, not multiples of 120; integer division
    alone would round every one of them to a dead zero."""
    assert wheel_units(1) == -1
    assert wheel_units(-1) == 1
    assert wheel_units(3) == -1


def test_x11_wheel_buttons_scroll():
    # X11 sends no delta at all -- only the button number.
    assert wheel_units(0, 4) == -1
    assert wheel_units(0, 5) == 1


def test_unset_tkinter_event_fields_are_not_trusted():
    """tkinter fills unset event fields with the string "??". A handler that
    assumed an int would raise inside a Tk callback -- which Tk prints and
    swallows, leaving a window that silently refuses to scroll."""
    assert wheel_units("??", "??") == 0
    assert wheel_units("??", 4) == -1  # a real X11 button, with delta unset
    assert wheel_units(120, "??") == -1  # a real Windows delta, with num unset


def test_zero_delta_is_not_a_scroll():
    assert wheel_units(0) == 0
    assert wheel_units(0, 0) == 0


def test_bool_is_not_a_wheel_delta():
    """`True` is an `int` in Python. It is not a scroll."""
    assert wheel_units(True) == 0


# ---------------------------------------------------------------------------
# thumb_span
# ---------------------------------------------------------------------------


def test_thumb_span_on_an_unmapped_track_is_empty_not_an_error():
    """Before the first `<Configure>` a canvas reports height 1, and 0 once
    destroyed; neither may raise inside a redraw."""
    assert thumb_span(0.0, 1.0, 0) == (0, 0)
    assert thumb_span(0.0, 1.0, -5) == (0, 0)


def test_thumb_fills_a_track_whose_content_fully_fits():
    assert thumb_span(0.0, 1.0, 200) == (0, 200)


def test_thumb_is_proportional_to_the_visible_fraction():
    assert thumb_span(0.0, 0.5, 200) == (0, 100)


def test_thumb_follows_the_scroll_offset():
    assert thumb_span(0.5, 1.0, 200) == (100, 200)


def test_a_very_long_content_still_gets_a_grabbable_thumb():
    top, bottom = thumb_span(0.0, 0.01, 200)
    assert bottom - top == MIN_THUMB_LENGTH


def test_a_min_length_thumb_at_the_end_stays_inside_the_track():
    """The bug a naive `top = first * track` has: the thumb hangs off the
    bottom exactly when the user has scrolled to the end."""
    track = 200
    top, bottom = thumb_span(0.99, 1.0, track)
    assert bottom == track
    assert bottom - top == MIN_THUMB_LENGTH


def test_thumb_never_leaves_the_track_at_any_offset():
    track = 200
    for step in range(0, 101):
        first = step / 100
        top, bottom = thumb_span(first, min(first + 0.02, 1.0), track)
        assert 0 <= top <= bottom <= track


def test_thumb_span_clamps_fractions_tk_reports_outside_zero_to_one():
    assert thumb_span(-0.4, 1.6, 200) == (0, 200)


def test_min_length_never_exceeds_a_track_shorter_than_it():
    top, bottom = thumb_span(0.0, 0.01, 10)
    assert (top, bottom) == (0, 10)


# ---------------------------------------------------------------------------
# drag_fraction
# ---------------------------------------------------------------------------


def test_drag_maps_pointer_to_the_matching_view_fraction():
    assert drag_fraction(pointer_y=50, grab_offset=0, track_length=200, visible=0.5) == pytest.approx(0.25)


def test_drag_keeps_the_offset_the_thumb_was_grabbed_at():
    """Without this the thumb jumps to centre itself under the cursor on the
    first pixel of movement."""
    assert drag_fraction(pointer_y=60, grab_offset=10, track_length=200, visible=0.5) == pytest.approx(0.25)


def test_drag_clamps_at_the_start():
    assert drag_fraction(pointer_y=-80, grab_offset=0, track_length=200, visible=0.5) == 0.0


def test_drag_clamps_at_the_end_leaving_the_last_page_visible():
    """Past `1 - visible` the content scrolls off its own end and Tk snaps it
    back, which reads as a stuck scrollbar."""
    assert drag_fraction(pointer_y=999, grab_offset=0, track_length=200, visible=0.5) == pytest.approx(0.5)


def test_drag_on_an_unmapped_track_is_the_start_not_a_division_by_zero():
    assert drag_fraction(pointer_y=10, grab_offset=0, track_length=0, visible=0.5) == 0.0


def test_drag_of_fully_visible_content_stays_at_the_start():
    assert drag_fraction(pointer_y=999, grab_offset=0, track_length=200, visible=1.0) == 0.0


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_scrollbar_span_accounts_for_the_bar_and_its_gap():
    """The windows size themselves from the content's request, so the bar has
    to be paid for explicitly or it eats the rightmost column of every row."""
    assert SCROLLBAR_SPAN > SCROLLBAR_WIDTH
