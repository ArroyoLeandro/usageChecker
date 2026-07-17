"""Unit tests for claude_usage_tray.settings — the user-settings model.

Pure: validation, defaults, serialization, and the settings -> Theme
mapping. No tkinter, no display server (user-settings spec, "Headless
Testability").
"""

from __future__ import annotations

import json

import pytest

from claude_usage_tray import settings
from claude_usage_tray.quotas import QUOTA_WINDOWS
from claude_usage_tray.theme import DARK, LIGHT, PALETTE_FIELDS


def every_window(tiers):
    """The per-window global map holding `tiers` in all three windows.

    Thresholds are stored per quota window now. This spells the common
    "the same tiers everywhere" case, which is both the shipped default
    and exactly what a pre-per-window config.json means.
    """
    return {window: tiers for window in QUOTA_WINDOWS}


# ---------------------------------------------------------------------------
# Requirement: Defined Defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("absent", [None, {}])
def test_absent_settings_key_yields_the_documented_defaults(absent):
    # A Slice-1 config.json has no `settings` key at all.
    result = settings.from_raw(absent)

    assert result == settings.Settings()
    assert result.theme == "dark"
    assert result.font_size == 9
    assert result.window_size is None
    assert result.alerts.enabled is True
    assert result.alerts.thresholds == every_window((80, 95))


def test_defaults_reproduce_the_apps_current_hardcoded_theme():
    theme = settings.build_theme(settings.Settings())

    assert theme.palette is DARK
    assert theme.base_size == 9
    assert theme.font("body") == ("Segoe UI", 9)


def test_a_settings_key_that_is_not_an_object_falls_back_to_defaults():
    assert settings.from_raw("nonsense") == settings.Settings()
    assert settings.from_raw([1, 2, 3]) == settings.Settings()


def test_partial_settings_only_override_what_they_name():
    result = settings.from_raw({"theme": "light"})

    assert result.theme == "light"
    assert result.font_size == 9
    assert result.alerts.thresholds == every_window((80, 95))


# ---------------------------------------------------------------------------
# Requirement: Predefined Themes Only
# ---------------------------------------------------------------------------


def test_exactly_two_themes_are_offered():
    assert settings.THEME_NAMES == ("dark", "light")


@pytest.mark.parametrize("name,palette", [("dark", DARK), ("light", LIGHT)])
def test_each_theme_name_maps_to_its_palette(name, palette):
    assert settings.build_theme(settings.Settings(theme=name)).palette is palette


@pytest.mark.parametrize("bogus", ["purple", "DARK", "", None, 7, {"bg": "#fff"}])
def test_an_unknown_theme_name_falls_back_to_dark(bogus):
    # Degrading to a working default beats quarantining a config file whose
    # profile list is perfectly valid.
    assert settings.from_raw({"theme": bogus}).theme == "dark"


# ---------------------------------------------------------------------------
# Requirement: Font Size Setting
# ---------------------------------------------------------------------------


def test_font_size_drives_theme_base_size_and_every_role_scales():
    theme = settings.build_theme(settings.Settings(font_size=12))

    assert theme.base_size == 12
    assert theme.font("small") == ("Segoe UI", 11)
    assert theme.font("body") == ("Segoe UI", 12)
    assert theme.font("heading") == ("Segoe UI", 13, "bold")
    assert theme.font("title") == ("Segoe UI", 14, "bold")


def test_font_size_persists_and_reloads():
    raw = settings.to_raw(settings.Settings(font_size=13))

    assert settings.from_raw(raw).font_size == 13


@pytest.mark.parametrize(
    "value,expected",
    [
        (7, 7),
        (16, 16),
        (3, settings.MIN_FONT_SIZE),  # clamped, not rejected
        (99, settings.MAX_FONT_SIZE),
        (11.0, 11),
    ],
)
def test_font_size_is_clamped_into_the_supported_range(value, expected):
    assert settings.from_raw({"font_size": value}).font_size == expected


