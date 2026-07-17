"""macOS backend for the platform seam.

Selected by `platform/__init__.py` only when running on macOS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ._types import FileManagerError, PlatformUnsupportedError, Rect, WorkArea


def set_app_user_model_id(app_id: str) -> None:
    # No AppUserModelID concept on macOS; explicit, documented no-op.
    return None


def subprocess_flags() -> int:
    return 0


def work_area_bounds() -> WorkArea | None:
    return None


def open_in_file_manager(path: Path) -> None:
    try:
        subprocess.Popen(["open", str(path)])
    except OSError as exc:
        raise FileManagerError(f"Could not open {path} in Finder") from exc


def startup_supported() -> bool:
    return False


def is_startup_enabled() -> bool:
    return False


def set_startup_enabled(enabled: bool, command: str) -> None:
    raise PlatformUnsupportedError("Startup registration is not supported on macOS")


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
