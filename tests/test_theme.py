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
    LIGHT,
    PALETTE_FIELDS,
    TEXT_CONTRAST_PAIRS,
    Palette,
    Theme,
    contrast_ratio,
    contrast_report,
    parse_hex_color,
    relative_luminance,
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