@pytest.mark.parametrize("bogus", ["big", None, True, [9]])
def test_a_non_numeric_font_size_falls_back_to_the_default(bogus):
    assert settings.from_raw({"font_size": bogus}).font_size == settings.DEFAULT_FONT_SIZE


# ---------------------------------------------------------------------------
# Requirement: Window Size Setting
# ---------------------------------------------------------------------------


def test_window_size_round_trips_as_a_wxh_string():
    result = settings.from_raw({"window_size": "640x480"})

    assert result.window_size == "640x480"
    assert settings.parse_window_size(result.window_size) == (640, 480)


def test_format_window_size_is_the_inverse_of_parse_window_size():
    assert settings.format_window_size(640, 480) == "640x480"
    assert settings.parse_window_size(settings.format_window_size(640, 480)) == (640, 480)


@pytest.mark.parametrize(
    "bogus",
    ["", "nope", "640", "640x", "x480", "640x480x2", "-640x480", "0x0", "640X480", None, 640, ["640x480"]],
)
def test_an_unusable_window_size_is_discarded_rather_than_applied(bogus):
    # A bad geometry string would otherwise reach Tk's `geometry()` and
    # raise at render time -- a seam no Linux test can reach, so it is
    # rejected here, where a test can.
    assert settings.parse_window_size(bogus) is None
    assert settings.from_raw({"window_size": bogus}).window_size is None


@pytest.mark.parametrize("absurd", ["1x1", "99999x99999"])
def test_an_out_of_bounds_window_size_is_discarded(absurd):
    assert settings.parse_window_size(absurd) is None


# ---------------------------------------------------------------------------
# Alert settings (schema only -- this slice ships no UI for them)
# ---------------------------------------------------------------------------


def test_alert_settings_round_trip():
    original = settings.Settings(alerts=settings.AlertSettings(enabled=False, thresholds=every_window((50, 90))))

    assert settings.from_raw(settings.to_raw(original)) == original


@pytest.mark.parametrize("bogus", ["yes", None, 1, []])
def test_a_non_boolean_alerts_enabled_falls_back_to_the_default(bogus):
    assert settings.from_raw({"alerts": {"enabled": bogus}}).alerts.enabled is True


def test_thresholds_are_sorted_and_deduplicated():
    assert settings.from_raw({"alerts": {"thresholds": [95, 80, 95]}}).alerts.thresholds == every_window((80, 95))


def test_out_of_range_thresholds_are_dropped():
    result = settings.from_raw({"alerts": {"thresholds": [0, 80, 101, 95, -5]}})

    assert result.alerts.thresholds == every_window((80, 95))


@pytest.mark.parametrize("bogus", ["80,95", None, {"a": 1}, [], ["high"], [0]])
def test_thresholds_that_yield_no_usable_tier_fall_back_to_the_default(bogus):
    # An empty tier list with `enabled: true` is a silently dead feature that
    # still claims to be on; the default is the honest reading.
    assert settings.from_raw({"alerts": {"thresholds": bogus}}).alerts.thresholds == every_window(
        settings.DEFAULT_THRESHOLDS
    )


def test_an_alerts_key_that_is_not_an_object_falls_back_to_defaults():
    assert settings.from_raw({"alerts": "on"}).alerts == settings.AlertSettings()


# ---------------------------------------------------------------------------
# Change: repeat-alert cooldown (min_interval_minutes)
# ---------------------------------------------------------------------------


def test_min_interval_defaults_to_60():
    assert settings.Settings().alerts.min_interval_minutes == 60
    assert settings.from_raw({"alerts": {"enabled": True}}).alerts.min_interval_minutes == 60


def test_min_interval_clamps_into_range():
    assert settings.from_raw({"alerts": {"min_interval_minutes": 5000}}).alerts.min_interval_minutes == 1440
    assert settings.from_raw({"alerts": {"min_interval_minutes": -10}}).alerts.min_interval_minutes == 0
    assert settings.from_raw({"alerts": {"min_interval_minutes": 30}}).alerts.min_interval_minutes == 30


