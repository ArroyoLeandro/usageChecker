"""Where the OAuth token comes from, per platform.

Claude Code does not store its credentials the same way everywhere: on
Windows and Linux it writes `.credentials.json` inside the config dir, on
macOS it puts the identical JSON blob in the login Keychain and writes no
file at all. `providers._claude._read_credentials_blob` is the one place that
reconciles the two, and these tests pin the precedence rules it encodes.

The rule that needed the most pinning is which Keychain entry answers for
which directory. The entry is not one global secret: Claude Code names it
after the config directory, appending a short digest of the path to the base
service name -- with its own `~/.claude` as the sole exception, which keeps
the unsuffixed name. Both halves matter. Miss the suffix and every profile
but the home one reports "no session"; apply the suffix to everything and the
home profile, the one that works today, breaks instead.

The reconciliation lives in the adapter, not in `api.py`, because the
Keychain entry is *Claude Code's*: it carries Claude Code's service name and
no other provider has one. Placing it in the provider-agnostic layer would
also have left half the bug alive -- `read_access_token` would have found the
token while `usage_request`, which reads the credentials again to build the
Authorization header, kept looking for a file macOS never wrote. The last two
tests here are the ones that pin that second half, for both kinds of profile.

`platform.read_secret` is monkeypatched throughout, so the suite asserts the
same behaviour on every OS instead of only on a Mac with a live Keychain.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_usage_tray import api
from claude_usage_tray.config import ClaudeProfile
from claude_usage_tray.providers import _claude

TOKEN = "sk-ant-oat01-from-the-file"
HOME_TOKEN = "sk-ant-oat01-from-the-home-entry"
SECOND_TOKEN = "sk-ant-oat01-from-the-second-entry"


def _blob(token: str) -> str:
    return json.dumps({"claudeAiOauth": {"accessToken": token}})


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Relocate `~` into the test's own directory.

    Lets the home-default rule run for real -- against a path this test owns
    -- instead of being stubbed out, which is the only way to catch a rule
    that suffixes the home entry too. `CLAUDE_CONFIG_DIR` is cleared so the
    ambient directory cannot drift with whatever launched pytest.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def no_keychain(monkeypatch):
    """Default stance: the platform has no credential store (Windows/Linux)."""
    monkeypatch.setattr(_claude.platform, "read_secret", lambda service: None)


@pytest.fixture
def keychain(monkeypatch):
    """A macOS-shaped Keychain: a service -> blob store, plus the lookup log.

    The log is half the point. "Answered with the right token" and "never even
    asked for another account's entry" are different guarantees, and only the
    second one rules out a fallback that happens to be shadowed today.
    """
    store: dict[str, str] = {}
    asked: list[str] = []

    def read_secret(service: str) -> str | None:
        asked.append(service)
        return store.get(service)

    monkeypatch.setattr(_claude.platform, "read_secret", read_secret)
    return SimpleNamespace(store=store, asked=asked)


def _profile(config_dir) -> ClaudeProfile:
    return ClaudeProfile(id="p1", name="Test", config_dir=Path(config_dir))


def _qualified(config_dir: Path) -> str:
    """The suffixed service name for `config_dir`, taken from the adapter.

    Reading it back rather than recomputing it keeps these tests about
    behaviour; the digest recipe itself is pinned once, literally, in
    `test_the_qualified_service_name_is_the_path_digest`.
    """
    services = _claude._keychain_services(config_dir)
    return services[0]


def test_reads_the_token_from_the_credentials_file(tmp_path, no_keychain):
    (tmp_path / ".credentials.json").write_text(_blob(TOKEN), encoding="utf-8")

    assert api.read_access_token(_profile(tmp_path)) == TOKEN


def test_returns_none_when_there_is_no_file_and_no_store(tmp_path, no_keychain):
    assert api.read_access_token(_profile(tmp_path)) is None


def test_the_qualified_service_name_is_the_path_digest():
    # Pinned against a literal so a change to the recipe -- digest length,
    # separator, hashing the resolved path instead of the given one -- fails
    # here rather than silently reading nobody's entry on a real Mac.
    services = _claude._keychain_services(Path("/Users/example/.claude-work"))

    assert services == ["Claude Code-credentials-dd1118a7"]


def test_the_home_default_is_the_only_directory_offered_the_unsuffixed_entry(home):
    home_services = _claude._keychain_services(home / ".claude")
    other_services = _claude._keychain_services(home / ".claude-work")

    assert home_services[-1] == _claude.KEYCHAIN_SERVICE
    assert _claude.KEYCHAIN_SERVICE not in other_services


def test_falls_back_to_the_unsuffixed_keychain_entry_for_the_home_default(home, keychain):
    # The macOS case for the machine's own account: Claude Code left no file
    # anywhere, and its entry for `~/.claude` carries no digest suffix.
    config_dir = home / ".claude"
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)

    assert api.read_access_token(_profile(config_dir)) == HOME_TOKEN


def test_a_second_profile_reads_its_own_keychain_entry(home, keychain):
    # The bug this rule exists to fix: a profile pointed at a second Claude
    # directory used to be refused the store outright and showed "no session"
    # forever, even with a perfectly good entry sitting under its own name.
    config_dir = home / ".claude-work"
    keychain.store[_qualified(config_dir)] = _blob(SECOND_TOKEN)

    assert api.read_access_token(_profile(config_dir)) == SECOND_TOKEN


def test_a_second_profile_is_never_answered_by_the_unsuffixed_entry(home, keychain):
    # The guardrail on the fix. A directory nobody ever logged in from has no
    # entry, and must read as "no session" rather than borrowing the home
    # account's -- two popup rows showing one account's usage under different
    # names is wrong data presented confidently, which is worse than no data.
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)

    assert api.read_access_token(_profile(home / ".claude-never-used")) is None
    assert _claude.KEYCHAIN_SERVICE not in keychain.asked


def test_the_home_default_prefers_its_qualified_entry_when_both_exist(home, keychain):
    # Order, not just membership. If Claude Code ever starts suffixing the
    # home entry too, the digest names one directory and the bare name names
    # a default that can move, so the specific one has to win.
    config_dir = home / ".claude"
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)
    keychain.store[_qualified(config_dir)] = _blob(SECOND_TOKEN)

    assert api.read_access_token(_profile(config_dir)) == SECOND_TOKEN


def test_the_file_wins_over_the_keychain(home, keychain):
    # A profile pointed at a real directory gets that directory's token, even
    # on macOS -- e.g. a mounted WSL `.claude` still works.
    config_dir = home / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_blob(TOKEN), encoding="utf-8")
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)

    assert api.read_access_token(_profile(config_dir)) == TOKEN
    assert keychain.asked == [], "no reason to consult the store when a file answered"


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


def test_an_unreadable_file_does_not_fall_through_to_the_keychain(home, monkeypatch, keychain):
    # A file that exists but cannot be read is a real fault, not "no session
    # here, try the store": answering with the home account's token would
    # attribute one profile's usage to another.
    config_dir = home / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(_blob(TOKEN), encoding="utf-8")
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", boom)

    assert api.read_access_token(_profile(config_dir)) is None
    assert keychain.asked == []


def test_the_usage_request_is_built_from_the_keychain_token_too(home, keychain):
    """The half that a fix confined to `read_access_token` would have missed.

    Nothing fetches usage from the token `read_access_token` returns -- the
    adapter reads the credentials a second time to build the Authorization
    header. On macOS, where the file does not exist, an adapter that only knew
    about files would report a live session and then never manage a request:
    the tray would show "signed in" and no numbers, forever.
    """
    config_dir = home / ".claude"
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)

    request = _claude.PROVIDER.usage_request(config_dir)

    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {HOME_TOKEN}"


def test_the_usage_request_for_a_second_profile_uses_its_own_entry(home, keychain):
    # The same second half, for the profile that used to be locked out
    # entirely. Both entries are present so a request built from the wrong one
    # would still succeed -- and still be wrong.
    config_dir = home / ".claude-work"
    keychain.store[_claude.KEYCHAIN_SERVICE] = _blob(HOME_TOKEN)
    keychain.store[_qualified(config_dir)] = _blob(SECOND_TOKEN)

    request = _claude.PROVIDER.usage_request(config_dir)

    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {SECOND_TOKEN}"
