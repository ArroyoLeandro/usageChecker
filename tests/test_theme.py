"""Unit tests for claude_usage_tray.theme.

The invariant that makes `Theme.font()` safe to introduce is this table:
`Theme(palette=DARK, base_size=9).font(role)` must reproduce today's font
literal exactly, role by role (design.md, "Theme returns font tuples").
"""

from __future__ import annotations

import pytest

from claude_usage_tray.theme import (
    AA_CONTRAST_RATIO,
    DARK,
    HOVER_MIX,
    LIGHT,
    MIN_SHADE_CONTRAST,
    PALETTE_FIELDS,
    PRESSED_MIX,
    RING_MIN_CONTRAST,
    TEXT_CONTRAST_PAIRS,
    Palette,
    Theme,
    contrast_ratio,
    contrast_report,
    disabled_foreground,
    focus_ring,
    interaction_shades,
    mix_colors,
    parse_hex_color,
    relative_luminance,
    shade_target,
    with_overrides,
)


def _luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of an `#rrggbb` string.

    Kept as an independent second implementation rather than delegating to
    `theme.relative_luminance`: the preset contrast gates below are the whole
    reason the light palette's hexes are what they are, and a gate that
    imports its own oracle from the code under test can only ever agree with
    it. The two are cross-checked directly further down.
    """
    raw = hex_color.lstrip("#")
    channels = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio. AA requires >= 4.5:1 for normal-size text."""
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# The 6 literal font shapes measured directly in app.py before extraction.
EXPECTED_FONTS_AT_BASE_9 = {
    "small": ("Segoe UI", 8),
    "small_bold": ("Segoe UI", 8, "bold"),
    "body": ("Segoe UI", 9),
    "body_bold": ("Segoe UI", 9, "bold"),
    "heading": ("Segoe UI", 10, "bold"),
    "title": ("Segoe UI", 11, "bold"),
}


@pytest.mark.parametrize("role,expected", list(EXPECTED_FONTS_AT_BASE_9.items()))
def test_theme_font_reproduces_todays_literals(role, expected):
    theme = Theme(palette=DARK, base_size=9)
    assert theme.font(role) == expected


def test_theme_font_offsets_scale_with_base_size():
    theme = Theme(palette=DARK, base_size=12)
    assert theme.font("small") == ("Segoe UI", 11)
    assert theme.font("body") == ("Segoe UI", 12)
    assert theme.font("heading") == ("Segoe UI", 13, "bold")
    assert theme.font("title") == ("Segoe UI", 14, "bold")


def test_dark_palette_matches_apps_original_hex_values():
    assert DARK == Palette(
        bg="#262522",
        panel="#2f2d29",
        track="#4a4743",
        fg="#f7f3ee",
        muted="#b9b1a6",
        accent="#d97757",
        warn="#f59e0b",
        danger="#ef4444",
        border="#3a3733",
    )


def test_palette_has_nine_fields_no_link():
    assert set(Palette.__dataclass_fields__.keys()) == {
        "bg",
        "panel",
        "track",
        "fg",
        "muted",
        "accent",
        "warn",
        "danger",
        "border",
    }


def test_light_is_a_real_palette_not_a_placeholder():
    # Slice (c) resolved design.md's open question: LIGHT was a stub equal to
    # DARK for as long as no theme picker could surface it. It can now.
    assert LIGHT != DARK


def test_light_keeps_claudes_accent_recognizable():
    assert LIGHT.accent == DARK.accent == "#d97757"


def test_light_is_genuinely_light_and_dark_is_genuinely_dark():
    assert _luminance(LIGHT.bg) > 0.7
    assert _luminance(LIGHT.fg) < 0.1
    assert _luminance(DARK.bg) < 0.1
    assert _luminance(DARK.fg) > 0.7


@pytest.mark.parametrize("palette_name,palette", [("DARK", DARK), ("LIGHT", LIGHT)])
@pytest.mark.parametrize("field_name", ["fg", "muted"])
def test_body_text_meets_wcag_aa_on_the_window_background(palette_name, palette, field_name):
    ratio = _contrast_ratio(getattr(palette, field_name), palette.bg)
    assert ratio >= 4.5, f"{palette_name}.{field_name} on {palette_name}.bg is {ratio:.2f}:1, below AA (4.5:1)"