def test_min_interval_degrades_on_a_bad_value():
    # A mistyped value is degraded to the default, never fatal -- the same
    # tolerance policy every other settings value follows.
    assert settings.from_raw({"alerts": {"min_interval_minutes": "nope"}}).alerts.min_interval_minutes == 60
    assert settings.from_raw({"alerts": {"min_interval_minutes": True}}).alerts.min_interval_minutes == 60


def test_with_alert_interval_minutes_clamps():
    base = settings.Settings()
    assert settings.with_alert_interval_minutes(base, 45).alerts.min_interval_minutes == 45
    assert settings.with_alert_interval_minutes(base, 99999).alerts.min_interval_minutes == 1440
    assert settings.with_alert_interval_minutes(base, 0).alerts.min_interval_minutes == 0


def test_with_alert_interval_minutes_leaves_other_alert_fields_untouched():
    base = settings.from_raw({"alerts": {"enabled": False, "thresholds": [70, 90]}})
    changed = settings.with_alert_interval_minutes(base, 120)
    assert changed.alerts.min_interval_minutes == 120
    assert changed.alerts.enabled is False
    assert changed.alerts.thresholds == every_window((70, 90))


def test_min_interval_round_trips_through_raw():
    original = settings.with_alert_interval_minutes(settings.Settings(), 240)
    assert settings.from_raw(settings.to_raw(original)) == original


# ---------------------------------------------------------------------------
# Requirement: Settings Live in config.json  (serialization shape)
# ---------------------------------------------------------------------------


def test_to_raw_emits_exactly_the_documented_schema():
    raw = settings.to_raw(settings.Settings())

    assert raw == {
        "theme": "dark",
        "font_size": 9,
        "window_size": None,
        "colors": {},
        "alerts": {
            "enabled": True,
            "min_interval_minutes": 60,
            "thresholds": {
                "session": [80, 95],
                "weekly": [80, 95],
                "weekly_fable": [80, 95],
            },
            "profiles": {},
        },
    }


def test_to_raw_is_json_serializable_with_no_tuples_left():
    import json

    raw = settings.to_raw(settings.Settings(window_size="640x480"))
    reloaded = json.loads(json.dumps(raw))

    assert settings.from_raw(reloaded) == settings.Settings(window_size="640x480")


def test_round_trip_is_stable_so_re_saving_never_drifts():
    # app.py re-serializes settings on every profile edit; if the round trip
    # were lossy, an unrelated profile edit would silently rewrite settings.
    original = settings.Settings(theme="light", font_size=14, window_size="800x600")
    once = settings.to_raw(original)
    twice = settings.to_raw(settings.from_raw(once))

    assert once == twice
    assert settings.from_raw(twice) == original


def test_settings_are_immutable():
    with pytest.raises(Exception):
        settings.Settings().theme = "light"  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.AlertSettings().enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Requirement: Custom Hex Palettes
# ---------------------------------------------------------------------------


def test_custom_colors_are_applied_on_top_of_the_preset():
    result = settings.build_theme(settings.Settings(theme="dark", colors={"accent": "#00ff00"}))

    assert result.palette.accent == "#00ff00"
    assert result.palette.bg == DARK.bg  # untouched fields still track the preset


def test_the_preset_is_the_starting_point_not_a_third_theme():
    # The same override on either preset keeps that preset's other eight
    # fields -- which is what "dark/light remain presets" has to mean.
    dark = settings.build_theme(settings.Settings(theme="dark", colors={"accent": "#00ff00"}))
    light = settings.build_theme(settings.Settings(theme="light", colors={"accent": "#00ff00"}))

    assert dark.palette.bg == DARK.bg
    assert light.palette.bg == LIGHT.bg
    assert dark.palette.accent == light.palette.accent == "#00ff00"


def test_no_colors_reproduces_the_preset_exactly():
    assert settings.build_theme(settings.Settings(theme="dark")).palette is DARK
    assert settings.build_theme(settings.Settings(theme="light")).palette is LIGHT


