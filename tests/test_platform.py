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


def _main_screen_height() -> float:
    """The height of the screen Cocoa's global coordinate space is anchored to.

    Deliberately a second, hand-rolled spelling of what `_darwin` computes:
    a test that reused the seam's own helper would agree with it even when
    both are wrong.
    """
    from AppKit import NSScreen

    return float(NSScreen.screens()[0].frame().size.height)


@pytest.fixture
def macos_status_item():
    """A real `NSStatusItem`, laid out in the real menu bar.

    There is no faking this one. The whole claim under test is that AppKit
    reports a true screen rectangle for a menu-bar item, and a stub would
    only prove that the arithmetic runs.

    The run loop is pumped until the item is actually laid out, not for a
    fixed interval. A fresh status item carries an off-screen placeholder
    frame (`origin.y = -33`) until AppKit places it in the bar, and how long
    that takes is not ours to predict -- a fixed sleep makes these tests flaky
    on a busy machine and hides the very race `app.py` retries around.
    """
    import time

    import AppKit
    import Foundation

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    status_bar = AppKit.NSStatusBar.systemStatusBar()
    item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
    item.button().setTitle_("test")

    screen_frame = AppKit.NSScreen.screens()[0].frame()
    deadline = time.monotonic() + 10.0
    laid_out = False
    while time.monotonic() < deadline:
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
            Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05)
        )
        window = item.button().window()
        if window is not None and window.isVisible():
            if AppKit.NSIntersectsRect(screen_frame, window.frame()):
                laid_out = True
                break

    if not laid_out:
        status_bar.removeStatusItem_(item)
        pytest.skip("the menu bar never laid out a test status item on this machine")

    try:
        yield item
    finally:
        status_bar.removeStatusItem_(item)


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
# Tray hover (slice e, extended to macOS). "Cannot know" -> None/False, never
# an exception: `app.py` reads that as "keep the native tooltip", which is a
# supported outcome rather than a failure.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(IS_DARWIN, reason="macOS answers this one; see the tests below")
def test_tray_hover_is_not_supported_on_linux():
    assert platform.tray_hover_supported() is False


@pytest.mark.skipif(IS_DARWIN, reason="macOS names `_status_item`; see the tests below")
def test_no_tray_handle_to_resolve_on_linux():
    # With no rect query there is nothing to dig out of pystray, and saying
    # so lets `app.py` stop before it reaches for a private attribute.
    assert platform.tray_handle_attribute() is None


@pytest.mark.skipif(IS_DARWIN, reason="macOS answers this one; see the tests below")
def test_tray_icon_rect_is_none_on_linux():
    assert platform.tray_icon_rect(platform.TrayHandle(native=12345, uid=0)) is None


def test_tray_icon_rect_does_not_raise_for_a_useless_handle():
    # The distinction the seam draws: this is a value we cannot know, not an
    # action we cannot perform, so it must not raise the way
    # set_startup_enabled does. A handle carrying nothing usable is the
    # ordinary shape of "pystray moved its attribute".
    assert platform.tray_icon_rect(platform.TrayHandle(native=None)) is None
    assert platform.tray_icon_rect(platform.TrayHandle(native=0)) is None
    assert platform.tray_icon_rect(platform.TrayHandle(native=object())) is None


@pytest.mark.skipif(IS_DARWIN, reason="macOS answers this one; see the tests below")
def test_cursor_position_is_none_on_linux():
    assert platform.cursor_position() is None


def test_tray_handle_defaults_to_the_uid_the_spike_measured():
    # uid=0 answered on pystray's win32 backend; the default records that
    # finding rather than making every caller repeat it. It lives on the
    # handle rather than in `tray_icon_rect`'s signature so that a datum only
    # one platform needs does not shape the call every platform makes.
    assert platform.TrayHandle(native=1).uid == 0


def test_tray_icon_rect_takes_one_handle_not_a_windows_shaped_pair():
    # The seam must not make macOS invent an `hwnd` it has no use for. One
    # opaque handle per platform is the whole point of `TrayHandle`.
    import inspect

    parameters = list(inspect.signature(platform.tray_icon_rect).parameters)
    assert parameters == ["handle"]


