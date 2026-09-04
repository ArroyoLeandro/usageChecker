"""Tests for claude_usage_tray.ui.button_state -- the flat button's decisions.

`widgets.FlatButton` replaces `tk.Button` because Tk on macOS draws the
native Aqua control and throws away every colour option the app passes it.
Getting the colours back means re-implementing press, hover, focus and
disabled by hand -- and a hand-written button is exactly the kind of thing
that breaks in ways a screenshot shows and no assertion catches, so every
decision it makes was put in a module with no tkinter in it and is tested
here.

No real tkinter import, on the same terms as `test_dismiss.py`: this is the
half of the button that is decidable without a display, which on this box is
the only half that is decidable at all.
"""

from __future__ import annotations

import pytest

from claude_usage_tray.theme import (
    DARK,
    LIGHT,
    RING_MIN_CONTRAST,
    contrast_ratio,
    with_overrides,
)
from claude_usage_tray.ui import button_state

# The same real user palette `test_theme.py` measures against: a green accent
# and a pure-black panel, which is what a button reading colours off a preset
# instead of off its caller would get wrong.
USER_PALETTE = with_overrides(
    DARK,
    {"bg": "#2e2e2e", "panel": "#000000", "accent": "#11a800", "warn": "#ff1717"},
)

PALETTES = [
    pytest.param(DARK, id="dark"),
    pytest.param(LIGHT, id="light"),
    pytest.param(USER_PALETTE, id="user"),
]


# --- resolve --------------------------------------------------------------


@pytest.mark.parametrize("palette", PALETTES)
def test_resolve_keeps_the_caller_colours_as_the_resting_pair(palette):
    colors = button_state.resolve(palette.accent, palette.fg, palette)

    assert colors.background == palette.accent
    assert colors.foreground == palette.fg


@pytest.mark.parametrize("palette", PALETTES)
def test_resolve_gives_every_state_a_colour_of_its_own(palette):
    colors = button_state.resolve(palette.panel, palette.muted, palette)

    assert len({colors.background, colors.hover, colors.pressed}) == 3


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("surface_field", ["bg", "panel", "accent"])
def test_the_focus_ring_is_visible_against_the_button(palette, surface_field):
    # A ring nobody can see is not an accessibility feature. 3:1 is WCAG's
    # bar for a non-text graphical indicator (1.4.11). This holds even on
    # the dark preset's `accent`, whose own `fg` sits at 2.83:1 and loses to
    # the monochrome fallback.
    surface = getattr(palette, surface_field)
    colors = button_state.resolve(surface, palette.fg, palette)

    assert contrast_ratio(colors.ring, colors.background) >= RING_MIN_CONTRAST


def test_a_swatch_is_not_ringed_in_its_own_fill():
    # The colour picker builds these with `bg` and `fg` the same hex, so the
    # button's own foreground is the one candidate that cannot work.
    colors = button_state.resolve("#11a800", "#11a800", USER_PALETTE)

    assert colors.ring != "#11a800"


# --- surface --------------------------------------------------------------


def _colors():
    return button_state.resolve(DARK.panel, DARK.muted, DARK)


def test_a_resting_button_shows_the_colours_it_was_given():
    colors = _colors()

    assert button_state.surface(colors, enabled=True, hovering=False, pressed=False) == (
        colors.background,
        colors.foreground,
    )


def test_hovering_shows_the_hover_surface():
    colors = _colors()

    assert button_state.surface(colors, enabled=True, hovering=True, pressed=False) == (
        colors.hover,
        colors.foreground,
    )


def test_pressing_shows_the_pressed_surface():
    colors = _colors()

    assert button_state.surface(colors, enabled=True, hovering=True, pressed=True) == (
        colors.pressed,
        colors.foreground,
    )


def test_dragging_off_a_held_button_stops_looking_pressed():
    # Held down, pointer gone: the click is about to be cancelled, so the
    # button must stop promising it. This is the same rule `released_inside`
    # enforces for the handler, shown to the eye.
    colors = _colors()

    assert button_state.surface(colors, enabled=True, hovering=False, pressed=True) == (
        colors.background,
        colors.foreground,
    )


def test_a_disabled_button_dims_its_label_and_keeps_its_surface():
    colors = _colors()

    assert button_state.surface(colors, enabled=False, hovering=False, pressed=False) == (
        colors.background,
        colors.disabled,
    )