def test_colors_persist_and_survive_a_restart():
    original = settings.Settings(colors={"accent": "#00ff00", "bg": "#101010"})
    reloaded = settings.from_raw(json.loads(json.dumps(settings.to_raw(original))))

    assert reloaded.colors == {"accent": "#00ff00", "bg": "#101010"}
    assert settings.build_theme(reloaded).palette.accent == "#00ff00"


def test_colors_are_normalized_on_the_way_in():
    assert settings.from_raw({"colors": {"accent": "#ABC"}}).colors == {"accent": "#aabbcc"}


def test_every_palette_field_is_overridable():
    raw = {"colors": {name: "#123456" for name in PALETTE_FIELDS}}
    result = settings.from_raw(raw)

    assert set(result.colors) == set(PALETTE_FIELDS)
    palette = settings.build_theme(result).palette
    assert {getattr(palette, name) for name in PALETTE_FIELDS} == {"#123456"}


# --- Migration: a config written before custom colors existed ---------------


def test_an_existing_theme_only_config_keeps_working_untouched():
    # The exact shape every pre-existing config.json holds.
    result = settings.load({"theme": "light", "font_size": 9, "window_size": None,
                            "alerts": {"enabled": True, "thresholds": [80, 95]}})

    assert result.notices == []
    assert result.settings.colors == {}
    assert settings.build_theme(result.settings).palette is LIGHT


@pytest.mark.parametrize("stored", [{"theme": "dark"}, {"theme": "light"}])
def test_the_pre_existing_theme_value_still_selects_its_palette(stored):
    # "theme": "dark|light" did not change meaning -- it became the preset a
    # (possibly empty) override set sits on.
    result = settings.from_raw(stored)
    expected = DARK if stored["theme"] == "dark" else LIGHT

    assert settings.build_theme(result).palette is expected


def test_an_absent_colors_key_is_not_a_notice():
    assert settings.load({"theme": "dark"}).notices == []


# --- Validation: a bad stored colour must not brick startup -----------------


@pytest.mark.parametrize("bogus", ["nope", "#12345", "red", "", None, 7, [], {"a": 1}, True])
def test_a_malformed_stored_color_falls_back_to_the_preset(bogus):
    result = settings.load({"colors": {"accent": bogus}})

    assert result.settings.colors == {}
    # Falls back rather than raising: an unusable colour must never be the
    # reason the app cannot start.
    assert settings.build_theme(result.settings).palette.accent == DARK.accent


def test_a_malformed_stored_color_is_reported_as_a_notice():
    result = settings.load({"colors": {"accent": "nope"}})

    assert result.notices == [settings.InvalidColors(fields=("accent",))]


def test_an_unknown_color_key_is_reported_rather_than_silently_ignored():
    # A "background" typo is the same user error as a bad hex, and silently
    # dropping it looks identical to the app being broken.
    result = settings.load({"colors": {"background": "#ffffff"}})

    assert result.settings.colors == {}
    assert result.notices == [settings.InvalidColors(fields=("background",))]


def test_valid_colors_survive_alongside_rejected_ones():
    result = settings.load({"colors": {"accent": "#00ff00", "bg": "zzz", "fg": "#abc"}})

    assert result.settings.colors == {"accent": "#00ff00", "fg": "#aabbcc"}
    assert result.notices == [settings.InvalidColors(fields=("bg",))]


def test_rejected_fields_are_reported_in_a_stable_order():
    # The notice is rendered into a dialog; a set's iteration order would make
    # the same corruption read differently run to run.
    result = settings.load({"colors": {"fg": "x", "bg": "y", "accent": "z"}})

    assert result.notices == [settings.InvalidColors(fields=("accent", "bg", "fg"))]


@pytest.mark.parametrize("bogus", ["#fff", None, 7, ["accent"]])
def test_a_colors_key_that_is_not_an_object_falls_back_to_no_overrides(bogus):
    result = settings.load({"colors": bogus})

    assert result.settings.colors == {}
    assert result.notices == []


def test_from_raw_is_load_without_the_notices():
    raw = {"colors": {"accent": "#00ff00", "bg": "nope"}}

    assert settings.from_raw(raw) == settings.load(raw).settings


# --- Mutators ---------------------------------------------------------------


