"""Where the OAuth token comes from, per platform.

Claude Code does not store its credentials the same way everywhere: on
Windows and Linux it writes `.credentials.json` inside the config dir, on
macOS it puts the identical JSON blob in the login Keychain and writes no
file at all. `providers._claude._read_credentials_blob` is the one place that
reconciles the two, and these tests pin the precedence rules it encodes --
especially the one that keeps a second macOS profile from silently reporting
the default account's usage.

The reconciliation lives in the adapter, not in `api.py`, because the
Keychain entry is *Claude Code's*: it carries Claude Code's fixed service
name and no other provider has one. Placing it in the provider-agnostic layer
would also have left half the bug alive -- `read_access_token` would have
found the token while `usage_request`, which reads the credentials again to
build the Authorization header, kept looking for a file macOS never wrote.
The last test here is the one that pins that second half.

`platform.read_secret` is monkeypatched throughout, so the suite asserts the
same behaviour on every OS instead of only on a Mac with a live Keychain.
"""

from __future__ import annotations

import json

import pytest

from claude_usage_tray import api
from claude_usage_tray.config import ClaudeProfile, default_claude_dir
from claude_usage_tray.providers import _claude

TOKEN = "sk-ant-oat01-from-the-file"
KEYCHAIN_TOKEN = "sk-ant-oat01-from-the-keychain"


def _blob(token: str) -> str:
    return json.dumps({"claudeAiOauth": {"accessToken": token}})


@pytest.fixture
def no_keychain(monkeypatch):
    """Default stance: the platform has no credential store (Windows/Linux)."""
    monkeypatch.setattr(_claude.platform, "read_secret", lambda service: None)


@pytest.fixture
def keychain(monkeypatch):
    """A macOS-shaped Keychain holding Claude Code's entry."""
    seen = []

    def read_secret(service: str) -> str | None:
        seen.append(service)
        return _blob(KEYCHAIN_TOKEN) if service == _claude.KEYCHAIN_SERVICE else None

    monkeypatch.setattr(_claude.platform, "read_secret", read_secret)
    return seen


@pytest.fixture
def ambient(monkeypatch):
    """Treat whatever directory a test uses as the machine's own `.claude`."""
    monkeypatch.setattr(_claude, "_is_ambient", lambda config_dir: True)


@pytest.fixture
def not_ambient(monkeypatch):
    """Treat every directory as a second account's, never the machine's own."""
    monkeypatch.setattr(_claude, "_is_ambient", lambda config_dir: False)


def _profile(config_dir) -> ClaudeProfile:
    return ClaudeProfile(id="p1", name="Test", config_dir=config_dir)


def _ambient_profile() -> ClaudeProfile:
    return ClaudeProfile(id="p0", name="Ambient", config_dir=default_claude_dir())


def test_reads_the_token_from_the_credentials_file(tmp_path, no_keychain):
    (tmp_path / ".credentials.json").write_text(_blob(TOKEN), encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) == TOKEN


def test_returns_none_when_there_is_no_file_and_no_store(tmp_path, no_keychain):
    assert api.read_access_token(_profile(tmp_path)) is None


def test_falls_back_to_the_keychain_for_the_ambient_profile(ambient, keychain):
    # The macOS case: Claude Code left no file anywhere, so the only copy of
    # the token is the Keychain entry.
    assert api.read_access_token(_ambient_profile()) == KEYCHAIN_TOKEN
    assert keychain == [_claude.KEYCHAIN_SERVICE]


def test_does_not_read_the_keychain_for_a_non_ambient_profile(tmp_path, not_ambient, keychain):
    # The rule that matters: the Keychain entry is not qualified by config
    # dir, so it can only speak for the profile that *is* the machine's
    # default. Letting a second profile fall back to it would make both rows
    # of the popup show the same account's usage under different names --
    # wrong data presented confidently, which is worse than "no session".
    assert api.read_access_token(_profile(tmp_path)) is None
    assert keychain == [], "the Keychain must not even be consulted"


def test_the_file_wins_over_the_keychain(tmp_path, ambient, keychain):
    # A profile pointed at a real directory gets that directory's token, even
    # on macOS -- e.g. a mounted WSL `.claude` still works.
    (tmp_path / ".credentials.json").write_text(_blob(TOKEN), encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) == TOKEN
    assert keychain == [], "no reason to consult the store when a file answered"


def test_malformed_json_is_not_a_crash(tmp_path, no_keychain):
    (tmp_path / ".credentials.json").write_text("{not json", encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) is None


def test_json_that_is_not_an_object_is_not_a_crash(tmp_path, no_keychain):
    # A bare list would blow up on `.get` if the type were assumed.
    (tmp_path / ".credentials.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) is None


def test_a_blob_without_an_access_token_reads_as_no_session(tmp_path, no_keychain):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r"}}), encoding="utf-8"
    )

    assert api.read_access_token(_profile(tmp_path)) is None


def test_an_empty_access_token_reads_as_no_session(tmp_path, no_keychain):
    (tmp_path / ".credentials.json").write_text(_blob(""), encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) is None


def test_an_unreadable_file_does_not_fall_through_to_the_keychain(
    tmp_path, monkeypatch, ambient, keychain
):
    # A file that exists but cannot be read is a real fault, not "no session
    # here, try the store": answering with the ambient account's token would
    # attribute one profile's usage to another.
    creds = tmp_path / ".credentials.json"
    creds.write_text(_blob(TOKEN), encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", boom)

    assert api.read_access_token(_profile(tmp_path)) is None
    assert keychain == []


def test_the_usage_request_is_built_from_the_keychain_token_too(ambient, keychain):
    """The half that a fix confined to `read_access_token` would have missed.

    Nothing fetches usage from the token `read_access_token` returns -- the
    adapter reads the credentials a second time to build the Authorization
    header. On macOS, where the file does not exist, an adapter that only knew
    about files would report a live session and then never manage a request:
    the tray would show "signed in" and no numbers, forever.
    """
    request = _claude.PROVIDER.usage_request(default_claude_dir())

    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {KEYCHAIN_TOKEN}"
