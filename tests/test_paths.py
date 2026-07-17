"""Unit tests for claude_usage_tray.paths — pure path composition."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_usage_tray import paths


def test_app_data_dir_composes_platform_root_and_app_dirname(monkeypatch):
    monkeypatch.setattr("claude_usage_tray.platform.app_data_root", lambda: Path("/fake/root"))
    assert paths.app_data_dir() == Path("/fake/root/ClaudeUsage")


def test_config_file_lives_under_app_data_dir(monkeypatch):
    monkeypatch.setattr("claude_usage_tray.platform.app_data_root", lambda: Path("/fake/root"))
    assert paths.config_file() == Path("/fake/root/ClaudeUsage/config.json")


def test_alert_state_file_lives_under_app_data_dir(monkeypatch):
    monkeypatch.setattr("claude_usage_tray.platform.app_data_root", lambda: Path("/fake/root"))
    assert paths.alert_state_file() == Path("/fake/root/ClaudeUsage/alert-state.json")


def test_resource_root_is_repo_root_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = Path(paths.__file__).resolve().parent.parent
    assert paths.resource_root() == expected


def test_resource_root_is_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_root() == tmp_path


def test_app_icon_under_resource_root_assets(monkeypatch):
    monkeypatch.setattr(paths, "resource_root", lambda: Path("/fake/resources"))
    assert paths.app_icon() == Path("/fake/resources/assets/claude_usage_icon.ico")


@pytest.mark.parametrize(
    "make_target,expect_original",
    [
        pytest.param(lambda tmp_path: tmp_path, True, id="existing-dir-returned-as-is"),
        pytest.param(lambda tmp_path: tmp_path / "missing-child", False, id="missing-child-falls-back-to-parent"),
    ],
)
def test_nearest_existing_dir_table(tmp_path, make_target, expect_original):
    target = make_target(tmp_path)
    result = paths.nearest_existing_dir(target)
    if expect_original:
        assert result == target
    else:
        assert result == target.parent


def test_nearest_existing_dir_falls_back_to_home_at_filesystem_root(monkeypatch):
    # Force the "doesn't exist" branch even for "/", which always exists on
    # a real filesystem, so we can exercise the root-has-no-parent fallback.
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/fake/home")))
    assert paths.nearest_existing_dir(Path("/")) == Path("/fake/home")