def test_with_colors_replaces_rather_than_merges():
    # The picker submits every field it knows about, so a merge would make a
    # cleared field impossible to express.
    original = settings.Settings(colors={"accent": "#00ff00", "bg": "#101010"})

    assert settings.with_colors(original, {"accent": "#00ff00"}).colors == {"accent": "#00ff00"}


def test_with_colors_drops_invalid_entries():
    assert settings.with_colors(settings.Settings(), {"accent": "nope"}).colors == {}


def test_without_colors_returns_to_the_preset():
    original = settings.Settings(theme="light", colors={"accent": "#00ff00"})
    result = settings.without_colors(original)

    assert result.colors == {}
    assert result.theme == "light", "resetting colours must not also reset the preset"
    assert settings.build_theme(result).palette is LIGHT


def test_switching_preset_keeps_custom_colors():
    # Overrides sit on top of a preset, so previewing the other preset must
    # not silently discard work. `without_colors` is the way to drop them.
    original = settings.Settings(theme="dark", colors={"accent": "#00ff00"})

    assert settings.with_theme(original, "light").colors == {"accent": "#00ff00"}


def test_preset_palette_reports_the_un_overridden_preset():
    assert settings.preset_palette(settings.Settings(theme="light", colors={"bg": "#000000"})) is LIGHT


# ---------------------------------------------------------------------------
# Requirement: Per-Profile, Per-Window Thresholds With a Global Default
# ---------------------------------------------------------------------------


def test_a_profile_without_an_override_uses_the_global_default():
    alerts = settings.AlertSettings(thresholds=every_window((50, 90)))

    for window in QUOTA_WINDOWS:
        assert alerts.thresholds_for("p1", window) == (50, 90)
        assert alerts.has_override("p1", window) is False


def test_a_profile_with_an_override_uses_its_own_tiers():
    alerts = settings.AlertSettings(
        thresholds=every_window((50, 90)), profiles={"p1": every_window((75,))}
    )

    assert alerts.thresholds_for("p1", "session") == (75,)
    assert alerts.thresholds_for("p2", "session") == (50, 90), "p2 must be unaffected by p1's override"
    assert alerts.has_override("p1", "session") is True


def test_an_override_applies_only_to_the_window_it_names():
    # The point of the per-window split: the state machine has always keyed on
    # (profile, window); until now the config could only speak in profiles.
    alerts = settings.AlertSettings(
        thresholds=every_window((80, 95)), profiles={"p1": {"session": (50,)}}
    )

    assert alerts.thresholds_for("p1", "session") == (50,)
    assert alerts.thresholds_for("p1", "weekly") == (80, 95), "must not leak across windows"
    assert alerts.thresholds_for("p1", "weekly_fable") == (80, 95)
    assert alerts.has_override("p1", "session") is True
    assert alerts.has_override("p1", "weekly") is False


def test_different_windows_of_one_profile_can_hold_different_tiers():
    alerts = settings.AlertSettings(
        thresholds=every_window((80, 95)),
        profiles={"p1": {"session": (50,), "weekly_fable": (30, 60)}},
    )

    assert alerts.thresholds_for("p1", "session") == (50,)
    assert alerts.thresholds_for("p1", "weekly") == (80, 95)
    assert alerts.thresholds_for("p1", "weekly_fable") == (30, 60)


def test_the_global_default_can_differ_per_window():
    alerts = settings.AlertSettings(
        thresholds={"session": (50,), "weekly": (80, 95), "weekly_fable": (99,)}
    )

    assert alerts.thresholds_for("p1", "session") == (50,)
    assert alerts.thresholds_for("p1", "weekly") == (80, 95)
    assert alerts.thresholds_for("p1", "weekly_fable") == (99,)


def test_per_profile_thresholds_round_trip_through_json():
    original = settings.Settings(
        alerts=settings.AlertSettings(
            thresholds={"session": (50,), "weekly": (80,), "weekly_fable": (90,)},
            profiles={"p1": {"session": (80, 95)}, "p2": every_window((99,))},
        )
    )
    reloaded = settings.from_raw(json.loads(json.dumps(settings.to_raw(original))))

    assert reloaded == original


