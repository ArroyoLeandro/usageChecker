"""`ui/colors.py`: showing a colour is not choosing it, and everything after.

The load-bearing test in this file is
`test_prefilled_dark_then_switch_to_light_renders_the_light_preset`. Every
other one is a detail of the rule it proves. The colour rows now display the
preset's own colour instead of sitting empty, and if that display were allowed
to mean "the user pinned this", the sparse map would fill with nine overrides
nobody chose and the Oscuro -> Claro switch would render dark forever.

There is no display on this box, so none of this is a claim about pixels. It
is a claim about what would be *persisted*, which is the half that decides
whether the preset switch works at all -- and the half a screenshot could not
tell you anyway.
"""

from __future__ import annotations

import pytest

from claude_usage_tray.settings import (
    Settings,
    build_theme,
    preset_palette,
    with_colors,
    with_theme,
)
from claude_usage_tray.theme import DARK, LIGHT, PALETTE_FIELDS
from claude_usage_tray.ui import colors


# --- effective ------------------------------------------------------------


def test_effective_falls_back_to_the_preset_for_an_absent_field():
    assert colors.effective({}, DARK, "bg") == DARK.bg


def test_effective_prefers_the_override():
    assert colors.effective({"bg": "#123456"}, DARK, "bg") == "#123456"


def test_effective_normalizes_a_short_override():
    assert colors.effective({"bg": "#ABC"}, DARK, "bg") == "#aabbcc"


def test_effective_falls_back_to_the_preset_for_an_unparseable_override():
    # A hand-edited config.json is the only way to get here, and the answer
    # has to be a colour Tk can render, not an exception at render time.
    assert colors.effective({"bg": "OOPS"}, DARK, "bg") == DARK.bg


@pytest.mark.parametrize("field", PALETTE_FIELDS)
def test_effective_is_defined_for_every_palette_field(field):
    assert colors.effective({}, DARK, field) == getattr(DARK, field)


# --- color_rows -----------------------------------------------------------


def test_color_rows_covers_every_field_in_palette_order():
    rows = colors.color_rows({}, DARK, {})
    assert tuple(row.field for row in rows) == PALETTE_FIELDS


def test_an_inherited_row_shows_the_preset_colour_but_is_not_pinned():
    # The whole point of change 4, and the whole trap of it, in one assertion.
    row = colors.color_rows({}, DARK, {})[0]
    assert row.field == "bg"
    assert row.value == DARK.bg  # it SHOWS the preset's colour
    assert row.pinned is False  # it does NOT claim the user chose it


def test_an_overridden_row_is_pinned():
    rows = {row.field: row for row in colors.color_rows({"bg": "#123456"}, DARK, {})}
    assert rows["bg"].value == "#123456"
    assert rows["bg"].pinned is True
    assert rows["panel"].pinned is False


def test_a_row_pinned_to_the_presets_own_colour_is_still_pinned():
    # Pinning `bg` to exactly dark's `bg` looks identical to inheriting it,
    # right up until the user switches preset -- at which point one of them
    # follows and the other does not. `pinned` has to answer from the map, not
    # from whether the value happens to match.
    rows = {row.field: row for row in colors.color_rows({"bg": DARK.bg}, DARK, {})}
    assert rows["bg"].value == DARK.bg
    assert rows["bg"].pinned is True


def test_revertable_is_false_when_nothing_moved():
    stored = {"bg": "#123456"}
    rows = colors.color_rows(stored, DARK, stored)
    assert all(row.revertable is False for row in rows)


def test_revertable_is_true_for_a_field_that_moved_off_the_baseline():
    rows = {row.field: row for row in colors.color_rows({"bg": "#123456"}, DARK, {})}
    assert rows["bg"].revertable is True
    assert rows["panel"].revertable is False


def test_revertable_is_true_for_a_field_that_was_unpinned_since_the_baseline():
    rows = {row.field: row for row in colors.color_rows({}, DARK, {"bg": "#123456"})}
    assert rows["bg"].revertable is True


# --- with_pick ------------------------------------------------------------


def test_with_pick_pins_the_field():
    assert colors.with_pick({}, "bg", "#123456") == {"bg": "#123456"}


def test_with_pick_normalizes_and_does_not_mutate_its_input():
    stored = {"panel": "#111111"}
    assert colors.with_pick(stored, "bg", "#ABC") == {"panel": "#111111", "bg": "#aabbcc"}
    assert stored == {"panel": "#111111"}  # untouched


def test_with_pick_leaves_other_fields_sparse():
    picked = colors.with_pick({}, "accent", "#00ff00")
    assert picked == {"accent": "#00ff00"}
    assert len(picked) == 1  # not nine