@pytest.mark.parametrize("palette_name,palette", [("DARK", DARK), ("LIGHT", LIGHT)])
@pytest.mark.parametrize("field_name", ["fg", "muted"])
def test_body_text_meets_wcag_aa_on_the_panel_surface(palette_name, palette, field_name):
    # Most labels actually sit on `panel`, so gate it too, not just `bg`.
    ratio = _contrast_ratio(getattr(palette, field_name), palette.panel)
    assert ratio >= 4.5, f"{palette_name}.{field_name} on {palette_name}.panel is {ratio:.2f}:1, below AA (4.5:1)"


@pytest.mark.parametrize("palette_name,palette", [("DARK", DARK), ("LIGHT", LIGHT)])
def test_warning_text_meets_wcag_aa_on_the_panel_it_renders_on(palette_name, palette):
    # `palette.warn` is a text color (ui/popup.py's rate-limit warning), not
    # a bar fill -- the bar fills come from formatting.bar_color().
    ratio = _contrast_ratio(palette.warn, palette.panel)
    assert ratio >= 4.5, f"{palette_name}.warn on {palette_name}.panel is {ratio:.2f}:1, below AA (4.5:1)"


def test_panel_stays_the_elevated_surface_in_both_themes():
    # The relationship is mirrored, not inverted: `panel` is nearer the light
    # source than `bg` in both themes, so a row on `bg` reads as recessed
    # either way.
    assert _luminance(DARK.panel) > _luminance(DARK.bg)
    assert _luminance(LIGHT.panel) > _luminance(LIGHT.bg)


def test_light_and_dark_expose_exactly_the_same_fields():
    assert set(LIGHT.__dataclass_fields__) == set(DARK.__dataclass_fields__)


def test_palette_is_frozen():
    with pytest.raises(Exception):
        DARK.bg = "#000000"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Requirement: Custom Hex Palettes -- parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#1a2b3c", "#1a2b3c"),
        ("#1A2B3C", "#1a2b3c"),  # normalized, so storage has one spelling
        ("  #1a2b3c  ", "#1a2b3c"),
        ("#abc", "#aabbcc"),  # short form expands
        ("#ABC", "#aabbcc"),
        ("#000", "#000000"),
        ("#ffffff", "#ffffff"),
    ],
)
def test_parse_hex_color_normalizes_every_accepted_spelling(raw, expected):
    assert parse_hex_color(raw) == expected


@pytest.mark.parametrize(
    "bogus",
    [
        "1a2b3c",  # no leading #
        "#12345",  # wrong length
        "#1234567",
        "#12",
        "#",
        "",
        "#gggggg",  # not hex digits
        "#12345g",
        "red",  # Tk would take it; we cannot measure its contrast
        "#rrggbb",
        "rgb(1,2,3)",
        None,
        7,
        ["#1a2b3c"],
        {"bg": "#1a2b3c"},
        True,
    ],
)
def test_parse_hex_color_rejects_anything_it_cannot_measure(bogus):
    # Rejected here, where a test can see it -- not at Tk's `bg=`, which
    # raises TclError at render time on a seam no headless test can reach.
    assert parse_hex_color(bogus) is None


def test_parse_hex_color_is_idempotent():
    once = parse_hex_color("#ABC")
    assert parse_hex_color(once) == once


# ---------------------------------------------------------------------------
# Requirement: Custom Hex Palettes -- overrides
# ---------------------------------------------------------------------------


def test_with_overrides_replaces_only_the_named_fields():
    result = with_overrides(DARK, {"accent": "#00ff00"})

    assert result.accent == "#00ff00"
    assert result.bg == DARK.bg
    assert result.fg == DARK.fg


def test_with_overrides_is_sparse_so_an_untouched_field_still_tracks_the_preset():
    dark_custom = with_overrides(DARK, {"accent": "#00ff00"})
    light_custom = with_overrides(LIGHT, {"accent": "#00ff00"})

    assert dark_custom.bg == DARK.bg
    assert light_custom.bg == LIGHT.bg


def test_with_overrides_normalizes_as_it_applies():
    assert with_overrides(DARK, {"accent": "#ABC"}).accent == "#aabbcc"


@pytest.mark.parametrize("overrides", [{}, {"nonsense": "#000000"}, {"accent": "not-a-color"}])
def test_with_overrides_ignores_anything_unusable(overrides):
    # A Palette must never be constructible with a value Tk would choke on.
    assert with_overrides(DARK, overrides) == DARK


def test_with_overrides_can_replace_every_field_at_once():
    everything = {name: "#123456" for name in PALETTE_FIELDS}
    result = with_overrides(DARK, everything)

    assert {getattr(result, name) for name in PALETTE_FIELDS} == {"#123456"}