def test_per_profile_overrides_are_sorted_and_deduplicated_like_the_global_list():
    result = settings.from_raw({"alerts": {"profiles": {"p1": {"session": [95, 80, 95]}}}})

    assert result.alerts.profiles["p1"]["session"] == (80, 95)


def test_out_of_range_tiers_are_dropped_from_an_override():
    result = settings.from_raw({"alerts": {"profiles": {"p1": {"session": [0, 80, 101, -5]}}}})

    assert result.alerts.profiles["p1"]["session"] == (80,)


@pytest.mark.parametrize("bogus", ["80,95", None, {"a": 1}, [], ["high"], [0], True])
def test_an_unusable_override_is_dropped_so_the_profile_inherits_the_global_list(bogus):
    # NOT defaulted to [80, 95]: that would silently pin this one profile to
    # the shipped default while every other profile used the user's tiers.
    result = settings.from_raw({"alerts": {"thresholds": [50], "profiles": {"p1": bogus}}})

    assert "p1" not in result.alerts.profiles
    assert result.alerts.thresholds_for("p1", "session") == (50,)


@pytest.mark.parametrize("bogus", ["80,95", None, [], ["high"], [0], True])
def test_an_unusable_single_window_override_is_dropped_so_that_window_inherits(bogus):
    result = settings.from_raw(
        {"alerts": {"thresholds": [50], "profiles": {"p1": {"session": bogus, "weekly": [70]}}}}
    )

    assert result.alerts.has_override("p1", "session") is False
    assert result.alerts.thresholds_for("p1", "session") == (50,)
    assert result.alerts.thresholds_for("p1", "weekly") == (70,), "a sibling window must survive"


@pytest.mark.parametrize("bogus", ["nope", None, 7, [1, 2]])
def test_a_profiles_key_that_is_not_an_object_yields_no_overrides(bogus):
    assert settings.from_raw({"alerts": {"profiles": bogus}}).alerts.profiles == {}


@pytest.mark.parametrize("bogus_key", ["", 7, None])
def test_an_override_keyed_by_a_non_profile_id_is_dropped(bogus_key):
    result = settings.from_raw({"alerts": {"profiles": {bogus_key: [80]}}})

    assert result.alerts.profiles == {}


# --- Migration: a config written before per-window thresholds existed --------
#
# Requirement: A Bare Tier List Means "Every Window". These are the tests that
# stand between a real user's real config.json and a silent reset. There is no
# migration *step* to test, and that is the point: a bare list is re-read, not
# rewritten.


def test_an_existing_global_thresholds_config_keeps_its_exact_meaning():
    # The whole reason the global default survives: this config predates
    # per-profile tiers, and every profile must keep behaving identically.
    result = settings.from_raw({"alerts": {"enabled": True, "thresholds": [80, 95]}})

    assert result.alerts.profiles == {}
    for window in QUOTA_WINDOWS:
        assert result.alerts.thresholds_for("any-profile-at-all", window) == (80, 95)


def test_the_users_actual_config_keeps_its_75_in_every_window():
    """The config in the wild right now: General = 75, two profiles.

    A bare `[75]` meant "75 for all three windows", because one list governed
    all three. It has to keep meaning exactly that -- no loss, no reset to the
    shipped [80, 95] that the user never chose.
    """
    result = settings.from_raw(
        {"theme": "dark", "font_size": 9, "alerts": {"enabled": True, "thresholds": [75]}}
    )

    for profile_id in ("pc-profile-id", "wayni-profile-id"):
        for window in QUOTA_WINDOWS:
            assert result.alerts.thresholds_for(profile_id, window) == (75,)
    assert result.alerts.enabled is True
    assert result.alerts.profiles == {}


