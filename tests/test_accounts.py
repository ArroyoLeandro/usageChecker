"""Which account the MCP server reports on, and why.

The precedence chain is the whole feature: a server launched by a Claude
running on a non-default config directory has to answer for *that* account
without being told. Every case below is a way that can silently go wrong and
report a stranger's quota with total confidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_usage_tray.accounts import (
    INHERITED_ENV,
    OVERRIDE_ENV,
    PROFILE_ID,
    profile_for,
    resolve_config_dir,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No ambient config dir unless a test asks for one.

    The dev box running these tests is itself inside Claude Code, which
    exports `CLAUDE_CONFIG_DIR` -- so without this, the fallback cases would
    pass or fail depending on who ran them and from where.
    """
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    monkeypatch.delenv(INHERITED_ENV, raising=False)


def test_falls_back_to_the_stock_directory_when_nothing_is_set(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    resolution = resolve_config_dir()

    assert resolution.config_dir == tmp_path / ".claude"
    assert resolution.source == "default"


def test_inherits_the_launching_claude_config_dir(monkeypatch):
    monkeypatch.setenv(INHERITED_ENV, "/home/someone/.claude-personal")

    resolution = resolve_config_dir()

    assert resolution.config_dir == Path("/home/someone/.claude-personal")
    assert resolution.source == INHERITED_ENV


def test_pin_beats_the_inherited_directory(monkeypatch):
    """The reason `CLAUDE_USAGE_CONFIG_DIR` exists at all: a server entry
    that must report on one named account no matter who launched it."""
    monkeypatch.setenv(INHERITED_ENV, "/home/someone/.claude")
    monkeypatch.setenv(OVERRIDE_ENV, "/home/someone/.claude-work")

    resolution = resolve_config_dir()

    assert resolution.config_dir == Path("/home/someone/.claude-work")
    assert resolution.source == OVERRIDE_ENV


def test_explicit_argument_beats_every_environment_variable(monkeypatch):
    monkeypatch.setenv(INHERITED_ENV, "/home/someone/.claude")
    monkeypatch.setenv(OVERRIDE_ENV, "/home/someone/.claude-work")

    resolution = resolve_config_dir("/home/someone/.claude-other")

    assert resolution.config_dir == Path("/home/someone/.claude-other")
    assert resolution.source == "argument"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_argument_is_not_a_choice(monkeypatch, blank):
    """An absent `config_dir` arrives as `None`, but a client that fills the
    field with an empty string must not thereby resolve to the process's
    working directory or to `Path('')`."""
    monkeypatch.setenv(INHERITED_ENV, "/home/someone/.claude-personal")

    resolution = resolve_config_dir(blank if blank is None else blank.strip() or None)

    assert resolution.config_dir == Path("/home/someone/.claude-personal")


def test_a_path_to_the_credentials_file_resolves_to_its_directory():
    """Same affordance the profiles window has: the credentials file is the
    thing a user can actually find on disk, so pasting it is the likely
    mistake and is worth absorbing rather than failing on."""
    resolution = resolve_config_dir("/home/someone/.claude-personal/.credentials.json")

    assert resolution.config_dir == Path("/home/someone/.claude-personal")


def test_tilde_is_expanded(monkeypatch, tmp_path):
    # `Path.expanduser` resolves `~` through `$HOME`, not `Path.home()` --
    # unlike `config.default_claude_dir`, which calls `Path.home()` directly.
    # The two are patched differently on purpose; conflating them is how this
    # test passed against the wrong directory the first time it was written.
    monkeypatch.setenv("HOME", str(tmp_path))

    resolution = resolve_config_dir("~/.claude-personal")

    assert resolution.config_dir == tmp_path / ".claude-personal"


def test_exists_reports_the_directory_not_the_credentials_file(tmp_path):
    present = tmp_path / "present"
    present.mkdir()

    assert resolve_config_dir(str(present)).exists is True
    assert resolve_config_dir(str(tmp_path / "absent")).exists is False


def test_profile_is_ephemeral_and_stably_keyed(tmp_path):
    """`api.py` caches and backs off per profile. Handing it a fresh uuid4
    on every call would give each call its own cache and its own 429 state,
    which is the opposite of what a polling caller needs."""
    resolution = resolve_config_dir(str(tmp_path / ".claude-personal"))

    first = profile_for(resolution)
    second = profile_for(resolution)

    assert first.id == second.id == PROFILE_ID
    assert first.config_dir == tmp_path / ".claude-personal"
    assert first.credentials_path == tmp_path / ".claude-personal" / ".credentials.json"
    assert first.name == ".claude-personal"
