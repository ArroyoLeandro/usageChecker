"""Linux/BSD backend for the platform seam.

Selected by `platform/__init__.py` for every platform that is neither
Windows nor macOS. This is also the backend used in this repo's own dev/CI
environment (WSL2/Linux).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ._types import FileManagerError, PlatformUnsupportedError, Rect, TrayHandle, WorkArea


def set_app_user_model_id(app_id: str) -> None:
    # No AppUserModelID concept off Windows; explicit, documented no-op.
    return None


def subprocess_flags() -> int:
    return 0


def work_area_bounds() -> WorkArea | None:
    return None


def open_in_file_manager(path: Path) -> None:
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise FileManagerError(f"Could not open {path} with xdg-open") from exc


def startup_supported() -> bool:
    return False


def is_startup_enabled() -> bool:
    return False


def set_startup_enabled(enabled: bool, command: str) -> None:
    raise PlatformUnsupportedError("Startup registration is not supported on this platform")


def app_data_root() -> Path:
    return Path.home() / ".config"


def tray_hover_supported() -> bool:
    return False


def tray_handle_attribute() -> str | None:
    # Nothing to reach for: with no rect query there is no handle worth
    # digging out of pystray. `None` is the caller's cue to stop early.
    return None


def tray_icon_rect(handle: TrayHandle) -> Rect | None:
    # No `Shell_NotifyIconGetRect` equivalent here, and no tray model this
    # app targets. "Cannot know", not "cannot do": the caller degrades to the
    # native tooltip rather than seeing an exception.
    return None


def cursor_position() -> tuple[int, int] | None:
    return None


def read_secret(service: str) -> str | None:
    # No credential store this app targets: Claude Code writes
    # `.credentials.json` on Linux, same as Windows.
    return None


def tray_requires_host_event_loop() -> bool:
    return False


def tray_anchor_edge() -> str:
    return "bottom"


def bind_tray_click(status_item, on_primary):
    # Same as Windows: the backend's own default-item handling applies.
    return None


def bind_tray_hidpi_image(icon) -> bool:
    # pystray's X11/AppIndicator backends hand the tray a PNG and let the
    # panel scale it; there is no per-image "points vs pixels" declaration to
    # correct, so there is nothing to replace here.
    return False
