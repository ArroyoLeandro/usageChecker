"""Unit tests for `claude_usage_tray.providers` -- the adapter registry.

Two kinds of test live here, and the split matters:

* **Contract tests** run against *every* registered adapter via
  `parametrize`. They are the thing that makes "adding a provider is one
  file plus one registry line" true rather than aspirational: a new adapter
  is held to the same normalization shape, the same tolerance for a missing
  or corrupt credentials file, and the same window-id vocabulary, without
  anyone remembering to write tests for it.
* **Per-adapter tests** pin the response shape each service actually
  returns, recorded from a real reply (values redacted).

No network: `usage_request` returns a description of a call, and
`normalize` is a pure function, so both halves are testable offline. That
separation is the whole reason the fetch itself lives in `api.py`.
"""

from __future__ import annotations

import json

import pytest

from claude_usage_tray import providers
from claude_usage_tray.quotas import QUOTA_WINDOWS

ALL = providers.all_providers()
PAYLOAD_KEYS = {"session", "weekly", "weekly_fable", "raw"}


# ---------------------------------------------------------------------------
# contract -- every adapter, present and future
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_identity_fields_are_present_and_non_empty(adapter):
    assert adapter.id and isinstance(adapter.id, str)
    assert adapter.display_name and isinstance(adapter.display_name, str)
    assert adapter.credentials_filename and isinstance(adapter.credentials_filename, str)


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_declared_windows_are_known_quota_windows(adapter):
    """An adapter may report a subset of the windows, never a new one.

    A window id outside `QUOTA_WINDOWS` would be silently dropped by the
    settings window (which renders in `QUOTA_WINDOWS` order) and would have
    no Spanish label, so `quota_label` would surface the raw id to the user.
    """
    assert adapter.windows, f"{adapter.id} declares no quota windows"
    assert set(adapter.windows) <= set(QUOTA_WINDOWS)


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_normalize_always_returns_the_shared_payload_shape(adapter):
    """Every consumer downstream indexes these four keys unconditionally."""
    result = adapter.normalize({})
    assert set(result) == PAYLOAD_KEYS


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
@pytest.mark.parametrize("garbage", [None, [], "nonsense", 7, {"unexpected": True}])
def test_normalize_survives_a_response_it_did_not_expect(adapter, garbage):
    """A provider changing its response shape must degrade to "no data",
    never raise: `api.fetch_usage` would turn an exception into the generic
    "No se pudo leer el uso" and lose every other profile's numbers in the
    same poll."""
    result = adapter.normalize(garbage)
    assert set(result) == PAYLOAD_KEYS
    assert all(result[window] is None for window in ("session", "weekly", "weekly_fable"))


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_normalize_only_fills_windows_it_declares(adapter):
    """`windows` must tell the truth: the settings window renders threshold
    fields from it, so a window filled but not declared would produce numbers
    the user cannot set an alert on."""
    result = adapter.normalize({})
    undeclared = set(QUOTA_WINDOWS) - set(adapter.windows)
    assert all(result[window] is None for window in undeclared)


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_no_session_means_no_request(adapter, tmp_path):
    """An empty directory is the signed-out case, and it must not produce a
    request with an empty bearer token -- that would spend a real API call to
    be told 401, and then trip the shared backoff for that profile."""
    assert adapter.usage_request(tmp_path) is None


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_corrupt_credentials_file_is_not_fatal(adapter, tmp_path):
    (tmp_path / adapter.credentials_filename).write_text("{not json", encoding="utf-8")
    assert adapter.usage_request(tmp_path) is None
    assert adapter.account_label(tmp_path) is None


@pytest.mark.parametrize("adapter", ALL, ids=[a.id for a in ALL])
def test_default_config_dir_is_absolute(adapter):
    assert adapter.default_config_dir().is_absolute()


def test_every_adapter_has_a_distinct_id_and_display_name():
    assert len({a.id for a in ALL}) == len(ALL)
    assert len({a.display_name for a in ALL}) == len(ALL)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_unknown_provider_degrades_to_the_default_instead_of_raising():
    """An exe reading a config written by a newer build must still start."""
    assert providers.get("opencode").id == providers.DEFAULT_ID
    assert providers.get(None).id == providers.DEFAULT_ID
    assert providers.get("").id == providers.DEFAULT_ID


def test_is_known_distinguishes_a_real_provider_from_a_substituted_one():
    assert providers.is_known("claude")
    assert not providers.is_known("opencode")
    assert not providers.is_known(None)


def test_default_is_claude_so_pre_provider_configs_keep_their_meaning():
    # Profiles written before SCHEMA_VERSION 3 carry no `provider` and are
    # migrated to this value; changing it would repoint every one of them.
    assert providers.DEFAULT_ID == "claude"


def test_credentials_path_is_provider_specific(tmp_path):
    assert providers.credentials_path("claude", tmp_path).name == ".credentials.json"
    assert providers.credentials_path("codex", tmp_path).name == "auth.json"


def test_detect_installed_reports_only_existing_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "here"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    (tmp_path / "here").mkdir()

    found = {adapter.id for adapter in providers.detect_installed()}
    assert found == {"claude"}


# ---------------------------------------------------------------------------
# Claude adapter
# ---------------------------------------------------------------------------