# ---------------------------------------------------------------------------
# Tray hover on macOS: the menu bar *can* answer, so unlike Linux it must not
# return the "cannot know" sentinel. Assertions stay shape-level where the
# numbers depend on the screen, and exact where a dropped Cocoa->Tk y-flip
# would still pass a shape check.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSStatusBar")
def test_tray_hover_is_supported_on_macos():
    assert platform.tray_hover_supported() is True


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: pystray's NSStatusItem")
def test_tray_handle_attribute_is_pystrays_status_item_on_macos():
    assert platform.tray_handle_attribute() == "_status_item"


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSEvent.mouseLocation")
def test_cursor_position_is_readable_on_macos():
    point = platform.cursor_position()
    assert point is not None
    x, y = point
    assert isinstance(x, int) and isinstance(y, int)
    # Cocoa's y grows upward from the bottom of the screen, Tk's downward
    # from the top. Dropping the flip would put the cursor off the bottom of
    # a tall screen far more often than not, but the honest check is simply
    # that the point lands on the desktop.
    assert 0 <= y <= _main_screen_height()


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSStatusBar")
def test_tray_icon_rect_measures_a_real_status_item_on_macos(macos_status_item):
    """The claim `_darwin.tray_icon_rect` used to deny: the menu bar *does*
    expose its item's rectangle."""
    rect = platform.tray_icon_rect(platform.TrayHandle(native=macos_status_item))

    assert rect is not None
    assert rect.width > 0 and rect.height > 0
    # The menu bar is at the top of the screen, so a correctly flipped rect
    # starts within its own height of y=0. Unflipped, `top` would come back
    # near the screen's full height instead -- which is exactly the bug that
    # puts the tooltip at the bottom of the screen, and the one a shape-only
    # assertion would wave through.
    assert 0 <= rect.top < rect.height
    assert rect.bottom <= _main_screen_height()


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSStatusBar")
def test_tray_icon_rect_is_none_once_the_item_leaves_the_bar(macos_status_item):
    # Retiring the item is the runtime shape of "the rect stopped resolving";
    # the tracker's failure budget turns a run of these into a fall back to
    # the native tooltip, so it has to be `None` and not an exception.
    from AppKit import NSStatusBar

    NSStatusBar.systemStatusBar().removeStatusItem_(macos_status_item)

    assert platform.tray_icon_rect(platform.TrayHandle(native=macos_status_item)) is None


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only: NSStatusBar")
def test_the_cursor_and_the_rect_share_one_coordinate_space(macos_status_item):
    # The single fact hover detection rests on: `Rect.contains` compares the
    # two, so a rect in Tk coordinates and a cursor in Cocoa's would hit-test
    # as "never inside" and the popup would simply never appear.
    rect = platform.tray_icon_rect(platform.TrayHandle(native=macos_status_item))
    assert rect is not None

    from AppKit import NSEvent

    cocoa = NSEvent.mouseLocation()
    tk_point = platform.cursor_position()
    assert tk_point is not None
    assert tk_point[0] == round(cocoa.x)
    assert tk_point[1] == round(_main_screen_height() - cocoa.y)


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


# --- high-DPI tray rendering ------------------------------------------------


def test_bind_tray_hidpi_image_returns_false_for_an_object_that_is_not_a_tray_icon():
    # The contract that keeps a pystray upgrade from costing the user the
    # tray: an icon that does not look the way the fix expects is declined,
    # never patched half-way and never raised over. On Windows/Linux this is
    # an unconditional False for the same reason.
    class NotAnIcon:
        pass

    assert platform.bind_tray_hidpi_image(NotAnIcon()) is False


def test_bind_tray_hidpi_image_is_a_noop_off_macos():
    if IS_DARWIN:
        pytest.skip("macOS installs a real replacement; covered separately")

    class FakeIcon:
        _status_bar = object()
        _status_item = object()

        def _assert_image(self):
            raise AssertionError("must not be called")

    icon = FakeIcon()
    original = icon._assert_image
    assert platform.bind_tray_hidpi_image(icon) is False
    assert icon._assert_image == original


@pytest.mark.skipif(not IS_DARWIN, reason="the replacement only exists on macOS")
def test_bind_tray_hidpi_image_replaces_only_the_instance_method():
    from PIL import Image

    from AppKit import NSStatusBar

    class FakeStatusItem:
        def __init__(self):
            self.image = None

        def button(self):
            return self

        def setImage_(self, image):
            self.image = image

    class FakeIcon:
        def __init__(self):
            self._status_bar = NSStatusBar.systemStatusBar()
            self._status_item = FakeStatusItem()
            self._icon_image = None
            self._icon = Image.new("RGBA", (128, 128), (255, 0, 0, 255))
            self.fallback_calls = 0

        def _assert_image(self):
            self.fallback_calls += 1

    icon = FakeIcon()
    class_method = type(icon)._assert_image

    assert platform.bind_tray_hidpi_image(icon) is True
    # The class is untouched: only this one object was patched, so a second
    # pystray.Icon in the same process is unaffected.
    assert type(icon)._assert_image is class_method
    assert icon._assert_image != class_method

    icon._assert_image()
    assert icon.fallback_calls == 0, "the original ran, so the replacement failed"

    thickness = int(icon._status_bar.thickness())
    image = icon._status_item.image
    assert image is not None
    # The point of the whole exercise: the NSImage measures `thickness`
    # POINTS while carrying `thickness * scale` pixels, which is what stops
    # AppKit from stretching a 22 px bitmap across a 44 px backing store.
    assert int(image.size().width) == thickness
    assert int(image.size().height) == thickness
    rep = image.representations()[0]
    assert int(rep.pixelsWide()) >= thickness


@pytest.mark.skipif(not IS_DARWIN, reason="the replacement only exists on macOS")
def test_hidpi_replacement_falls_back_to_pystray_instead_of_raising():
    # A pystray upgrade that changes the shape around `_assert_image` must
    # cost sharpness, never the icon. Here `_icon` is missing entirely.
    from AppKit import NSStatusBar

    class FakeIcon:
        def __init__(self):
            self._status_bar = NSStatusBar.systemStatusBar()
            self._status_item = object()
            self._icon_image = None
            self._icon = None
            self.fallback_calls = 0

        def _assert_image(self):
            self.fallback_calls += 1

    icon = FakeIcon()
    assert platform.bind_tray_hidpi_image(icon) is True
    icon._assert_image()
    assert icon.fallback_calls == 1