def test_the_users_actual_config_survives_a_save_and_reload_unchanged():
    """The round trip that matters: the rewrite must not change behaviour.

    A save rewrites `[75]` in the per-window form. That is a change of
    spelling, and it must be *only* that: reloading what was written has to
    answer 75 for every window, exactly as the bare list did.
    """
    loaded = settings.from_raw({"alerts": {"enabled": True, "thresholds": [75]}})

    raw = settings.to_raw(loaded)
    assert raw["alerts"]["thresholds"] == {
        "session": [75],
        "weekly": [75],
        "weekly_fable": [75],
    }, "written in the per-window form"

    reloaded = settings.from_raw(json.loads(json.dumps(raw)))

    assert reloaded == loaded, "the rewrite is a spelling change, not a behaviour change"
    for window in QUOTA_WINDOWS:
        assert reloaded.alerts.thresholds_for("any-profile", window) == (75,)

    # And it is stable: a second save/reload is a fixed point, not a drift.
    assert settings.from_raw(settings.to_raw(reloaded)) == reloaded


def test_an_existing_bare_list_override_keeps_meaning_all_three_windows():
    # A per-profile override written before per-window thresholds existed
    # applied to all three of that profile's windows. It still must.
    result = settings.from_raw(
        {"alerts": {"thresholds": [80, 95], "profiles": {"p1": [50]}}}
    )

    for window in QUOTA_WINDOWS:
        assert result.alerts.thresholds_for("p1", window) == (50,)
        assert result.alerts.has_override("p1", window) is True
    assert result.alerts.thresholds_for("p2", "session") == (80, 95)


def test_a_bare_list_override_round_trips_into_the_per_window_form():
    loaded = settings.from_raw({"alerts": {"thresholds": [75], "profiles": {"p1": [50]}}})
    reloaded = settings.from_raw(json.loads(json.dumps(settings.to_raw(loaded))))

    assert reloaded == loaded
    for window in QUOTA_WINDOWS:
        assert reloaded.alerts.thresholds_for("p1", window) == (50,)


def test_an_absent_profiles_key_is_not_an_error():
    result = settings.from_raw({"alerts": {"thresholds": [70]}})

    for window in QUOTA_WINDOWS:
        assert result.alerts.thresholds_for("p1", window) == (70,)


# --- Mutators ---------------------------------------------------------------


def test_with_profile_thresholds_sets_an_override_for_one_window_only():
    result = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])

    assert result.alerts.thresholds_for("p1", "session") == (70,)
    assert result.alerts.thresholds_for("p1", "weekly") == settings.DEFAULT_THRESHOLDS
    assert result.alerts.thresholds_for("p2", "session") == settings.DEFAULT_THRESHOLDS


def test_with_profile_thresholds_ignores_a_window_that_is_not_one():
    original = settings.Settings()

    assert settings.with_profile_thresholds(original, "p1", "monthly", [70]) == original


@pytest.mark.parametrize("cleared", [None, [], ["nope"], [0]])
def test_with_profile_thresholds_removes_the_override_when_given_nothing_usable(cleared):
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])
    result = settings.with_profile_thresholds(original, "p1", "session", cleared)

    assert result.alerts.has_override("p1", "session") is False
    assert result.alerts.thresholds_for("p1", "session") == settings.DEFAULT_THRESHOLDS


def test_clearing_one_window_leaves_its_siblings_overridden():
    original = settings.with_profile_thresholds(
        settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70]),
        "p1",
        "weekly",
        [60],
    )
    result = settings.with_profile_thresholds(original, "p1", "session", None)

    assert result.alerts.has_override("p1", "session") is False
    assert result.alerts.thresholds_for("p1", "weekly") == (60,)


def test_a_profile_left_with_no_overridden_window_is_dropped_entirely():
    # So "has no overrides" has exactly one spelling, rather than two that
    # prune/to_raw/has_override would each have to handle.
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])
    result = settings.with_profile_thresholds(original, "p1", "session", None)

    assert result.alerts.profiles == {}


def test_with_profile_thresholds_does_not_mutate_the_original():
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])
    settings.with_profile_thresholds(original, "p2", "session", [90])
    settings.with_profile_thresholds(original, "p1", "weekly", [90])

    assert set(original.alerts.profiles) == {"p1"}
    assert set(original.alerts.profiles["p1"]) == {"session"}, "the inner map must not be mutated either"


