"""`set_usage_budget`: the tool that arms the gate from inside a session.

Validated here rather than trusted to the JSON schema, because an MCP client
is not obliged to enforce `minimum`/`maximum` — and a ceiling of 0 or 500
yields a gate that blocks everything or nothing while looking perfectly
configured from the outside.
"""

from __future__ import annotations

import pytest

from claude_usage_tray import budget as budget_module
from claude_usage_tray.budget import Budget
from claude_usage_tray.mcp_server import server


@pytest.fixture(autouse=True)
def session(monkeypatch):
    monkeypatch.setenv(budget_module.SESSION_ENV, "session-alpha")
    return "session-alpha"


def test_setting_a_ceiling_persists_it_for_the_hook(app_data_dir):
    result = server.set_budget(70, warn_margin=5, note="overnight")

    assert result["ok"] is True
    assert result["budget"]["ceiling_percent"] == 70
    assert result["budget"]["warn_at_percent"] == 65
    assert budget_module.load_budget() == Budget(70.0, 5.0, "overnight")


def test_the_default_margin_applies_when_only_a_ceiling_is_given(app_data_dir):
    result = server.set_budget(80)

    assert result["budget"]["warn_at_percent"] == 80 - budget_module.DEFAULT_WARN_MARGIN


def test_clearing_removes_the_ceiling(app_data_dir):
    server.set_budget(70)

    result = server.set_budget(clear=True)

    assert result["ok"] is True
    assert result["budget"] is None
    assert budget_module.load_budget() is None


def test_clearing_when_nothing_is_set_is_not_an_error(app_data_dir):
    result = server.set_budget(clear=True)

    assert result["ok"] is True
    assert result["cleared"] is False


def test_a_ceiling_outside_one_to_a_hundred_is_refused(app_data_dir):
    for bad in (0, -10, 101, 500):
        result = server.set_budget(bad)
        assert result["ok"] is False, f"{bad} should have been refused"
        assert budget_module.load_budget() is None


def test_a_non_numeric_ceiling_is_refused(app_data_dir):
    assert server.set_budget("seventy")["ok"] is False


def test_a_missing_ceiling_without_clear_is_refused(app_data_dir):
    result = server.set_budget()

    assert result["ok"] is False
    assert "required" in result["error"]


def test_both_tools_are_advertised():
    names = [tool["name"] for tool in server._handle_tools_list({})["tools"]]

    assert names == ["get_claude_usage", "set_usage_budget"]


def test_the_usage_report_carries_the_active_budget(app_data_dir, monkeypatch, tmp_path):
    """An agent that can see 66% but not the 70% cap has no way to know it
    is three points from being cut off."""
    config_dir = tmp_path / ".claude-personal"
    config_dir.mkdir()
    monkeypatch.setattr(
        server.api,
        "fetch_usage",
        lambda *_args, **_kwargs: {
            "session": {"utilization": 25.0},
            "weekly": {"utilization": 66.0},
            "weekly_fable": None,
            "raw": {},
            "meta": {"fetched_at": "2026-08-09T23:00:00+00:00"},
        },
    )
    server.set_budget(70, warn_margin=5)

    report = server.get_usage(str(config_dir))

    assert report["highest_used_percent"] == 66.0
    assert report["budget"]["ceiling_percent"] == 70
    assert report["budget_status"] == "warn"
    assert "USAGE BUDGET WARNING" in report["budget_message"]


def test_the_usage_report_omits_budget_status_when_none_is_set(app_data_dir, monkeypatch, tmp_path):
    config_dir = tmp_path / ".claude-personal"
    config_dir.mkdir()
    monkeypatch.setattr(
        server.api,
        "fetch_usage",
        lambda *_args, **_kwargs: {"session": {"utilization": 25.0}, "raw": {}, "meta": {}},
    )

    report = server.get_usage(str(config_dir))

    assert report["budget"] is None
    assert "budget_status" not in report
