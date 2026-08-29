"""Shaping an `api.fetch_usage` payload into what a model reads.

The payloads below are the real shapes `api.py` produces -- a success, a
cached-but-stale rate-limited one, and an auth error -- because the failure
this suite exists to prevent is a caller reading `used_percent: null` as
"0% used, plenty left" and looping until the account is dry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from claude_usage_tray.mcp_server.report import (
    build_report,
    humanize_minutes,
    minutes_until,
)

NOW = datetime(2026, 8, 9, 23, 0, tzinfo=timezone.utc)


def _iso(**delta) -> str:
    return (NOW + timedelta(**delta)).isoformat()


def _success_payload() -> dict:
    return {
        "session": {"utilization": 25.0, "resets_at": _iso(hours=3, minutes=30)},
        "weekly": {"utilization": 66.0, "resets_at": _iso(days=4)},
        "weekly_fable": None,
        "raw": {
            "five_hour": {"utilization": 25.0, "resets_at": _iso(hours=3, minutes=30)},
            "seven_day": {"utilization": 66.0, "resets_at": _iso(days=4)},
            "seven_day_opus": {"utilization": 12.0, "resets_at": _iso(days=4)},
            "spend": {"amount": 0},
            "limits": [],
            "member_dashboard_available": True,
        },
        "meta": {"fetched_at": NOW.isoformat(), "stale": False, "rate_limited": False},
    }


def _report(payload: dict) -> dict:
    return build_report(payload, config_dir="/home/someone/.claude-personal", source="CLAUDE_CONFIG_DIR", now=NOW)


def test_reports_both_quota_windows_with_percent_and_reset():
    report = _report(_success_payload())

    session, weekly, fable = report["windows"]
    assert (session["id"], session["used_percent"], session["remaining_percent"]) == ("session", 25.0, 75.0)
    assert session["resets_in_minutes"] == 210
    assert session["resets_in"] == "3h 30m"
    assert (weekly["id"], weekly["used_percent"], weekly["remaining_percent"]) == ("weekly", 66.0, 34.0)
    assert weekly["resets_in"] == "4d"
    assert fable["available"] is False


def test_highest_used_percent_is_the_binding_constraint():
    """A session window at 25% says nothing about whether work can continue
    if the weekly window is at 66%. The max is the number a budget check has
    to read, so the report computes it rather than trusting every caller to
    remember which window binds."""
    report = _report(_success_payload())

    assert report["highest_used_percent"] == 66.0
    assert report["ok"] is True


def test_a_window_the_account_does_not_have_is_absent_not_zero():
    """The failure this whole module guards: `weekly_fable` is null on an
    account without it, and `used_percent: 0` would read as 'untouched'."""
    report = _report(_success_payload())

    fable = report["windows"][2]
    assert fable["available"] is False
    assert fable["used_percent"] is None
    assert fable["remaining_percent"] is None
    assert "Fable" not in report["summary"]


def test_summary_is_one_line_a_model_can_quote():
    assert _report(_success_payload())["summary"] == "5h 25% · 7d 66%"


def test_secondary_limits_are_carried_but_kept_out_of_the_windows():
    report = _report(_success_payload())

    assert report["other_limits"]["seven_day_opus"]["used_percent"] == 12.0
    # Not a limit: no utilization, so it must not become one with a null.
    assert "spend" not in report["other_limits"]
    assert "limits" not in report["other_limits"]
    assert [window["id"] for window in report["windows"]] == ["session", "weekly", "weekly_fable"]


def test_the_account_that_answered_is_always_reported():
    report = _report(_success_payload())

    assert report["account"] == {
        "config_dir": "/home/someone/.claude-personal",
        "source": "CLAUDE_CONFIG_DIR",
    }


def test_an_auth_error_is_an_answer_not_an_exception():
    report = _report({"error": "La sesion vencio.", "auth_error": True})

    assert report["ok"] is False
    assert report["auth_error"] is True
    assert report["error"] == "La sesion vencio."


def test_stale_cached_data_still_reports_its_numbers_and_says_it_is_stale():
    """`api.py` returns the last good payload with a warning when it is
    rate-limited. Dropping those numbers would leave a caller with nothing,
    which is strictly worse than slightly old figures it knows are old."""
    payload = _success_payload()
    payload["error"] = "Ya se alcanzo el limite por ahora."
    payload["meta"] = {"fetched_at": _iso(minutes=-20), "stale": True, "rate_limited": True, "from_cache": True}

    report = _report(payload)

    assert report["ok"] is True
    assert report["stale"] is True
    assert report["rate_limited"] is True
    assert report["highest_used_percent"] == 66.0
    assert report["warning"] == "Ya se alcanzo el limite por ahora."


def test_a_quota_already_past_its_reset_floors_at_zero_not_negative():
    payload = _success_payload()
    payload["session"]["resets_at"] = _iso(minutes=-5)

    session = _report(payload)["windows"][0]

    assert session["resets_in_minutes"] == 0
    assert session["resets_in"] == "any moment now"


def test_a_missing_reset_is_unknown_rather_than_imminent():
    """None and 0 must not collapse: 'the API did not say' and 'resets any
    second' lead a scheduler to opposite decisions."""
    assert minutes_until(None, now=NOW) is None
    assert minutes_until("not-a-timestamp", now=NOW) is None
    assert humanize_minutes(None) is None


def test_a_naive_reset_timestamp_is_read_as_utc():
    """Guards against a naive/aware subtraction raising `TypeError` deep in
    the report and turning a fetched payload into a protocol error."""
    naive = (NOW + timedelta(hours=2)).replace(tzinfo=None).isoformat()

    assert minutes_until(naive, now=NOW) == 120


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(0, "any moment now"), (7, "7m"), (60, "1h"), (95, "1h 35m"), (1440, "1d"), (1500, "1d 1h")],
)
def test_reset_durations_read_naturally(minutes, expected):
    assert humanize_minutes(minutes) == expected
