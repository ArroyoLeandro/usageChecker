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

from ._types import FileManagerError, PlatformUnsupportedError, Rect, WorkArea

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
    "tray_icon_rect",
    "cursor_position",
]


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
    therefore whether hovering over it can be detected at all. `False`
    everywhere but Windows — the caller keeps the native tooltip there."""
    return _backend.tray_hover_supported()


def tray_icon_rect(hwnd: int, uid: int = 0) -> Rect | None:
    """Return the screen rectangle of the notify icon identified by
    (`hwnd`, `uid`), or `None` when it cannot be determined — including the
    ordinary case of an icon hidden in the taskbar overflow.

    `uid=0` is the default because that is the value the hover spike measured
    working against pystray's win32 backend. Both values are the caller's to
    supply: this seam does OS, and digging a window handle out of a tray
    library is not an OS fact."""
    return _backend.tray_icon_rect(hwnd, uid)


def cursor_position() -> tuple[int, int] | None:
    """Return the cursor's `(x, y)` in the same coordinate space
    `tray_icon_rect` reports, or `None` where it cannot be known."""
    return _backend.cursor_position()