@pytest.mark.parametrize("hovering", [True, False])
@pytest.mark.parametrize("pressed", [True, False])
def test_a_disabled_button_ignores_the_pointer_entirely(hovering, pressed):
    colors = _colors()

    assert button_state.surface(colors, enabled=False, hovering=hovering, pressed=pressed) == (
        colors.background,
        colors.disabled,
    )


# --- released_inside ------------------------------------------------------


@pytest.mark.parametrize(
    "x, y",
    [(0, 0), (5, 5), (79, 23), (0, 23), (79, 0)],
    ids=["top-left", "middle", "bottom-right", "bottom-left", "top-right"],
)
def test_a_release_on_the_button_is_a_click(x, y):
    assert button_state.released_inside(x, y, 80, 24) is True


@pytest.mark.parametrize(
    "x, y",
    [(-1, 5), (5, -1), (80, 5), (5, 24), (200, 200), (-40, -40)],
    ids=["left", "above", "right", "below", "far-off", "far-off-negative"],
)
def test_a_release_off_the_button_is_not_a_click(x, y):
    # Press-and-drag-away is how a user takes back a click. `tk.Button` gave
    # this for free and a Label does not, so it is the behaviour most likely
    # to be quietly lost.
    assert button_state.released_inside(x, y, 80, 24) is False


def test_the_far_edge_is_outside():
    # Tk reports `x == width` for a release just past the right edge, so
    # counting it as inside would fire on a click that landed on the
    # neighbouring widget.
    assert button_state.released_inside(80, 12, 80, 24) is False
    assert button_state.released_inside(79, 12, 80, 24) is True


def test_a_button_with_no_size_yet_cannot_be_clicked():
    # Before the first geometry pass a widget measures 1x1 or 0x0; a release
    # cannot be attributed to it, and guessing "inside" would fire commands
    # during window construction.
    assert button_state.released_inside(0, 0, 0, 0) is False


# --- split_options --------------------------------------------------------


def test_configure_options_the_label_understands_are_forwarded():
    owned, forwarded = button_state.split_options({"text": "Guardar", "font": ("Segoe UI", 9)})

    assert owned == {}
    assert forwarded == {"text": "Guardar", "font": ("Segoe UI", 9)}


def test_command_and_state_are_answered_by_the_button():
    def noop():
        return None

    owned, forwarded = button_state.split_options({"command": noop, "state": "disabled"})

    assert owned == {"command": noop, "state": "disabled"}
    assert forwarded == {}


def test_activebackground_is_swallowed_rather_than_forwarded():
    # `tk.Label` has no such option, so forwarding it is a `TclError`.
    # `settings_window.refresh` passes it on every keystroke in the colour
    # section -- a screen no headless test can open, which is exactly why
    # this one exists.
    owned, forwarded = button_state.split_options(
        {"bg": "#11a800", "activebackground": "#11a800", "text": " ", "fg": "#11a800"}
    )

    assert "activebackground" not in owned
    assert "activebackground" not in forwarded
    assert owned == {"bg": "#11a800", "fg": "#11a800"}
    assert forwarded == {"text": " "}


def test_activeforeground_is_swallowed_too():
    owned, forwarded = button_state.split_options({"activeforeground": "#ffffff"})

    assert owned == {}
    assert forwarded == {}


def test_tk_long_spellings_normalize_to_the_short_ones():
    owned, forwarded = button_state.split_options({"background": "#000000", "foreground": "#ffffff"})

    assert owned == {"bg": "#000000", "fg": "#ffffff"}
    assert forwarded == {}


def test_nothing_in_is_nothing_out():
    assert button_state.split_options({}) == ({}, {})


# --- is_enabled -----------------------------------------------------------


@pytest.mark.parametrize("state", ["normal", "active"])
def test_only_disabled_disables(state):
    assert button_state.is_enabled(state) is True


def test_disabled_disables():
    assert button_state.is_enabled("disabled") is False


def test_an_unrecognised_state_leaves_the_button_usable():
    # A typo must not produce a button that silently refuses to be clicked.
    assert button_state.is_enabled("dsabled") is True


def test_tcl_string_objects_are_read_as_strings():
    # Tcl hands option values back as its own string type, not `str`.
    class TclString:
        def __str__(self) -> str:
            return "disabled"

    assert button_state.is_enabled(TclString()) is False