def test_with_overrides_does_not_mutate_the_preset():
    with_overrides(DARK, {"bg": "#ffffff"})

    assert DARK.bg == "#262522"


def test_palette_fields_is_derived_from_the_dataclass_not_retyped():
    assert PALETTE_FIELDS == tuple(Palette.__dataclass_fields__)
    assert len(PALETTE_FIELDS) == 9


# ---------------------------------------------------------------------------
# Contrast math -- cross-checked against this file's independent oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("color", ["#000000", "#ffffff", "#d97757", "#262522", "#f0ede7", "#7f7f7f"])
def test_shipped_luminance_agrees_with_the_independent_oracle(color):
    assert relative_luminance(color) == pytest.approx(_luminance(color))


@pytest.mark.parametrize(
    "foreground,background",
    [("#ffffff", "#000000"), ("#d97757", "#262522"), ("#232120", "#f0ede7"), ("#777777", "#888888")],
)
def test_shipped_contrast_ratio_agrees_with_the_independent_oracle(foreground, background):
    assert contrast_ratio(foreground, background) == pytest.approx(_contrast_ratio(foreground, background))


def test_contrast_ratio_hits_the_known_extremes():
    assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#7f7f7f", "#7f7f7f") == pytest.approx(1.0)


def test_contrast_ratio_is_symmetric():
    assert contrast_ratio("#ffffff", "#262522") == pytest.approx(contrast_ratio("#262522", "#ffffff"))


def test_contrast_ratio_accepts_the_short_hex_form():
    assert contrast_ratio("#fff", "#000") == pytest.approx(21.0)


@pytest.mark.parametrize("bogus", ["red", "#12345", "", None])
def test_luminance_raises_on_a_color_no_palette_could_hold(bogus):
    # Unlike parse_hex_color, this is not a tolerant boundary: every Palette
    # is built from parsed values, so an unparseable one here is our bug.
    with pytest.raises(ValueError):
        relative_luminance(bogus)


# ---------------------------------------------------------------------------
# Contrast report -- measures, never vetoes
# ---------------------------------------------------------------------------


def test_contrast_report_measures_every_declared_text_pair():
    report = contrast_report(DARK)

    assert [(check.foreground, check.background) for check in report] == list(TEXT_CONTRAST_PAIRS)


def test_contrast_report_agrees_with_the_oracle_pair_by_pair():
    for check in contrast_report(LIGHT):
        expected = _contrast_ratio(getattr(LIGHT, check.foreground), getattr(LIGHT, check.background))
        assert check.ratio == pytest.approx(expected)


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_both_presets_pass_every_pair_in_their_own_report(palette):
    # The same hard gate the parametrized AA tests above assert, restated
    # through the shipped reporting path the settings window actually calls.
    assert [check for check in contrast_report(palette) if not check.passes] == []


def test_contrast_report_reports_a_failing_custom_palette_without_raising():
    # The user is entitled to an unreadable palette; the app's job is to say
    # so, not to refuse it. This is the exact inversion of the preset gate.
    unreadable = with_overrides(DARK, {"fg": "#262523"})
    failures = [check for check in contrast_report(unreadable) if not check.passes]

    assert failures, "a near-invisible fg must be reported"
    assert all(check.ratio < AA_CONTRAST_RATIO for check in failures)


def test_contrast_check_passes_exactly_at_the_aa_boundary():
    report = contrast_report(DARK)
    assert all(check.passes is (check.ratio >= AA_CONTRAST_RATIO) for check in report)


def test_a_custom_palette_that_passes_reports_no_failures():
    inverted = with_overrides(DARK, {"fg": "#ffffff", "bg": "#000000", "panel": "#000000", "muted": "#bbbbbb"})

    assert [check for check in contrast_report(inverted) if not check.passes] == []


# --- interaction shades ---------------------------------------------------
#
# The buttons these feed are the one part of the UI a headless box cannot
# look at, so what is asserted here is not "it looks right" but the two
# properties that decide whether it *can*: the shades differ from the surface
# they came from, and they are computed from that surface rather than from a
# preset. Everything below is one of those two.
#
# `USER_PALETTE` is a real user's stored overrides, not an invented case. A
# green accent and a pure-black panel are exactly the inputs that break a
# hardcoded hover, and `#000000` is the boundary the arithmetic has to reason
# about rather than sit near.

USER_PALETTE = with_overrides(
    DARK,
    {"bg": "#2e2e2e", "panel": "#000000", "accent": "#11a800", "warn": "#ff1717"},
)

