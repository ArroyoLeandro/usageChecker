"""macOS backend for the platform seam.

Selected by `platform/__init__.py` only when running on macOS.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from ._types import FileManagerError, PlatformUnsupportedError, Rect, WorkArea


def set_app_user_model_id(app_id: str) -> None:
    # No AppUserModelID concept on macOS; explicit, documented no-op.
    return None


def subprocess_flags() -> int:
    return 0


def work_area_bounds() -> WorkArea | None:
    """The main screen's usable rectangle, in Tk's coordinate space.

    `NSScreen.visibleFrame()` is exactly the desktop minus the menu bar and
    the Dock -- the macOS equivalent of `SPI_GETWORKAREA` -- but it is
    expressed in Cocoa coordinates, whose origin is the *bottom* left of the
    main screen and whose y grows upward. Tk's origin is the top left with y
    growing downward, so the two y values have to be flipped against the full
    frame's height; x needs no adjustment. Getting this wrong does not throw,
    it just puts windows off-screen, which is why the flip is spelled out
    here once instead of at each call site.

    AppKit is imported inside the function on purpose: `paths.py` (L1) pulls
    this seam in, and a module-scope pyobjc import would drag a GUI framework
    into every headless import of the package, tests included. Any failure --
    pyobjc absent, no attached screen -- degrades to the documented `None`
    rather than raising, because a caller that can handle "I don't know" for
    Windows can handle it here.
    """
    try:
        from AppKit import NSScreen
    except Exception:
        return None

    screen = NSScreen.mainScreen()
    if screen is None:
        return None

    full = screen.frame()
    visible = screen.visibleFrame()
    screen_height = full.size.height

    left = int(visible.origin.x)
    right = int(visible.origin.x + visible.size.width)
    # Flip: Cocoa's visible.origin.y measures up from the bottom, so the
    # rect's *top* in Tk terms is whatever is left above it.
    top = int(screen_height - (visible.origin.y + visible.size.height))
    bottom = int(screen_height - visible.origin.y)
    return WorkArea(left=left, top=top, right=right, bottom=bottom)


def open_in_file_manager(path: Path) -> None:
    try:
        subprocess.Popen(["open", str(path)])
    except OSError as exc:
        raise FileManagerError(f"Could not open {path} in Finder") from exc


#: The LaunchAgent's job label, and the basename of its plist. Reverse-DNS by
#: convention, and stable: `launchctl` keys the loaded job off this string, so
#: changing it would strand the previously registered job under the old name.
LAUNCH_AGENT_LABEL = "com.anthropic.claudeusage"


def _launch_agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def startup_supported() -> bool:
    return True


def is_startup_enabled() -> bool:
    """Whether our LaunchAgent plist is installed.

    The file's presence is the answer, rather than parsing `launchctl print`:
    a plist in `~/Library/LaunchAgents` with `RunAtLoad` is by definition what
    starts the app at the next login, which is the question being asked. A job
    booted out for the current session but still on disk will run again next
    login, so reporting it as enabled is the truthful answer.
    """
    return _launch_agent_plist().is_file()


def set_startup_enabled(enabled: bool, command: str) -> None:
    """Install or remove the "open at login" LaunchAgent.

    `command` arrives as a shell-style line (it has to be one on Windows,
    where the registry stores exactly that), but `launchctl` wants an argv
    array -- so it is split with `shlex`, which understands the quoting
    `paths.startup_command` puts in. Quoting matters here: the frozen app's
    path runs through `/Users/<name>/Applications`, and an unsplit or
    naively-split path breaks on the first space.

    Writing the plist is what makes this take effect at the *next* login;
    the `launchctl bootstrap` call is what makes it true for the session
    already running. The preceding `bootout` clears any job left from an
    earlier install -- `bootstrap` fails outright on a label already loaded --
    and its own failure is expected and ignored, since "nothing was loaded"
    is the normal case.
    """
    plist_path = _launch_agent_plist()
    domain = f"gui/{os.getuid()}"

    if not enabled:
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True,
        )
        plist_path.unlink(missing_ok=True)
        return

    argv = shlex.split(command)
    if not argv:
        raise PlatformUnsupportedError(f"Cannot register an empty launch command: {command!r}")

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": LAUNCH_AGENT_LABEL,
                "ProgramArguments": argv,
                "RunAtLoad": True,
                # Not a daemon: if the user quits from the menu, it stays
                # quit until the next login. `KeepAlive` would resurrect it
                # immediately and make "Salir" look broken.
                "KeepAlive": False,
                "ProcessType": "Interactive",
            },
            handle,
        )

    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # The plist is on disk, so the next login is already covered; only
        # loading it *now* failed. Say so rather than pretending either full
        # success or total failure -- a silent no-op is the one outcome the
        # seam's contract forbids.
        raise PlatformUnsupportedError(
            "The login item was installed but could not be started for this "
            f"session: {result.stderr.strip() or result.returncode}"
        )


def app_data_root() -> Path:
    return Path.home() / "Library" / "Application Support"


def tray_hover_supported() -> bool:
    return False


def tray_icon_rect(hwnd: int, uid: int) -> Rect | None:
    # The menu bar is not a notify-icon tray and exposes no equivalent rect
    # query. "Cannot know" -> the caller keeps the native tooltip.
    return None


def cursor_position() -> tuple[int, int] | None:
    return None


#: How long to let `security` run before giving up. It is a local Keychain
#: read, so it is either instant or it is stuck behind an unlock prompt that
#: nobody is going to answer -- and the tray must not freeze waiting.
_SECURITY_TIMEOUT_SECONDS = 10


def read_secret(service: str) -> str | None:
    """Read a generic password out of the login Keychain via `/usr/bin/security`.

    Shelling out rather than linking the Security framework is deliberate.
    A Keychain item carries an ACL naming the apps allowed to read it without
    prompting, and Claude Code's item names Apple-signed `security` among
    them; a fresh unsigned binary of our own would trip the "wants to access
    your keychain" panel on every poll. This also keeps the seam free of a
    pyobjc import at module scope.

    `-w` prints the bare password (here, Claude Code's credentials JSON) and
    nothing else. Every failure -- no such item, locked keychain, denied
    access, `security` missing -- collapses to `None`, because the caller's
    response is the same in each case: treat the user as not logged in.

    The empty name is rejected before it gets there: `security` treats an
    absent `-s` value as "no service constraint" and happily returns the
    first generic password in the keychain, which would be some unrelated
    application's secret handed back as if it were ours. No caller passes an
    empty service today, so this guards a mistake rather than a scenario --
    but the failure mode is silently reading someone else's credentials, and
    that is not a thing to leave to call-site discipline.
    """
    if not service.strip():
        return None
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=_SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def tray_requires_host_event_loop() -> bool:
    # An NSStatusItem belongs to the process's one NSApplication, and under
    # Aqua Tk that run loop is the one `Tk.mainloop()` drives. See the seam's
    # docstring for what the caller has to do about it.
    return True


def tray_anchor_edge() -> str:
    return "top"


#: Cached ObjC delegate class. Declaring an `NSObject` subclass registers the
#: name with the Objective-C runtime, and doing that twice under one name is
#: an error -- so the class is built once, lazily, and reused.
_CLICK_TARGET_CLASS = None


def _click_target_class():
    global _CLICK_TARGET_CLASS
    if _CLICK_TARGET_CLASS is not None:
        return _CLICK_TARGET_CLASS

    import objc
    from AppKit import NSApplication, NSEventModifierFlagControl, NSEventTypeRightMouseUp
    from Foundation import NSObject

    class _ClaudeUsageClickTarget(NSObject):
        """Target for the status item button's action.

        Holds the two Python callables and picks between them by inspecting
        the event that is being dispatched: AppKit gives one action for both
        buttons, and `NSApp.currentEvent()` is the documented way to ask which
        one it was. Control-click counts as secondary because macOS treats it
        that way everywhere else.
        """

        def initWithPrimary_secondary_(self, primary, secondary):
            self = objc.super(_ClaudeUsageClickTarget, self).init()
            if self is None:
                return None
            self._primary = primary
            self._secondary = secondary
            return self

        def onClick_(self, sender):
            event = NSApplication.sharedApplication().currentEvent()
            secondary = False
            if event is not None:
                secondary = event.type() == NSEventTypeRightMouseUp or bool(
                    event.modifierFlags() & NSEventModifierFlagControl
                )
            handler = self._secondary if secondary else self._primary
            handler()

    _CLICK_TARGET_CLASS = _ClaudeUsageClickTarget
    return _CLICK_TARGET_CLASS


def bind_tray_click(status_item: Any, on_primary: Callable[[], None]) -> Any | None:
    """Route a primary click to `on_primary`, leaving the menu on secondary.

    Why this exists: pystray's macOS backend sets `HAS_DEFAULT_ACTION = False`,
    and for a real reason -- once an `NSMenu` is attached to a status item,
    AppKit opens the menu itself and the button's action never fires. So the
    `default=True` that gives Windows its one-click-shows-usage behaviour is
    silently inert on a Mac, and clicking the icon can only ever open a menu.

    The fix is to take the menu back off the status item and dispatch clicks
    ourselves, re-presenting that same menu for secondary clicks. Nothing is
    rebuilt: the `NSMenu` pystray already assembled from the app's menu
    definition is reused as-is, so the two paths can never drift.

    Returns a token the caller must keep a reference to -- it is the ObjC
    target object, and letting it be collected would leave the button firing
    at nothing. Returns `None` if the wiring could not be completed, in which
    case *nothing has been changed*: the menu is still attached and the icon
    behaves exactly as before. The order below matters for that guarantee --
    the menu is detached only once the new target is installed.
    """
    try:
        from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp
        import objc
    except Exception:
        return None

    menu = status_item.menu()
    if menu is None:
        # pystray has not built the menu yet, or there is none. Rewiring now
        # would strand the icon with no menu at all.
        return None

    button = status_item.button()
    if button is None:
        return None

    def open_menu() -> None:
        status_item.popUpStatusItemMenu_(menu)

    target = _click_target_class().alloc().initWithPrimary_secondary_(on_primary, open_menu)
    if target is None:
        return None

    button.setTarget_(target)
    button.setAction_(objc.selector(None, selector=b"onClick:"))
    button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
    # Last: until this line the menu still owns the click, so an exception
    # anywhere above leaves the original behaviour intact.
    status_item.setMenu_(None)
    return target
