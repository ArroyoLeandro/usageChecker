"""claude_usage_tray.platform — the single seam for OS-specific behavior.

This is the ONLY module in the codebase permitted to reference
`sys.platform`. Every other module (`api.py`, `config.py`, `app.py`, ...)
must obtain platform-dependent behavior by calling into this package,
never by branching on `sys.platform` itself. `tests/test_platform_seam.py`
enforces this mechanically.

This package does not shadow the stdlib `platform` module: Python 3 uses
absolute imports, so `import platform` anywhere in this codebase (including
inside this package's own submodules, indirectly) still resolves to the
stdlib module. `platform` and `claude_usage_tray.platform` coexist in
`sys.modules` under their own distinct keys.

Unknown vs. unsupported: a platform that *cannot know* an answer (work area,
startup-enabled query) gets a defined "I don't know" value (`None` / `False`)
rather than an exception. A platform asked to *do* something it fundamentally
cannot (register startup, launch a file manager that fails) raises — a
silent no-op would be indistinguishable from success.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from ._types import FileManagerError, PlatformUnsupportedError, Rect, TrayHandle, WorkArea

IS_WINDOWS = sys.platform == "win32"
_IS_DARWIN = sys.platform == "darwin"

if IS_WINDOWS:
    from . import _windows as _backend
elif _IS_DARWIN:
    from . import _darwin as _backend
else:
    from . import _posix as _backend

__all__ = [
    "IS_WINDOWS",
    "PlatformUnsupportedError",
    "FileManagerError",
    "Rect",
    "TrayHandle",
    "WorkArea",
    "set_app_user_model_id",
    "subprocess_flags",
    "work_area_bounds",
    "open_in_file_manager",
    "startup_supported",
    "is_startup_enabled",
    "set_startup_enabled",
    "app_data_root",
    "tray_hover_supported",
    "tray_handle_attribute",
    "tooltip_window_style",
    "tray_icon_rect",
    "cursor_position",
    "read_secret",
    "tray_requires_host_event_loop",
    "tray_anchor_edge",
    "bind_tray_click",
    "bind_tray_hidpi_image",
    "TRAY_ANCHOR_TOP",
    "TRAY_ANCHOR_BOTTOM",
]

#: The screen edge the tray/menu-bar icon lives on. Windows and Linux put it
#: at the bottom (taskbar/system tray); macOS puts it at the top (menu bar).
#: Named constants rather than bare strings so a typo is an AttributeError at
#: import time instead of a window that silently opens on the wrong edge.
TRAY_ANCHOR_TOP = "top"
TRAY_ANCHOR_BOTTOM = "bottom"


def set_app_user_model_id(app_id: str) -> None:
    """Set the process's explicit AppUserModelID so Windows attributes tray
    toasts/balloons to a friendly app identity rather than the executable's
    filename. An explicit, documented no-op on every non-Windows platform —
    call it once at startup, before the tray icon runs."""
    _backend.set_app_user_model_id(app_id)


def subprocess_flags() -> int:
    """Return the `subprocess.run`/`Popen` creation flag to suppress a console
    window on Windows, or an inert `0` on every other platform."""
    return _backend.subprocess_flags()


def work_area_bounds() -> WorkArea | None:
    """Return the usable desktop rectangle, or `None` if it cannot be
    determined on this platform (an explicit "I don't know", replacing the
    legacy `(0, 0, 0, 0)` sentinel, which looked like a valid rect)."""
    return _backend.work_area_bounds()


def open_in_file_manager(path: Path) -> None:
    """Open `path` in the platform's file manager (Explorer / Finder /
    whatever handles `xdg-open`). Raises `FileManagerError` on failure —
    never swallows the error silently."""
    _backend.open_in_file_manager(path)


def startup_supported() -> bool:
    """Whether "run at login" registration is available on this platform."""
    return _backend.startup_supported()


def is_startup_enabled() -> bool:
    """Whether the app is currently registered to run at login. Always
    `False` where `startup_supported()` is `False`."""
    return _backend.is_startup_enabled()


def set_startup_enabled(enabled: bool, command: str) -> None:
    """Register or unregister the app to run at login using `command` as the
    launch line. Raises `PlatformUnsupportedError` where unsupported — guard
    the call with `startup_supported()` first."""
    _backend.set_startup_enabled(enabled, command)


def app_data_root() -> Path:
    """Return the per-user application-data root for this platform
    (`%APPDATA%` on Windows, `~/Library/Application Support` on macOS,
    `~/.config` on Linux)."""
    return _backend.app_data_root()


def tray_hover_supported() -> bool:
    """Whether this platform can report a tray icon's screen rectangle, and
    therefore whether hovering over it can be detected at all.

    `True` on Windows (`Shell_NotifyIconGetRect`) and on macOS (the status
    item's own `NSStatusBarWindow` knows its frame). `False` on Linux, where
    there is no tray model this app targets — the caller keeps the native
    tooltip there."""
    return _backend.tray_hover_supported()


def tray_handle_attribute() -> str | None:
    """The attribute on a `pystray.Icon` that carries this platform's tray
    handle, or `None` where hover tracking is not available at all.

    `"_hwnd"` on Windows, `"_status_item"` on macOS. Both are private to
    pystray and discovered rather than published, which is why the caller
    reads them defensively and degrades to the native tooltip when they are
    missing.

    The seam names the attribute but does not read it: reaching into a tray
    library is that library's business, not the OS's, and `_windows.py` is
    deliberately stdlib-only. What *is* an OS fact is which kind of object
    identifies an icon here — an integer window handle or an AppKit object —
    and that is exactly what this answers."""
    return _backend.tray_handle_attribute()


def tooltip_window_style() -> tuple[str, tuple[str, ...]] | None:
    """The window class this platform wants a tooltip surface declared as, or
    `None` where an ordinary borderless window is already correct.

    `None` on Windows and Linux. On macOS, `("help", ())` -- the Aqua window
    class for a tooltip, which the UI layer hands to Tk's
    `::tk::unsupported::MacWindowStyle`.

    This exists because of a genuine OS fact, not a toolkit quirk: on Aqua,
    mapping an ordinary window *activates the application*, and activating an
    application makes macOS switch the user to the Space where that
    application's windows live. A tooltip that yanks the user off their
    current Space and onto another monitor because the pointer grazed a
    menu-bar icon is hostile, and it is exactly what the borderless window
    used everywhere else does here. A `help`-class window is declared to the
    window server as a tooltip, and mapping one does not activate anybody.

    The seam names the class but does not apply it: writing it means calling
    into Tcl, and this package is deliberately free of tkinter (see
    `tray_handle_attribute()`, which strikes the same bargain with pystray).
    What *is* an OS fact is that this platform distinguishes tooltip windows
    from ordinary ones at all, and that is what this answers.

    The attribute tuple is empty on purpose and is not a placeholder for
    `noActivates`. Measured on Tk 8.6.18 / macOS 26.6 over repeated trials,
    passing *any* attribute list alongside the class re-applies the window's
    attributes after it has been created, and that re-application is itself
    what activates the app -- `("help", ("noActivates",))` activated on 9 of
    10 runs, while `("help", ())` activated on 0 of 12. The tuple stays in the
    signature because the class/attributes pair is the shape Aqua actually
    has, and a future surface may need one.
    """
    return _backend.tooltip_window_style()


def tray_icon_rect(handle: TrayHandle) -> Rect | None:
    """Return the screen rectangle of the tray icon `handle` identifies, or
    `None` when it cannot be determined — including the ordinary case of an
    icon hidden in the taskbar overflow.

    Top-left origin with y growing downward on every platform, matching Tk
    and `cursor_position()`. macOS reports its own geometry in Cocoa
    coordinates (bottom-left origin, y growing up) and the backend flips it,
    so no caller has to know which convention it is holding.

    Never cache the result. On macOS the menu-bar item slides horizontally
    every time another icon appears or disappears, so a rect read once is
    wrong within seconds; on Windows the icon moves in and out of the
    overflow flyout. Re-read it on every poll — that is what the hover
    tracker does.

    Where `tray_requires_host_event_loop()` is `True` this call reads live
    AppKit state and must be made on the host toolkit's event loop, the same
    rule that governs every other write to the icon.
    """
    return _backend.tray_icon_rect(handle)


def cursor_position() -> tuple[int, int] | None:
    """Return the cursor's `(x, y)` in the same coordinate space
    `tray_icon_rect` reports, or `None` where it cannot be known."""
    return _backend.cursor_position()


def read_secret(service: str) -> str | None:
    """Read `service` from the OS credential store, or `None`.

    An "I don't know" everywhere the platform has no such store (Windows and
    Linux here), and equally `None` when the store exists but holds no entry
    under that name -- the caller cannot act differently on those two cases,
    so they are not distinguished. Never raises: a missing secret is an
    ordinary state (the user is simply not logged in yet), not a fault.

    Exists because Claude Code does not store its OAuth token the same way on
    every platform: on Windows and Linux it writes `.credentials.json` inside
    the config dir, while on macOS it puts the same JSON blob in the login
    Keychain and leaves no file behind. Reading a file is not an OS fact and
    stays in `api.py`; reaching into a *platform credential store* is, and
    belongs here.
    """
    return _backend.read_secret(service)


def tray_requires_host_event_loop() -> bool:
    """Whether the tray icon must be serviced by the GUI toolkit's own event
    loop instead of one pystray runs on a thread of its own.

    `True` on macOS: an `NSStatusItem` is an AppKit object, so it must be
    created and updated on the main thread, and its clicks are dispatched by
    the process's single `NSApplication` run loop -- which, under Aqua Tk, is
    the loop `Tk.mainloop()` is already running. The caller answers this by
    using pystray's `run_detached()` (which readies the icon without starting
    a second loop) rather than `run()` on a background thread, and by hopping
    every later mutation of the icon back onto the main thread.

    `False` on Windows and Linux, where pystray owns a message loop of its own
    and driving it from a background thread is the supported arrangement.
    """
    return _backend.tray_requires_host_event_loop()


def tray_anchor_edge() -> str:
    """The screen edge the tray icon sits on: `TRAY_ANCHOR_TOP` or
    `TRAY_ANCHOR_BOTTOM`.

    A window that means to appear *near the tray icon* has to know which end
    of the work area to measure from. Every other platform here anchors at
    the bottom, next to a taskbar; macOS anchors at the top, under the menu
    bar. Returned as an edge rather than a coordinate because the caller also
    needs the window's own measured height to place it, and that is a Tk fact
    this seam has no business knowing.
    """
    return _backend.tray_anchor_edge()


def bind_tray_click(status_item: Any, on_primary: Callable[[], None]) -> Any | None:
    """Make a primary click on the tray icon invoke `on_primary` directly.

    Windows gets this for free: pystray's backend honours the `default=True`
    menu item, so a left click already runs it and this is an explicit no-op
    returning `None`. macOS does not -- an `NSStatusItem` with a menu attached
    hands every click to the menu -- so there the backend rewires the button
    and re-presents the menu on secondary clicks instead.

    Returns an opaque token that the caller must hold a reference to for as
    long as the icon lives, or `None` when nothing was rewired. `None` is not
    a failure to handle: it means the platform's own default-action handling
    is in force, which is the correct behaviour there.
    """
    return _backend.bind_tray_click(status_item, on_primary)


def bind_tray_hidpi_image(icon: Any) -> bool:
    """Make the tray icon rasterise at the display's real pixel density.

    macOS is the only platform here that needs this, and it needs it badly:
    pystray's `NSStatusItem` image is built from a 22x22-pixel PNG and handed
    to AppKit with no indication that it is a 1x asset, so on every Retina
    Mac the system stretches 22 pixels across 44 and the icon is permanently
    soft. The macOS backend corrects that by rasterising at
    `thickness * backingScaleFactor` and declaring the image's size in
    points. Windows and Linux return `False`: their backends hand the shell a
    bitmap it already scales correctly, so there is nothing to correct.

    Returns whether anything was installed. `False` is not a failure -- it is
    either "this platform is already correct" or "the tray library did not
    look the way the fix expects", and both leave a working, merely less
    crisp, icon. The caller must repaint after a `True` (assigning
    `icon.icon` back to itself suffices) for the sharper image to appear.
    """
    return _backend.bind_tray_hidpi_image(icon)
