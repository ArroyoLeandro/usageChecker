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
BUDGET_DIRNAME = "budgets"
USAGE_CACHE_FILENAME = "usage-cache.json"
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


def budget_dir() -> Path:
    """Directory holding one ceiling per Claude session."""
    return app_data_dir() / BUDGET_DIRNAME


def safe_session_token(session_id: str) -> str:
    """`session_id` reduced to characters that are safe in a filename.

    Session ids are uuids in practice, but this value arrives from outside
    the process -- a hook's stdin, an environment variable -- and is about
    to be concatenated into a path. `../../etc/passwd` must not become a
    path, so anything outside `[A-Za-z0-9._-]` is replaced rather than
    trusted, and a leading dot is neutralised so the result can never be a
    relative-path segment.
    """
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "_" for char in session_id)
    cleaned = cleaned.strip(".") or "unknown"
    return cleaned[:128]


def budget_file(session_id: str) -> Path:
    """Canonical path for one session's usage ceiling.

    One file per session rather than one file holding every session's
    ceiling, and deliberately: several Claude sessions can be running at
    once, each writing through `write_json_atomic`, which replaces a file
    wholesale. A shared map would make two sessions setting a budget at the
    same moment a last-writer-wins race that silently drops one of them.
    Separate files cannot collide.

    Beside `config.json` rather than inside it, for the same reason
    `alert-state.json` is: these are written by short-lived hook and MCP
    processes, while `config.json` is owned by a long-running tray that
    holds it in memory and rewrites it whole.
    """
    return budget_dir() / f"{safe_session_token(session_id)}.json"


def usage_cache_file(account_token: str) -> Path:
    """Canonical path for the cross-process usage cache the hooks read.

    `api.py` caches per process, which is exactly nothing for a hook: the
    harness spawns a fresh interpreter for every prompt and every tool call.
    Without a cache on disk, the gate would put a network round trip in
    front of each one -- and then get rate-limited for it.

    Keyed per account rather than shared. Two Claude installations on one
    machine can be two different accounts with two different quotas, and a
    single cache file would let a session on one of them be blocked by the
    other's usage -- a gate firing on a number that has nothing to do with
    the account it is gating.

    Machine-written and disposable, like `alert-state.json`.
    """
    stem = Path(USAGE_CACHE_FILENAME).stem
    return app_data_dir() / f"{stem}-{safe_session_token(account_token)}.json"


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
