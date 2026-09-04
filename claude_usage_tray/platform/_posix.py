"""Linux/BSD backend for the platform seam.

Selected by `platform/__init__.py` for every platform that is neither
Windows nor macOS. This is also the backend used in this repo's own dev/CI
environment (WSL2/Linux).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ._types import FileManagerError, PlatformUnsupportedError, Rect, TrayHandle, WorkArea


def set_app_user_model_id(app_id: str) -> None:
    # No AppUserModelID concept off Windows; explicit, documented no-op.
    return None


def subprocess_flags() -> int:
    return 0


def work_area_bounds() -> WorkArea | None:
    return None


def work_area_for_rect(rect: Rect) -> WorkArea | None:
    # Same honest "I don't know" as `work_area_bounds()`: there is no work
    # area query here, so there is no per-monitor one either.
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


def present_window_without_activating(window_title: str) -> bool:
    # As on Windows: no activation to avoid, so the caller shows the window
    # the ordinary way. Moot in practice anyway -- `tray_hover_supported()`
    # is False here and the native tooltip stands.
    return False


def preserve_frontmost_application(action: Callable[[], None]) -> None:
    # Creating a window here does not take the front from another
    # application, so there is nothing to hand back: just run the action.
    action()


def prepare_overlay_window(window_title: str) -> bool:
    # No Spaces to join and no click-through flag to set: a `-topmost` window
    # already behaves the way the caller wants. `False` is "nothing to do
    # here", not a failure.
    return False


def hide_window_without_unmapping(window_title: str) -> bool:
    # The caller only needs this where its own hide path would unmap the
    # window and stop it drawing, which is an Aqua problem. `False` sends it
    # back to the ordinary hide.
    return False


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