# Every (surface, text) pair the app actually builds a button from, read off
# the call sites rather than generated: `bg` and `panel` carry `fg` or
# `muted`, and `accent` only ever carries `fg`. `warn` and `danger` are text
# and glyph colours in this app and never a button's background, so pairing
# them as surfaces would assert things about buttons that do not exist --
# and `muted` on `warn` in particular measures 1.01:1, where "dimmed" is not
# a distinction any contrast rule can express.
BUTTON_PAIRS = [
    pytest.param(f"{palette_name}", surface, text, id=f"{palette_name}-{surface_name}-on-{text_name}")
    for palette_name, palette in (("dark", DARK), ("light", LIGHT), ("user", USER_PALETTE))
    for surface_name, surface, text_name, text in (
        ("bg", palette.bg, "fg", palette.fg),
        ("bg", palette.bg, "muted", palette.muted),
        ("panel", palette.panel, "fg", palette.fg),
        ("panel", palette.panel, "muted", palette.muted),
        ("accent", palette.accent, "fg", palette.fg),
    )
]


def test_mix_colors_returns_the_endpoints_unchanged():
    assert mix_colors("#102030", "#ffffff", 0.0) == "#102030"
    assert mix_colors("#102030", "#ffffff", 1.0) == "#ffffff"


def test_mix_colors_clamps_out_of_range_amounts():
    assert mix_colors("#102030", "#ffffff", -5.0) == "#102030"
    assert mix_colors("#102030", "#ffffff", 12.0) == "#ffffff"


def test_mix_colors_is_linear_per_channel():
    assert mix_colors("#000000", "#ffffff", 0.5) == "#808080"


def test_mix_colors_normalizes_the_short_form():
    assert mix_colors("#000", "#fff", 1.0) == "#ffffff"


@pytest.mark.parametrize("bad", ["nope", "", "#12", 42, None])
def test_mix_colors_rejects_what_it_cannot_measure(bad):
    with pytest.raises(ValueError):
        mix_colors(bad, "#ffffff", 0.5)
    with pytest.raises(ValueError):
        mix_colors("#ffffff", bad, 0.5)


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_every_real_button_surface_gets_two_visible_distinct_shades(palette_name, surface, text):
    # The whole defect, stated as an assertion: the buttons this replaces set
    # their pressed surface equal to their resting one, so pressing changed
    # nothing. Neither shade may equal the surface, and the two may not equal
    # each other, on any palette this app can be handed.
    hover, pressed = interaction_shades(surface, text)

    assert hover != surface
    assert pressed != surface
    assert pressed != hover


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_pressed_moves_further_from_the_surface_than_hover(palette_name, surface, text):
    hover, pressed = interaction_shades(surface, text)
    surface_luminance = relative_luminance(surface)

    hover_distance = abs(relative_luminance(hover) - surface_luminance)
    pressed_distance = abs(relative_luminance(pressed) - surface_luminance)

    assert pressed_distance > hover_distance


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_both_shades_move_the_same_direction(palette_name, surface, text):
    # Hover and pressed are one gesture deepening, not two unrelated colours,
    # so they must sit on the same side of the resting surface.
    hover, pressed = interaction_shades(surface, text)
    surface_luminance = relative_luminance(surface)

    hover_delta = relative_luminance(hover) - surface_luminance
    pressed_delta = relative_luminance(pressed) - surface_luminance

    assert (hover_delta > 0) == (pressed_delta > 0)


def test_the_shades_are_derived_from_the_surface_not_from_a_preset():
    # The property the user's green accent depends on. A hardcoded hover
    # would pass every test above and fail this one.
    hover, pressed = interaction_shades("#11a800", DARK.fg)

    for shade in (hover, pressed):
        red, green, blue = (int(shade[index : index + 2], 16) for index in (1, 3, 5))
        assert green > red and green > blue, f"{shade} is not still green"


def test_a_pure_black_surface_lightens_because_it_cannot_darken():
    # The user's `panel` is `#000000`. Shading "away from the light text"
    # would land on black again and acknowledge nothing, so the headroom rule
    # has to overrule the text rule here.
    hover, pressed = interaction_shades("#000000", DARK.muted)

    assert shade_target("#000000", DARK.muted) == "#ffffff"
    assert relative_luminance(hover) > 0.0
    assert relative_luminance(pressed) > relative_luminance(hover)