def test_with_thresholds_changes_only_the_named_window_of_the_global_default():
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])
    result = settings.with_thresholds(original, "session", [50])

    assert result.alerts.thresholds["session"] == (50,)
    assert result.alerts.thresholds["weekly"] == settings.DEFAULT_THRESHOLDS
    assert result.alerts.thresholds_for("p1", "session") == (70,), "an override must survive a global edit"
    assert result.alerts.thresholds_for("p2", "session") == (50,)


def test_with_thresholds_ignores_a_window_that_is_not_one():
    original = settings.Settings()

    assert settings.with_thresholds(original, "monthly", [50]) == original


def test_with_thresholds_falls_back_to_the_default_rather_than_emptying_a_window():
    # A window with no tiers and alerts enabled is silently dead.
    result = settings.with_thresholds(settings.Settings(), "session", ["nope"])

    assert result.alerts.thresholds["session"] == settings.DEFAULT_THRESHOLDS


def test_with_alerts_enabled_leaves_every_tier_alone():
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])
    result = settings.with_alerts_enabled(original, False)

    assert result.alerts.enabled is False
    assert result.alerts.thresholds_for("p1", "session") == (70,)


def test_prune_drops_overrides_for_profiles_that_no_longer_exist():
    original = settings.with_profile_thresholds(
        settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70]),
        "p2",
        "weekly",
        [90],
    )
    result = settings.prune_profile_thresholds(original, {"p1"})

    assert set(result.alerts.profiles) == {"p1"}
    assert set(original.alerts.profiles) == {"p1", "p2"}, "prune must not mutate its argument"


def test_prune_is_identity_when_every_profile_is_known():
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])

    assert settings.prune_profile_thresholds(original, {"p1", "p2"}) == original


def test_prune_with_no_known_profiles_drops_every_override():
    original = settings.with_profile_thresholds(settings.Settings(), "p1", "session", [70])

    assert settings.prune_profile_thresholds(original, set()).alerts.profiles == {}


# ---------------------------------------------------------------------------
# Threshold text parsing (the settings window's entry fields)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("80, 95", (80, 95)),
        ("80,95", (80, 95)),
        ("  80 , 95  ", (80, 95)),
        ("95, 80", (80, 95)),  # sorted
        ("80, 80, 95", (80, 95)),  # deduplicated
        ("80", (80,)),
        ("80%, 95%", (80, 95)),  # a typed % is the obvious thing to write
        ("80; 95", (80, 95)),
        ("80, 95,", (80, 95)),  # trailing separator
        ("1, 100", (1, 100)),
    ],
)
def test_parse_thresholds_text_accepts_what_a_user_would_plausibly_type(text, expected):
    assert settings.parse_thresholds_text(text) == expected


@pytest.mark.parametrize(
    "bogus",
    ["", "   ", "abc", "80, abc", "8.5", "80.0", ",", "0", "101", "0, 101", "-5", None, 80, [80]],
)
def test_parse_thresholds_text_reports_anything_unusable_as_none(bogus):
    # None, never (): the caller has to be able to tell "nothing usable" from
    # a valid empty edit, because at a per-profile row blank means "inherit".
    assert settings.parse_thresholds_text(bogus) is None


def test_parse_thresholds_text_reports_a_typo_rather_than_dropping_it():
    # "80, 9x" must not quietly become (80,) -- the user would never learn
    # that half of what they typed did not take.
    assert settings.parse_thresholds_text("80, 9x") is None


def test_format_thresholds_is_the_inverse_of_parse_thresholds_text():
    assert settings.format_thresholds((80, 95)) == "80, 95"
    assert settings.parse_thresholds_text(settings.format_thresholds((80, 95))) == (80, 95)


def test_format_thresholds_of_the_default_round_trips():
    text = settings.format_thresholds(settings.DEFAULT_THRESHOLDS)

    assert settings.parse_thresholds_text(text) == settings.DEFAULT_THRESHOLDS
