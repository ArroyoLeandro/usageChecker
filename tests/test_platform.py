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


IS_DARWIN = sys.platform == "darwin"


@pytest.mark.skipif(IS_DARWIN, reason="macOS answers this one; see the test below")
def test_work_area_bounds_is_none_off_windows_and_macos():
    assert platform.work_area_bounds() is None


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSScreen.visibleFrame")
def test_work_area_bounds_is_a_sane_rect_on_macos():
    # macOS *can* answer -- `NSScreen.visibleFrame()` is the direct analogue
    # of `SPI_GETWORKAREA` -- so unlike Linux it must not return the "cannot
    # know" sentinel. The assertions stay shape-level on purpose: the numbers
    # depend on the screen, the menu bar and where the Dock is parked.
    area = platform.work_area_bounds()
    assert area is not None
    assert area.right > area.left
    assert area.bottom > area.top
    # The menu bar is never zero-height and is always reserved, so the usable
    # area can never start at the very top of the screen. This is the one
    # number that would be wrong if the Cocoa->Tk y-flip were dropped.
    assert area.top > 0


def test_set_app_user_model_id_is_a_noop_off_windows():
    # Off Windows there is no AppUserModelID concept; the seam must be an
    # explicit no-op that returns None rather than raising AttributeError on
    # the missing shell32 symbol.
    assert platform.set_app_user_model_id("Anthropic.ClaudeUsage") is None


@pytest.mark.skipif(IS_DARWIN, reason="macOS registers a LaunchAgent")
def test_startup_not_supported_on_linux():
    assert platform.startup_supported() is False


@pytest.mark.skipif(IS_DARWIN, reason="macOS registers a LaunchAgent")
def test_startup_not_enabled_on_linux():
    assert platform.is_startup_enabled() is False


@pytest.mark.skipif(IS_DARWIN, reason="macOS registers a LaunchAgent")
def test_set_startup_enabled_raises_on_linux():
    with pytest.raises(platform.PlatformUnsupportedError):
        platform.set_startup_enabled(True, "some-command")


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: LaunchAgents")
def test_startup_is_supported_on_macos():
    # `~/Library/LaunchAgents` is the documented per-user "open at login"
    # mechanism and needs no privileges, so unlike Linux this is a thing the
    # seam can actually do -- and `app.py` shows the menu entry off this.
    assert platform.startup_supported() is True


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: LaunchAgents")
def test_startup_state_round_trips_through_the_plist(monkeypatch, tmp_path):
    # Writes to a temp path and never touches the real LaunchAgents dir, and
    # stubs `launchctl` so the test asserts *our* logic rather than the
    # session's ability to bootstrap a job (which CI would not have).
    import plistlib
    import subprocess as subprocess_module

    from claude_usage_tray.platform import _darwin

    plist = tmp_path / "LaunchAgents" / f"{_darwin.LAUNCH_AGENT_LABEL}.plist"
    monkeypatch.setattr(_darwin, "_launch_agent_plist", lambda: plist)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess_module.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(_darwin.subprocess, "run", fake_run)

    assert _darwin.is_startup_enabled() is False

    # A path with a space is the case naive splitting gets wrong.
    _darwin.set_startup_enabled(True, '"/Applications/Claude Usage.app/Contents/MacOS/ClaudeUsage"')
    assert _darwin.is_startup_enabled() is True
    written = plistlib.loads(plist.read_bytes())
    assert written["Label"] == _darwin.LAUNCH_AGENT_LABEL
    assert written["RunAtLoad"] is True
    assert written["KeepAlive"] is False
    assert written["ProgramArguments"] == [
        "/Applications/Claude Usage.app/Contents/MacOS/ClaudeUsage"
    ], "the quoted path must survive as a single argv element"
    assert any("bootstrap" in argv for argv in calls)

    _darwin.set_startup_enabled(False, "")
    assert _darwin.is_startup_enabled() is False
    assert not plist.exists()
    assert any("bootout" in argv for argv in calls)


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: LaunchAgents")
def test_disabling_startup_twice_is_harmless(monkeypatch, tmp_path):
    # The menu toggle can be clicked off when it is already off (or after the
    # plist was removed by hand); unlink must not raise on a missing file.
    import subprocess as subprocess_module

    from claude_usage_tray.platform import _darwin

    plist = tmp_path / "LaunchAgents" / "x.plist"
    monkeypatch.setattr(_darwin, "_launch_agent_plist", lambda: plist)
    monkeypatch.setattr(
        _darwin.subprocess,
        "run",
        lambda argv, **kw: subprocess_module.CompletedProcess(argv, 1, "", "not loaded"),
    )

    _darwin.set_startup_enabled(False, "")
    _darwin.set_startup_enabled(False, "")


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: LaunchAgents")
def test_an_empty_launch_command_is_refused(monkeypatch, tmp_path):
    # Registering an empty ProgramArguments would install a login item that
    # cannot start anything -- a silent no-op the seam's contract forbids.
    from claude_usage_tray.platform import _darwin

    monkeypatch.setattr(_darwin, "_launch_agent_plist", lambda: tmp_path / "x.plist")
    with pytest.raises(platform.PlatformUnsupportedError):
        _darwin.set_startup_enabled(True, "   ")


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


def test_tray_event_loop_ownership_matches_the_platform():
    # macOS has exactly one NSApplication run loop and Tk is already running
    # it, so pystray must not start a second one on a thread; everywhere else
    # pystray owning its own loop is the supported arrangement.
    assert platform.tray_requires_host_event_loop() is IS_DARWIN


def test_tray_anchor_edge_is_one_of_the_two_named_constants():
    # Guards against a backend returning a free-form string that silently
    # fails every `== TRAY_ANCHOR_TOP` comparison at the call sites.
    assert platform.tray_anchor_edge() in (
        platform.TRAY_ANCHOR_TOP,
        platform.TRAY_ANCHOR_BOTTOM,
    )


def test_tray_anchors_to_the_menu_bar_on_macos_and_the_taskbar_elsewhere():
    expected = platform.TRAY_ANCHOR_TOP if IS_DARWIN else platform.TRAY_ANCHOR_BOTTOM
    assert platform.tray_anchor_edge() == expected


def test_read_secret_returns_none_for_an_absent_entry():
    # The contract everywhere: a missing secret is an ordinary state (nobody
    # is logged in yet), never an exception. On Windows/Linux there is no
    # store at all and this is unconditionally None; on macOS the Keychain
    # exists but holds nothing under this name.
    assert platform.read_secret("claude-usage-tests-no-such-service") is None


@pytest.mark.skipif(IS_DARWIN, reason="macOS has a real credential store")
def test_read_secret_is_always_none_off_macos():
    assert platform.read_secret("Claude Code-credentials") is None


def test_read_secret_never_raises_on_a_hostile_service_name():
    # The name reaches `security` as an argv element, never a shell string,
    # so quoting and flag-looking values are inert rather than injectable.
    for name in ("", "-w", "a b'c\"d", "--help", "$(whoami)", "x" * 300):
        assert platform.read_secret(name) is None