def test_a_pure_white_surface_darkens_because_it_cannot_lighten():
    hover, pressed = interaction_shades("#ffffff", "#000000")

    assert shade_target("#ffffff", "#000000") == "#000000"
    assert relative_luminance(pressed) < relative_luminance(hover) < 1.0


def test_a_surface_with_room_shades_away_from_its_own_text():
    # Light text on a mid-dark surface: darkening keeps the label readable,
    # and the surface has the contrast headroom to do it.
    assert contrast_ratio("#808080", "#000000") >= MIN_SHADE_CONTRAST
    assert shade_target("#808080", "#ffffff") == "#000000"
    assert shade_target("#808080", "#000000") == "#ffffff"


def test_a_surface_without_room_overrules_the_text_rule():
    # The light preset's panel is a hair off white, so "away from the dark
    # text" is a direction it cannot move in far enough to be seen.
    assert contrast_ratio(LIGHT.panel, "#ffffff") < MIN_SHADE_CONTRAST
    assert shade_target(LIGHT.panel, LIGHT.muted) == "#000000"


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_a_swatch_whose_text_is_its_own_fill_still_reacts(palette_name, surface, text):
    # The colour picker's swatches pass the same hex as `bg` and `fg`, which
    # makes every contrast measurement against the text degenerate. The
    # headroom rule is what still produces an answer.
    hover, pressed = interaction_shades(surface, surface)

    assert hover != surface
    assert pressed not in (surface, hover)


def test_the_shade_amounts_are_ordered():
    assert 0 < HOVER_MIX < PRESSED_MIX < 1


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_a_disabled_label_lands_between_its_text_and_its_surface(palette_name, surface, text):
    # Channel by channel, not by luminance. Two colours of near-equal
    # luminance but different hue have a blend whose luminance sits outside
    # both -- true of `muted` on `warn` (1.01:1) -- so luminance is the wrong
    # axis to state this on even though it is the right one for the shades.
    dimmed = disabled_foreground(text, surface)

    for index in (1, 3, 5):
        channel = int(dimmed[index : index + 2], 16)
        low, high = sorted(
            (int(text[index : index + 2], 16), int(surface[index : index + 2], 16))
        )
        assert low <= channel <= high


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_a_disabled_label_is_less_visible_than_an_enabled_one(palette_name, surface, text):
    # Only meaningful where the enabled label was visible to begin with,
    # which on every real button pair it is -- these are the app's text
    # colours on the app's surfaces.
    dimmed = disabled_foreground(text, surface)

    assert contrast_ratio(dimmed, surface) < contrast_ratio(text, surface)


def test_disabled_text_on_its_own_colour_stays_put():
    # A swatch is its own foreground; there is nothing to dim toward.
    assert disabled_foreground("#11a800", "#11a800") == "#11a800"


def test_focus_ring_prefers_the_first_candidate_that_is_visible_enough():
    # Ranked by intent: a ring in the button's own text colour looks
    # deliberate, so it wins whenever it clears the floor even if a later
    # candidate would measure higher.
    assert focus_ring("#000000", "#cccccc", "#ffffff") == "#cccccc"
    assert focus_ring("#000000", "#111111", "#ffffff") == "#ffffff"
    assert focus_ring("#ffffff", "#eeeeee", "#000000") == "#000000"


@pytest.mark.parametrize("palette_name, surface, text", BUTTON_PAIRS)
def test_focus_ring_always_finds_something_visible(palette_name, surface, text):
    # The guarantee, on every surface the app builds: black and white are
    # appended to whatever the caller offered, and one of them always clears
    # the floor. `fg` on the dark preset's `accent` is 2.83:1 and does not.
    ring = focus_ring(surface, text, DARK.fg, DARK.accent)

    assert contrast_ratio(ring, surface) >= RING_MIN_CONTRAST


def test_focus_ring_falls_back_to_black_or_white_when_the_palette_cannot():
    assert contrast_ratio(DARK.fg, DARK.accent) < RING_MIN_CONTRAST
    assert focus_ring(DARK.accent, DARK.fg, DARK.accent) in ("#ffffff", "#000000")


def test_focus_ring_rejects_a_swatch_ringed_in_its_own_fill():
    # The case the fallbacks exist for: a swatch's foreground *is* its
    # background, so the obvious first candidate is the invisible one.
    ring = focus_ring(USER_PALETTE.accent, USER_PALETTE.accent, USER_PALETTE.fg, USER_PALETTE.accent)

    assert ring != USER_PALETTE.accent


def test_focus_ring_needs_something_to_choose_between():
    with pytest.raises(ValueError):
        focus_ring("#000000")
