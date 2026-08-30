"""Windows backend for the platform seam.

Selected by `platform/__init__.py` only when running on Windows.
Everything Windows-only (`winreg`, `ctypes.windll`, `os.startfile`) lives
here so it is never even imported on another OS.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

from ._types import FileManagerError, Rect, WorkArea

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ClaudeUsage"


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    """`Shell_NotifyIconGetRect`'s input: which notify icon we are asking about."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", ctypes.c_byte * 16),
    ]


# Declared once, at import. `restype = HRESULT` is what makes ctypes raise
# OSError on a failure HRESULT instead of handing back an int nobody checks --
# the same trap `Shell_NotifyIcon`'s unchecked `BOOL` restype set for
# `Icon.notify()` elsewhere in this app.
ctypes.windll.shell32.Shell_NotifyIconGetRect.argtypes = [
    ctypes.POINTER(_NOTIFYICONIDENTIFIER),
    ctypes.POINTER(_RECT),
]
ctypes.windll.shell32.Shell_NotifyIconGetRect.restype = ctypes.HRESULT


def set_app_user_model_id(app_id: str) -> None:
    """Set this process's explicit AppUserModelID.

    Windows attributes a tray balloon/toast to whatever AppUserModelID the
    process declares; with none set it falls back to the executable's filename
    (`python.exe` in dev, `claudeusage.exe` frozen). Setting it here is what
    lets the toast read as a friendly app name instead. Returns an HRESULT
    rather than raising, so a failure is inert -- the app must never die over
    a cosmetic toast attribution.
    """
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def subprocess_flags() -> int:
    return subprocess.CREATE_NO_WINDOW


def work_area_bounds() -> WorkArea | None:
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    rect = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return WorkArea(rect.left, rect.top, rect.right, rect.bottom)


def open_in_file_manager(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as exc:
        raise FileManagerError(f"Could not open {path} in Explorer") from exc


def startup_supported() -> bool:
    return True


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
        return True
    except OSError:
        return False


def set_startup_enabled(enabled: bool, command: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


def app_data_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    return Path.home() / "AppData" / "Roaming"


def tray_hover_supported() -> bool:
    return True


def tray_icon_rect(hwnd: int, uid: int) -> Rect | None:
    """The screen rectangle of one notify icon, or `None` if unobtainable.

    `None` is a routine answer, not an error: an icon parked in the taskbar
    overflow flyout genuinely has no rect, and the shell says so by failing.
    The caller reads that as "cannot know" and falls back to the native
    tooltip, per the seam's unknown-vs-unsupported rule.

    `hwnd` and `uid` are plain ints supplied by the caller. This module never
    touches pystray -- it is stdlib-only by layer, and reading a tray
    library's private attribute is that library's business, not the OS's.
    """
    identifier = _NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(_NOTIFYICONIDENTIFIER)
    identifier.hWnd = wintypes.HWND(hwnd)
    identifier.uID = wintypes.UINT(uid)
    rect = _RECT()
    try:
        ctypes.windll.shell32.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect))
    except OSError:
        return None
    if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
        return None
    return Rect(left=rect.left, top=rect.top, right=rect.right, bottom=rect.bottom)


def cursor_position() -> tuple[int, int] | None:
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


def read_secret(service: str) -> str | None:
    # Claude Code writes its token to `.credentials.json` here; there is no
    # credential-store read to make. Explicit, documented "nothing to find".
    return None


def tray_requires_host_event_loop() -> bool:
    # pystray's win32 backend runs its own message loop on a thread, which is
    # the arrangement `app.py` was built around.
    return False


def tray_anchor_edge() -> str:
    return "bottom"


def bind_tray_click(status_item, on_primary):
    # pystray's win32 backend honours `default=True` on a menu item, so the
    # primary click already reaches the right callback. Nothing to rewire.
    return None
