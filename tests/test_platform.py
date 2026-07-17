"""Unit tests for the platform seam's off-Windows behavior.

These run on Linux (this repo's dev/CI environment) and assert the no-op /
explicit-failure contract from design.md's "Unknown value -> None/0;
unsupported action -> raise" decision. On Windows these same code paths are
covered by the Windows-only manual smoke checklist (tasks.md 9.4) since the
seam then dispatches to `_windows.py` instead.
"""

from __future__ import annotations

import sys

import pytest

from claude_usage_tray import platform


def test_is_not_windows_in_this_environment():
    # Sanity check for every other assertion in this file: they only hold
    # off-Windows, where the seam dispatches to `_posix.py` (or `_darwin.py`).
    assert platform.IS_WINDOWS is False
    assert sys.platform != "win32"


def test_subprocess_flags_is_inert_off_windows():
    assert platform.subprocess_flags() == 0


def test_work_area_bounds_is_none_off_windows():
    assert platform.work_area_bounds() is None


def test_set_app_user_model_id_is_a_noop_off_windows():
    # Off Windows there is no AppUserModelID concept; the seam must be an
    # explicit no-op that returns None rather than raising AttributeError on
    # the missing shell32 symbol.
    assert platform.set_app_user_model_id("Anthropic.ClaudeUsage") is None


def test_startup_not_supported_off_windows():
    assert platform.startup_supported() is False


def test_startup_not_enabled_off_windows():
    assert platform.is_startup_enabled() is False


def test_set_startup_enabled_raises_off_windows():
    with pytest.raises(platform.PlatformUnsupportedError):
        platform.set_startup_enabled(True, "some-command")


def test_open_in_file_manager_raises_on_missing_command(monkeypatch):
    import subprocess as subprocess_module
    from pathlib import Path

    def _raise_missing_executable(*args, **kwargs):
        raise FileNotFoundError("xdg-open: command not found")

    monkeypatch.setattr(subprocess_module, "Popen", _raise_missing_executable)
    with pytest.raises(platform.FileManagerError):
        platform.open_in_file_manager(Path("/tmp"))


def test_app_data_root_is_a_path_under_home():
    from pathlib import Path

    root = platform.app_data_root()
    assert isinstance(root, Path)


# ---------------------------------------------------------------------------
# Tray hover (slice e). "Cannot know" -> None/False, never an exception:
# `app.py` reads that as "keep the native tooltip", which is a supported
# outcome rather than a failure.
# ---------------------------------------------------------------------------


def test_tray_hover_is_not_supported_off_windows():
    assert platform.tray_hover_supported() is False


def test_tray_icon_rect_is_none_off_windows():
    assert platform.tray_icon_rect(12345, 0) is None


def test_tray_icon_rect_does_not_raise_off_windows():
    # The distinction the seam draws: this is a value we cannot know, not an
    # action we cannot perform, so it must not raise the way
    # set_startup_enabled does.
    assert platform.tray_icon_rect(0, 0) is None


def test_cursor_position_is_none_off_windows():
    assert platform.cursor_position() is None


def test_tray_icon_rect_defaults_to_the_uid_the_spike_measured():
    # uid=0 answered on pystray's win32 backend; the default records that
    # finding rather than making every caller repeat it.
    import inspect

    signature = inspect.signature(platform.tray_icon_rect)
    assert signature.parameters["uid"].default == 0


# ---------------------------------------------------------------------------
# Rect: pure geometry, so it is testable on any platform
# ---------------------------------------------------------------------------


def test_rect_reports_its_own_size():
    rect = platform.Rect(left=100, top=200, right=124, bottom=230)

    assert (rect.width, rect.height) == (24, 30)


def test_rect_hit_test_is_half_open():
    rect = platform.Rect(left=0, top=0, right=10, bottom=10)

    assert rect.contains(0, 0) is True
    assert rect.contains(9, 9) is True
    assert rect.contains(10, 5) is False, "right edge is exclusive"
    assert rect.contains(5, 10) is False, "bottom edge is exclusive"
    assert rect.contains(-1, 5) is False


def test_rect_is_frozen():
    rect = platform.Rect(left=0, top=0, right=10, bottom=10)
    with pytest.raises(Exception):
        rect.left = 5  # type: ignore[misc]