def _write_claude(tmp_path, **oauth):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth}), encoding="utf-8"
    )
    return tmp_path


def test_claude_maps_the_three_anthropic_fields():
    result = providers.get("claude").normalize(
        {
            "five_hour": {"utilization": 12.5, "resets_at": "2026-09-01T10:00:00+00:00"},
            "seven_day": {"utilization": 40, "resets_at": "2026-09-05T10:00:00+00:00"},
            "seven_day_fable": {"utilization": 3, "resets_at": "2026-09-05T10:00:00+00:00"},
        }
    )
    assert result["session"] == {"utilization": 12.5, "resets_at": "2026-09-01T10:00:00+00:00"}
    assert result["weekly"]["utilization"] == 40.0
    assert result["weekly_fable"]["utilization"] == 3.0


def test_claude_absent_fable_is_none_not_zero():
    """Zero would render a 0% Fable row for an account that has no Fable
    quota at all; `None` is what makes the row disappear."""
    result = providers.get("claude").normalize({"five_hour": {"utilization": 1}})
    assert result["weekly_fable"] is None


def test_claude_builds_an_authorized_request(tmp_path):
    _write_claude(tmp_path, accessToken="tok-123")
    request = providers.get("claude").usage_request(tmp_path)
    assert request is not None
    assert request.url.startswith("https://api.anthropic.com/")
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["anthropic-beta"] == "oauth-2025-04-20"


def test_claude_account_label_prefers_email(tmp_path):
    _write_claude(tmp_path, accessToken="t", email="user@example.com", username="fallback")
    assert providers.get("claude").account_label(tmp_path) == "user@example.com"


def test_claude_refresh_is_refused_for_a_directory_the_cli_does_not_own(tmp_path, monkeypatch):
    """Running `claude update` for a second account's folder would renew the
    *ambient* account's token and report success for the wrong profile."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ambient"))
    assert providers.get("claude").refresh_credentials(tmp_path / "somewhere-else") is False


# ---------------------------------------------------------------------------
# Codex adapter
# ---------------------------------------------------------------------------


def _write_codex(tmp_path, **tokens):
    (tmp_path / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": tokens}), encoding="utf-8"
    )
    return tmp_path


#: Shape recorded from a real `GET /backend-api/codex/usage` reply.
CODEX_USAGE = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {
            "used_percent": 18,
            "limit_window_seconds": 18000,
            "reset_at": 1788250575,
        },
        "secondary_window": {
            "used_percent": 50,
            "limit_window_seconds": 604800,
            "reset_at": 1788748143,
        },
    },
}


def test_codex_maps_primary_and_secondary_onto_session_and_weekly():
    """The correspondence that let Codex reuse the existing window ids:
    a 5-hour primary window is `session`, a 7-day secondary is `weekly`."""
    result = providers.get("codex").normalize(CODEX_USAGE)
    assert result["session"]["utilization"] == 18.0
    assert result["weekly"]["utilization"] == 50.0


def test_codex_has_no_fable_window():
    assert providers.get("codex").normalize(CODEX_USAGE)["weekly_fable"] is None


def test_codex_converts_epoch_resets_to_iso():
    """Codex reports resets as unix seconds; `formatting.reset_text` parses
    ISO-8601 with `datetime.fromisoformat`. Converting in the adapter is what
    keeps that difference out of the pure text layer."""
    from datetime import datetime

    result = providers.get("codex").normalize(CODEX_USAGE)
    parsed = datetime.fromisoformat(result["session"]["resets_at"])
    assert int(parsed.timestamp()) == 1788250575


def test_codex_builds_an_authorized_request_with_the_account_header(tmp_path):
    _write_codex(tmp_path, access_token="tok-abc", account_id="acct-1")
    request = providers.get("codex").usage_request(tmp_path)
    assert request is not None
    assert request.url == "https://chatgpt.com/backend-api/codex/usage"
    assert request.headers["Authorization"] == "Bearer tok-abc"
    assert request.headers["chatgpt-account-id"] == "acct-1"


def test_codex_request_omits_the_account_header_when_absent(tmp_path):
    _write_codex(tmp_path, access_token="tok-abc")
    request = providers.get("codex").usage_request(tmp_path)
    assert request is not None
    assert "chatgpt-account-id" not in request.headers


def test_codex_account_label_reads_the_id_token_email(tmp_path):
    import base64

    claims = base64.urlsafe_b64encode(json.dumps({"email": "who@example.com"}).encode()).decode()
    _write_codex(tmp_path, access_token="t", id_token=f"header.{claims.rstrip('=')}.sig")
    assert providers.get("codex").account_label(tmp_path) == "who@example.com"


def test_codex_account_label_survives_a_malformed_id_token(tmp_path):
    _write_codex(tmp_path, access_token="t", id_token="not-a-jwt")
    assert providers.get("codex").account_label(tmp_path) is None


def test_codex_refresh_without_a_refresh_token_fails_without_network(tmp_path):
    """Returns False before attempting any request -- so a profile that was
    never signed in cannot make the tray hang on a socket timeout."""
    _write_codex(tmp_path, access_token="t")
    assert providers.get("codex").refresh_credentials(tmp_path) is False
