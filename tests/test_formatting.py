"""Characterization tests for claude_usage_tray.formatting.

These assertions were originally written and run against the pure helpers
while they still lived in `claude_usage_tray.app` (tasks.md 4.1), *before*
`formatting.py` existed -- every golden value was captured by actually
executing the then-current `app.py` code (see design.md's
characterize-then-move sequence), not guessed. Task 4.3 re-points the same
assertion bodies at the new module (only the import target, the
monkeypatch path, and the now-unprefixed public names changed -- e.g.
`_pct` -> `pct` -- since the design's public surface for `formatting.py`
drops the leading underscore), proving the move is behavior-preserving.

Time-dependent helpers are exercised behind a frozen clock: a `datetime`
subclass whose `now()` is patched into the module under test, so results
never depend on wall-clock time or the day the suite happens to run.
Task 4.4 adds a further, additive three-timezone table that exercises the
new `now=`/`tz=` injection directly, with no monkeypatch at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from claude_usage_tray.config import ClaudeProfile
from claude_usage_tray.formatting import (
    DAY_NAMES,
    TOOLTIP_BUDGET,
    availability_pct,
    availability_pct_from_bundle,
    bar_color,
    hover_sections,
    parse_meta_datetime,
    pct,
    reset_label,
    reset_text,
    tooltip,
    tray_level,
    updated_text,
)
from claude_usage_tray.theme import DARK, LIGHT

# Fixed instant used to freeze `datetime.now()` for every time-dependent
# test below. Chosen so the local (America/Argentina/Buenos_Aires, UTC-3)
# wall-clock time is safely mid-day: no test here should ever cross a local
# midnight boundary and become flaky.
FIXED_NOW_UTC = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    """`datetime` subclass whose `now()` always returns `FIXED_NOW_UTC`.

    Installed in place of the real `datetime` class inside
    `claude_usage_tray.formatting` so the *default* (`now=None`) path
    through `reset_text`/`reset_label`/`updated_text` -- which then call
    `datetime.now()` internally -- becomes deterministic without `TZ` +
    `time.tzset()` (rejected by design.md: `tzset` is Unix-only, absent on
    the Windows target). This class exists only to characterize that
    default path; the timezone table below exercises the injected-`now`/
    `tz` path directly, with no monkeypatch, which is how production tests
    (and, later, real call sites that need determinism) are meant to use
    these functions.
    """

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return FIXED_NOW_UTC.astimezone(tz)
        return FIXED_NOW_UTC.astimezone().replace(tzinfo=None)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr("claude_usage_tray.formatting.datetime", _FrozenDateTime)


# ---------------------------------------------------------------------------
# pct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry,expected",
    [
        (None, None),
        ({}, None),
        ({"utilization": 12}, 12.0),
        ({"utilization": "not-a-number"}, None),
        ({"utilization": None}, None),
    ],
)
def test_pct(entry, expected):
    assert pct(entry) == expected


# ---------------------------------------------------------------------------
# bar_color
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected_hex",
    [
        (95, "#ef4444"),
        (90, "#ef4444"),
        (75, "#f59e0b"),
        (70, "#f59e0b"),
        (10, "#d97757"),
    ],
)
def test_bar_color(value, expected_hex):
    assert bar_color(value) == expected_hex


@pytest.mark.parametrize("value", [95, 90, 75, 70, 10, 0, 100])
def test_bar_color_without_a_palette_still_matches_the_dark_preset(value):
    # The no-palette path is the fallback, and it must stay exactly what the
    # characterization above pinned -- which is DARK's three colours.
    assert bar_color(value) == bar_color(value, DARK)


@pytest.mark.parametrize(
    "value,field",
    [(95, "danger"), (90, "danger"), (75, "warn"), (70, "warn"), (10, "accent"), (0, "accent")],
)
@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_bar_color_reads_the_palette_it_is_given(value, field, palette):
    # Same thresholds, different source: the bands are a formatting decision,
    # the colours are the palette's.
    assert bar_color(value, palette) == getattr(palette, field)


def test_bar_color_follows_a_custom_palette():
    # The point of threading the palette through: a user's own accent must
    # reach the app's most prominent element.
    from claude_usage_tray.theme import with_overrides

    custom = with_overrides(DARK, {"accent": "#00ff00", "warn": "#0000ff", "danger": "#ff00ff"})

    assert bar_color(10, custom) == "#00ff00"
    assert bar_color(75, custom) == "#0000ff"
    assert bar_color(95, custom) == "#ff00ff"


def test_bar_color_bands_differ_between_the_two_presets():
    # Regression: for as long as bar_color ignored the palette, bars rendered
    # identically in both themes and the light preset was only half-themed.
    assert bar_color(75, LIGHT) != bar_color(75, DARK)
    assert bar_color(95, LIGHT) != bar_color(95, DARK)


# ---------------------------------------------------------------------------
# availability_pct / availability_pct_from_bundle / tray_level
# ---------------------------------------------------------------------------


def test_availability_pct_takes_the_minimum_across_windows():
    data = {"session": {"utilization": 10}, "weekly": {"utilization": 40}}
    assert availability_pct(data) == 60.0


def test_availability_pct_none_when_no_usage_data():
    assert availability_pct({}) is None


def test_availability_pct_from_bundle_single_profile():
    bundle = {"primary": {"session": {"utilization": 10}}}
    assert availability_pct_from_bundle(bundle) == 90.0


def test_availability_pct_from_bundle_multi_profile_takes_the_minimum():
    bundle = {
        "profiles": [
            {"data": {"session": {"utilization": 10}}},
            {"data": {"session": {"utilization": 60}}},
        ]
    }
    assert availability_pct_from_bundle(bundle) == 40.0


def test_availability_pct_from_bundle_none_when_no_data():
    assert availability_pct_from_bundle({"primary": {}}) is None


@pytest.mark.parametrize(
    "availability,expected_level",
    [(None, 0), (85, 4), (60, 3), (25, 2), (5, 1)],
)
def test_tray_level(availability, expected_level):
    assert tray_level(availability) == expected_level


# ---------------------------------------------------------------------------
# tooltip (golden strings)
# ---------------------------------------------------------------------------


def test_tooltip_multi_profile():
    profile = ClaudeProfile(id="a", name="Casa", config_dir=Path("/x"))
    bundle = {
        "profiles": [
            {"profile": profile, "data": {"session": {"utilization": 12}, "weekly": {"utilization": 62}}},
            {"profile": None, "data": {"error": "sin datos"}},
        ]
    }
    assert tooltip(bundle) == "Claude — uso\nCasa: 5h 12% · 7d 62%\nPerfil: sin datos"


def test_tooltip_single_profile_with_warning():
    bundle = {
        "primary": {
            "session": {"utilization": 10},
            "weekly": {"utilization": 20},
            "weekly_fable": {"utilization": 30},
            "meta": {"warning": "Aviso!"},
        }
    }
    assert tooltip(bundle) == (
        "Claude — uso\nMostrando la ultima informacion disponible\n5h: 10%\n7d: 20%\nFable: 30%\nAviso!"
    )


def test_tooltip_single_profile_error_only():
    bundle = {"primary": {"error": "Sin conexion"}}
    assert tooltip(bundle) == "Claude uso\nSin conexion"


# ---------------------------------------------------------------------------
# tray-tooltip spec (slice e), native fallback path.
#
# The three goldens above are unchanged on purpose: they are the fixtures the
# move was characterized against, and the fixes below are additive for them.
# Everything here is new behavior the spec requires and the old code did not
# have -- a budget, multi-profile parity, and an explicit omission marker.
# ---------------------------------------------------------------------------


def _profiles_bundle(count: int, *, name: str = "Perfil", warning: bool = False, fable: bool = True) -> dict:
    entries = []
    for index in range(count):
        data = {"session": {"utilization": 88}, "weekly": {"utilization": 71}}
        if fable:
            data["weekly_fable"] = {"utilization": 34}
        if warning:
            data["meta"] = {"warning": "Mostrando datos en cache"}
        entries.append(
            {
                "profile": ClaudeProfile(id=str(index), name=f"{name}{index}", config_dir=Path("/x")),
                "data": data,
            }
        )
    return {"profiles": entries}


# --- Requirement: Hard Character Budget ------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 10, 25])
def test_tooltip_never_exceeds_the_budget_however_many_profiles_exist(count):
    assert len(tooltip(_profiles_bundle(count))) <= TOOLTIP_BUDGET


@pytest.mark.parametrize("count", [1, 2, 6, 25])
def test_tooltip_never_exceeds_the_budget_with_long_names_and_warnings(count):
    bundle = _profiles_bundle(count, name="Cuenta de trabajo muy larga ", warning=True)

    assert len(tooltip(bundle)) <= TOOLTIP_BUDGET


def test_tooltip_reduces_content_that_would_otherwise_overflow():
    # The spec's own scenario: content that would be far past the budget in
    # full is reduced deterministically, never handed to the OS to cut.
    bundle = _profiles_bundle(6, warning=True)
    full_length = sum(len(item["profile"].name) + 24 for item in bundle["profiles"])

    assert full_length > TOOLTIP_BUDGET
    assert len(tooltip(bundle)) <= TOOLTIP_BUDGET


def test_tooltip_budget_holds_for_an_absurd_single_profile_warning():
    bundle = {
        "primary": {
            "session": {"utilization": 100},
            "weekly": {"utilization": 100},
            "weekly_fable": {"utilization": 100},
            "meta": {"warning": "A" * 400},
        }
    }
    assert len(tooltip(bundle)) <= TOOLTIP_BUDGET


def test_tooltip_budget_holds_for_an_absurd_single_profile_error():
    assert len(tooltip({"primary": {"error": "E" * 500}})) <= TOOLTIP_BUDGET


def test_tooltip_budget_holds_for_an_absurd_profile_name():
    bundle = _profiles_bundle(1, name="N" * 300)

    assert len(tooltip(bundle)) <= TOOLTIP_BUDGET


# --- Requirement: Multi-Profile Content Parity ------------------------------


def test_multi_profile_content_includes_weekly_fable():
    # Regression: the multi-profile branch used to omit weekly_fable
    # entirely, so a Fable quota at 99% was invisible unless you opened the
    # popup.
    text = tooltip(_profiles_bundle(2))

    assert "34%" in text, f"weekly_fable dropped from the multi-profile branch: {text!r}"


def test_multi_profile_content_includes_every_profiles_warning():
    # Regression: the multi-profile branch used to omit the rate-limit
    # warning entirely.
    text = tooltip(_profiles_bundle(2, warning=True))

    assert text.count("⚠") == 2, f"a profile's warning was dropped: {text!r}"


def test_a_profile_with_only_fable_data_is_not_reported_as_having_none():
    bundle = {"profiles": [{"profile": None, "data": {"weekly_fable": {"utilization": 42}}}]}

    assert "42%" in tooltip(bundle)


def test_single_and_multi_profile_branches_report_the_same_fields():
    single = tooltip({"primary": {"session": {"utilization": 88}, "weekly": {"utilization": 71},
                                  "weekly_fable": {"utilization": 34}}})
    multi = tooltip(_profiles_bundle(1))

    for value in ("88", "71", "34"):
        assert value in single and value in multi, f"{value} missing from one branch"


# --- Requirement: Warnings Take Priority Over Precision ---------------------


def test_budget_pressure_drops_fable_before_a_warning():
    # Enough profiles that the full form cannot fit, each with a warning.
    text = tooltip(_profiles_bundle(4, name="Perfil bastante largo ", warning=True))

    assert len(text) <= TOOLTIP_BUDGET
    assert "⚠" in text, f"a warning was surrendered before the numbers were: {text!r}"


def test_a_warning_survives_when_a_single_profiles_numbers_must_go():
    bundle = {
        "primary": {
            "session": {"utilization": 100},
            "weekly": {"utilization": 100},
            "weekly_fable": {"utilization": 100},
            "meta": {"warning": "Limite alcanzado, reintentando mas tarde con datos guardados"},
        }
    }
    text = tooltip(bundle)

    assert len(text) <= TOOLTIP_BUDGET
    assert "Limite alcanzado" in text
    assert "Fable: 100%" not in text, "the tertiary quota must be surrendered before the warning"


# --- Requirement: Explicit Truncation When Profile Count Exceeds Capacity ----


def test_more_profiles_than_fit_are_marked_never_silently_dropped():
    # Regression: the old code did `profiles[:4]` unconditionally, with no
    # signal at all that other profiles existed.
    text = tooltip(_profiles_bundle(12, name="Cuenta numero "))

    assert len(text) <= TOOLTIP_BUDGET
    assert "mas" in text, f"profiles were dropped with no marker: {text!r}"


def test_the_omission_marker_counts_exactly_what_was_left_out():
    import re

    bundle = _profiles_bundle(12, name="Cuenta numero ")
    text = tooltip(bundle)
    match = re.search(r"\+(\d+) mas", text)

    assert match, f"no marker in {text!r}"
    shown = len(text.splitlines()) - 2  # minus the header and the marker line
    assert int(match.group(1)) == 12 - shown


def test_four_profiles_are_no_longer_a_silent_ceiling():
    # The old ceiling was exactly 4; a 5th profile vanished without trace.
    text = tooltip(_profiles_bundle(5, name="P"))

    assert "P4" in text or "mas" in text, f"the 5th profile vanished silently: {text!r}"


def test_no_marker_appears_when_every_profile_fits():
    assert "mas" not in tooltip(_profiles_bundle(2, name="P"))


# ---------------------------------------------------------------------------
# hover_sections -- the custom popup's content (no budget)
# ---------------------------------------------------------------------------


def test_hover_sections_has_no_four_profile_ceiling():
    # The whole point of the custom popup: nothing here is rationed.
    sections = hover_sections(_profiles_bundle(9))

    assert len(sections) == 9


def test_hover_sections_carries_every_quota_window():
    sections = hover_sections(_profiles_bundle(1))

    assert [key for key, _ in sections[0].quotas] == ["session", "weekly", "weekly_fable"]
    assert dict(sections[0].quotas) == {"session": 88.0, "weekly": 71.0, "weekly_fable": 34.0}


def test_hover_sections_omits_a_quota_the_poll_did_not_report():
    sections = hover_sections(_profiles_bundle(1, fable=False))

    assert [key for key, _ in sections[0].quotas] == ["session", "weekly"]


def test_hover_sections_carries_each_profiles_warning():
    sections = hover_sections(_profiles_bundle(2, warning=True))

    assert all(section.warning == "Mostrando datos en cache" for section in sections)


def test_hover_sections_uses_the_profile_name_as_its_title():
    sections = hover_sections(_profiles_bundle(2, name="Casa"))

    assert [section.title for section in sections] == ["Casa0", "Casa1"]


def test_hover_sections_names_an_unnamed_profile_by_position():
    bundle = {"profiles": [{"profile": None, "data": {"session": {"utilization": 5}}}]}

    assert hover_sections(bundle)[0].title == "Perfil 1"


def test_hover_sections_reports_an_error_only_when_there_is_no_data():
    with_error = hover_sections({"profiles": [{"profile": None, "data": {"error": "Sin conexion"}}]})
    assert with_error[0].error == "Sin conexion"
    assert with_error[0].quotas == ()

    # A warning plus usable data is not an error state -- the popup must show
    # the numbers, not an error in place of them.
    with_data = hover_sections(_profiles_bundle(1, warning=True))
    assert with_data[0].error is None
    assert with_data[0].quotas


def test_hover_sections_falls_back_to_the_primary_bundle():
    sections = hover_sections({"primary": {"session": {"utilization": 12}}})

    assert len(sections) == 1
    assert sections[0].title == "Perfil"
    assert dict(sections[0].quotas) == {"session": 12.0}


def test_hover_sections_of_an_empty_bundle_does_not_raise():
    sections = hover_sections({"primary": {}})

    assert len(sections) == 1
    assert sections[0].quotas == ()


# ---------------------------------------------------------------------------
# reset_text / reset_label / updated_text / parse_meta_datetime
#
# All exercised with the frozen clock above (default now=None path).
# Expected clock/day-name fragments are computed with the exact same
# arithmetic the function under test uses (`reset.astimezone()`,
# `DAY_NAMES[reset.weekday()]`) rather than hardcoded, so the assertion
# holds on any machine timezone -- only the frozen "now" and the offsets
# from it are fixed.
# ---------------------------------------------------------------------------


def test_reset_text_multi_hour(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(hours=2, minutes=15)
    expected_clock = reset_dt.astimezone().strftime("%H:%M")
    assert reset_text(reset_dt.isoformat()) == f"en 2h 15m ({expected_clock})"


def test_reset_text_minutes_only(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(minutes=45)
    expected_clock = reset_dt.astimezone().strftime("%H:%M")
    assert reset_text(reset_dt.isoformat()) == f"en 45m ({expected_clock})"


def test_reset_text_past_is_pronto(frozen_clock):
    reset_dt = FIXED_NOW_UTC - timedelta(seconds=10)
    expected_clock = reset_dt.astimezone().strftime("%H:%M")
    assert reset_text(reset_dt.isoformat()) == f"pronto ({expected_clock})"


def test_reset_text_none_and_malformed():
    assert reset_text(None) == "—"
    assert reset_text("not-a-date") == "—"


def test_reset_label_same_day_under_a_minute(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(seconds=30)
    assert reset_label(reset_dt.isoformat()) == "Se restablece pronto"


def test_reset_label_same_day_minutes(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(minutes=25)
    assert reset_label(reset_dt.isoformat()) == "Se restablece en 25 min"


def test_reset_label_same_day_hours_and_minutes(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(hours=2, minutes=10)
    assert reset_label(reset_dt.isoformat()) == "Se restablece en 2 h 10 min"


def test_reset_label_same_day_whole_hours(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(hours=3)
    assert reset_label(reset_dt.isoformat()) == "Se restablece en 3 h"


def test_reset_label_different_day(frozen_clock):
    reset_dt = FIXED_NOW_UTC + timedelta(days=2, hours=2, minutes=10)
    reset_local = reset_dt.astimezone()
    expected_day = DAY_NAMES[reset_local.weekday()]
    expected_clock = reset_local.strftime("%H:%M")
    assert reset_label(reset_dt.isoformat()) == f"Se restablece {expected_day} {expected_clock} hs"


def test_reset_label_none_and_malformed():
    assert reset_label(None) == "Se restablece: —"
    assert reset_label("garbage") == "Se restablece: —"


def test_updated_text_under_a_minute(frozen_clock):
    local_now = FIXED_NOW_UTC.astimezone().replace(tzinfo=None)
    assert updated_text(local_now - timedelta(seconds=10)) == "Ultima actualizacion: hace menos de un minuto"


def test_updated_text_minutes(frozen_clock):
    local_now = FIXED_NOW_UTC.astimezone().replace(tzinfo=None)
    assert updated_text(local_now - timedelta(minutes=7)) == "Ultima actualizacion: hace 7 min"


def test_updated_text_none():
    assert updated_text(None) == "Ultima actualizacion: sin datos"


def test_parse_meta_datetime_fixed_input():
    value = "2026-07-16T18:00:00+00:00"
    expected = datetime.fromisoformat(value).astimezone().replace(tzinfo=None)
    assert parse_meta_datetime(value) == expected


def test_parse_meta_datetime_none_and_malformed():
    assert parse_meta_datetime(None) is None
    assert parse_meta_datetime("nope") is None


# ---------------------------------------------------------------------------
# Task 4.4: three-timezone table for reset_text / parse_meta_datetime.
#
# Demonstrates the design's claim verbatim: "Time-dependent output is made
# deterministic by injection, not by pinning TZ." No monkeypatch anywhere in
# this section -- `now`/`tz` are passed straight in, and `tz.tzset()` is
# never invoked (it is Unix-only and the suite must pass on Windows too).
# Fixed input: 2026-07-16T18:00:00+00:00, one hour fifty-seven minutes in
# the future of a fixed `now`.
# ---------------------------------------------------------------------------

_RESET_ISO = "2026-07-16T18:00:00+00:00"
_NOW_FOR_TABLE = datetime(2026, 7, 16, 16, 2, 30, tzinfo=timezone.utc)  # 1h57m before the reset


@pytest.mark.parametrize(
    "tz,expected_reset_text,expected_parsed",
    [
        pytest.param(
            timezone.utc,
            "en 1h 57m (18:00)",
            datetime(2026, 7, 16, 18, 0),
            id="UTC",
        ),
        pytest.param(
            ZoneInfo("America/Argentina/Buenos_Aires"),
            "en 1h 57m (15:00)",
            datetime(2026, 7, 16, 15, 0),
            id="America/Argentina/Buenos_Aires",
        ),
        pytest.param(
            ZoneInfo("Asia/Tokyo"),
            "en 1h 57m (03:00)",
            datetime(2026, 7, 17, 3, 0),
            id="Asia/Tokyo (date rolls)",
        ),
    ],
)
def test_reset_text_and_parse_meta_datetime_across_timezones(tz, expected_reset_text, expected_parsed):
    assert reset_text(_RESET_ISO, now=_NOW_FOR_TABLE, tz=tz) == expected_reset_text
    assert parse_meta_datetime(_RESET_ISO, tz=tz) == expected_parsed
