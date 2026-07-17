"""claude_usage_tray.paths — app-specific path composition.

The only module that reads `sys.frozen` / `sys._MEIPASS` (PyInstaller's
frozen-app markers). Everything here is pure path arithmetic: no I/O beyond
what `Path.exists()` needs, no network, no UI.

Depends on `claude_usage_tray.platform` for the per-OS app-data root, never
the reverse — that keeps the L0/L1 dependency graph acyclic.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import platform

CONFIG_FILENAME = "config.json"
ALERT_STATE_FILENAME = "alert-state.json"
LOG_FILENAME = "claude-usage.log"
APP_DATA_DIRNAME = "ClaudeUsage"
ICON_FILENAME = "claude_usage_icon.ico"


def app_data_dir() -> Path:
    """Per-user, per-app data directory: `<platform app-data root>/ClaudeUsage`."""
    return platform.app_data_root() / APP_DATA_DIRNAME


def config_file() -> Path:
    """Canonical path for the user-authored config store."""
    return app_data_dir() / CONFIG_FILENAME


def alert_state_file() -> Path:
    """Canonical path for the machine-written, disposable alert-arm state."""
    return app_data_dir() / ALERT_STATE_FILENAME


def log_file() -> Path:
    """Canonical path for the diagnostic log.

    Lives beside the config rather than next to the exe: the packaged app may
    be installed somewhere unwritable (`Program Files`), which is the same
    reason the config store moved here.
    """
    return app_data_dir() / LOG_FILENAME


def resource_root() -> Path:
    """Root directory to resolve bundled resources (assets, etc.) from.

    Explicit about `sys._MEIPASS`: when frozen by PyInstaller (onefile
    build), resources are unpacked to that temp directory. Unfrozen (running
    from source), the repo root is two levels up from this file.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def app_icon() -> Path:
    """Path to the bundled window/tray `.ico` asset."""
    return resource_root() / "assets" / ICON_FILENAME


def startup_command() -> str:
    """The command line to register for "run at login".

    Frozen: the packaged exe itself. Unfrozen: `pythonw.exe` (not
    `python.exe`, to avoid a console window) running `launcher.py`. Does not
    branch on platform at all — the `python.exe` -> `pythonw.exe` suffix
    swap is simply a no-op string check on interpreters that aren't named
    that way.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return f'"{exe}" "{launcher}"'


def nearest_existing_dir(target: Path) -> Path:
    """If `target` doesn't exist, fall back to its parent (or the user's
    home directory if `target` has no distinct parent, i.e. a filesystem
    root). Move-only extraction of `app.py`'s original single-level
    fallback — deliberately not a recursive walk up the tree, to keep this
    extraction behavior-preserving.
    """
    destination = target.expanduser()
    if destination.exists():
        return destination
    return destination.parent if destination.parent != destination else Path.home()