def test_with_pick_ignores_an_unknown_field_or_an_unreadable_colour():
    assert colors.with_pick({}, "background", "#123456") == {}
    assert colors.with_pick({}, "bg", "chartreuse") == {}
    assert colors.with_pick({}, "bg", None) == {}


# --- with_revert ----------------------------------------------------------


def test_with_revert_restores_the_baselines_hex():
    assert colors.with_revert({"bg": "#123456"}, {"bg": "#abcdef"}, "bg") == {"bg": "#abcdef"}


def test_with_revert_unpins_a_field_the_baseline_never_pinned():
    # The one that matters: reverting to "inheriting" must REMOVE the key, not
    # pin it to the colour the preset happens to have today.
    assert colors.with_revert({"bg": "#123456"}, {}, "bg") == {}


def test_with_revert_touches_only_the_named_field():
    stored = {"bg": "#123456", "accent": "#00ff00"}
    assert colors.with_revert(stored, {}, "bg") == {"accent": "#00ff00"}


def test_with_revert_is_idempotent():
    once = colors.with_revert({"bg": "#123456"}, {}, "bg")
    twice = colors.with_revert(once, {}, "bg")
    assert once == twice == {}


def test_with_revert_ignores_an_unknown_field():
    assert colors.with_revert({"bg": "#123456"}, {}, "background") == {"bg": "#123456"}


# --- submitted ------------------------------------------------------------


def test_submitted_persists_nothing_when_nothing_is_pinned():
    # Every field carries text now, because every field is pre-filled. If
    # `submitted` read the text instead of the pin set, this would return all
    # nine and the preset switch would be dead.
    texts = {field: getattr(DARK, field) for field in PALETTE_FIELDS}
    overrides, invalid = colors.submitted(texts, set())
    assert overrides == {}
    assert invalid == ()


def test_submitted_persists_only_the_pinned_fields():
    texts = {field: getattr(DARK, field) for field in PALETTE_FIELDS}
    texts["accent"] = "#00ff00"
    overrides, invalid = colors.submitted(texts, {"accent"})
    assert overrides == {"accent": "#00ff00"}
    assert invalid == ()


def test_submitted_reports_an_unreadable_pinned_field_and_drops_it():
    overrides, invalid = colors.submitted({"bg": "OOPS", "accent": "#00ff00"}, {"bg", "accent"})
    assert invalid == ("bg",)
    assert "bg" not in overrides


def test_submitted_ignores_an_unreadable_field_that_is_not_pinned():
    overrides, invalid = colors.submitted({"bg": "OOPS"}, set())
    assert (overrides, invalid) == ({}, ())


def test_submitted_normalizes_what_it_persists():
    overrides, _ = colors.submitted({"bg": "  #ABC  "}, {"bg"})
    assert overrides == {"bg": "#aabbcc"}


# --- normalize_choice -----------------------------------------------------


def test_normalize_choice_reads_the_text_when_it_is_a_plain_hex():
    assert colors.normalize_choice((18.0, 52.0, 86.0), "#123456") == "#123456"


def test_normalize_choice_falls_back_to_the_tuple_for_a_wide_spelling():
    # Tk colours may legally be `#rrrrggggbbbb`, which `parse_hex_color`
    # rejects by design. Without the fallback a colour the user really did
    # pick would look exactly like a cancel.
    assert colors.normalize_choice((255.0, 0.0, 170.0), "#ffff0000aaaa") == "#ff00aa"


def test_normalize_choice_handles_the_float_channels_tkinter_actually_returns():
    # `tkinter.colorchooser` returns `(r/256, g/256, b/256)` -- floats, not
    # the ints the shape suggests.
    assert colors.normalize_choice((254.99609375, 0.0, 0.0), "bogus") == "#fe0000"


def test_normalize_choice_returns_none_on_cancel():
    assert colors.normalize_choice(None, None) is None


def test_normalize_choice_returns_none_when_neither_half_is_readable():
    assert colors.normalize_choice("nope", "nope") is None


def test_normalize_choice_clamps_out_of_range_channels():
    assert colors.normalize_choice((-5, 300, 128), None) == "#00ff80"


# --- the round trip -------------------------------------------------------
#
# The reason this module exists, exercised end to end against the real
# settings model rather than against a restatement of it.


def _submit_untouched_prefill(settings: Settings) -> Settings:
    """What the colour section persists when the user only *looks* at it.

    Reproduces the tab's own path exactly: build the rows, seed the pin set
    from them (never from the text), pre-fill every field with the colour it
    renders as, then hit "Aplicar colores" without touching anything.
    """
    preset = preset_palette(settings)
    rows = colors.color_rows(settings.colors, preset, settings.colors)
    pinned = {row.field for row in rows if row.pinned}
    texts = {row.field: row.value for row in rows}
    overrides, invalid = colors.submitted(texts, pinned)
    assert invalid == ()
    return with_colors(settings, overrides)


def test_prefilled_dark_then_switch_to_light_renders_the_light_preset():
    """Change 4's trap, closed.

    Pre-fill under dark, save, switch to light: the light preset must be what
    renders. If the pre-fill had been persisted, all nine dark values would be
    sitting on top of the light preset and the window would stay dark -- the
    theme switch would look broken, and the user would have "chosen" nine
    colours they never picked.
    """
    dark_settings = Settings(theme="dark")
    assert build_theme(dark_settings).palette == DARK

    # The user opens the window on dark, sees nine pre-filled hex fields, and
    # applies without choosing anything.
    saved = _submit_untouched_prefill(dark_settings)
    assert saved.colors == {}, "a colour merely displayed must never be persisted"

    # ... then switches to the light preset.
    switched = with_theme(saved, "light")
    assert build_theme(switched).palette == LIGHT


def test_a_deliberate_pick_survives_the_switch_and_the_other_eight_do_not():
    """The other half: a colour genuinely chosen has to stick.

    This is the user-settings spec's "The preset still governs un-overridden
    fields" scenario, driven through the picker's own entry point.
    """
    settings = Settings(theme="dark")
    picked = with_colors(settings, colors.with_pick(settings.colors, "accent", "#00ff00"))
    assert picked.colors == {"accent": "#00ff00"}

    switched = with_theme(picked, "light")
    palette = build_theme(switched).palette
    assert palette.accent == "#00ff00"  # the user's, kept
    assert palette.bg == LIGHT.bg  # everything else, the light preset's
    assert palette.fg == LIGHT.fg


def test_revert_returns_a_picked_colour_to_inheriting_and_the_switch_works_again():
    """Pick, then "volver": the field goes back to tracking the preset.

    Reverting to the baseline of an un-pinned field must remove the override
    rather than freeze it at today's preset value -- otherwise revert would
    silently pin the colour it was supposed to unpin.
    """
    settings = Settings(theme="dark")
    baseline = dict(settings.colors)  # {} -- the window opened with no overrides

    picked = with_colors(settings, colors.with_pick(settings.colors, "bg", "#00ff00"))
    assert picked.colors == {"bg": "#00ff00"}
    assert build_theme(picked).palette.bg == "#00ff00"

    reverted = with_colors(picked, colors.with_revert(picked.colors, baseline, "bg"))
    assert reverted.colors == {}
    assert build_theme(reverted).palette.bg == DARK.bg

    # And the preset switch is intact, which is what "unpinned" has to mean.
    assert build_theme(with_theme(reverted, "light")).palette == LIGHT


def test_revert_returns_to_a_baseline_that_was_itself_a_pinned_colour():
    """Reopening on a saved colour: "previous" is what you walked in with."""
    settings = Settings(theme="dark", colors={"bg": "#111111"})
    baseline = dict(settings.colors)

    picked = with_colors(settings, colors.with_pick(settings.colors, "bg", "#222222"))
    assert picked.colors == {"bg": "#222222"}

    reverted = with_colors(picked, colors.with_revert(picked.colors, baseline, "bg"))
    assert reverted.colors == {"bg": "#111111"}
    assert build_theme(reverted).palette.bg == "#111111"


def test_prefill_under_light_is_equally_inert():
    """The trap is not dark-specific; neither is the fix."""
    light_settings = Settings(theme="light")
    saved = _submit_untouched_prefill(light_settings)
    assert saved.colors == {}
    assert build_theme(with_theme(saved, "dark")).palette == DARK


def test_prefill_preserves_a_pre_existing_override_without_adding_the_other_eight():
    """A saved colour must survive a visit that touched nothing else."""
    settings = Settings(theme="dark", colors={"accent": "#00ff00"})
    saved = _submit_untouched_prefill(settings)
    assert saved.colors == {"accent": "#00ff00"}
    assert build_theme(with_theme(saved, "light")).palette.bg == LIGHT.bg


def test_typing_pins_only_the_field_that_was_typed_in():
    """The text path's equivalent of the round trip above.

    A keystroke is what pins a field now, since a non-empty field stopped
    meaning anything the moment every field was pre-filled.
    """
    settings = Settings(theme="dark")
    preset = preset_palette(settings)
    rows = colors.color_rows(settings.colors, preset, settings.colors)
    pinned = {row.field for row in rows if row.pinned}
    texts = {row.field: row.value for row in rows}

    # The user types in the `fg` field, and only that one.
    texts["fg"] = "#abcdef"
    pinned.add("fg")

    overrides, invalid = colors.submitted(texts, pinned)
    assert (overrides, invalid) == ({"fg": "#abcdef"}, ())

    saved = with_colors(settings, overrides)
    switched = with_theme(saved, "light")
    palette = build_theme(switched).palette
    assert palette.fg == "#abcdef"
    assert palette.bg == LIGHT.bg
