"""claude_usage_tray.logging_setup -- diagnostic logging and global exception capture.

The packaged app is built with PyInstaller's `--windowed` flag, so it owns no
console: `stdout` and `stderr` are discarded. Without this module an unhandled
exception is *completely invisible* -- the tray icon keeps running while the
UI is dead, and there is no way to find out why. That is not hypothetical:
`ui/popup.py` and `ui/profiles_window.py` shipped defaulting `theme:` to a
`Palette` instead of a `Theme`, so every window raised `AttributeError` on
render and the app looked inert with no diagnostic whatsoever.

Three capture points, because an exception can escape in three different ways
and each has its own hook:

- `sys.excepthook` -- the main thread.
- `threading.excepthook` -- worker threads. This one matters most here: the
  fetch/refresh work all runs in daemon threads, where an exception otherwise
  dies with the thread and takes its traceback with it.
- Tk callbacks -- installed by the caller via `tk_exception_handler()`, since
  wiring it needs a Tk root and this module must stay importable headless.

This is the only module that configures logging. Every other module does the
library-correct thing (`logging.getLogger(__name__)`) and configures nothing,
so nothing else needs to import this.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from types import TracebackType
from typing import Any

from . import paths

LOGGER_NAME = "claude_usage_tray"

_MAX_BYTES = 512 * 1024
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"

_log = logging.getLogger(LOGGER_NAME)


def log_exception(
    exc_type: type[BaseException],
    value: BaseException,
    tb: TracebackType | None,
    *,
    source: str,
) -> None:
    """Record an escaped exception. Never raises: a failure inside the last
    line of defense must not become the thing that kills the process."""
    try:
        _log.error("unhandled exception in %s", source, exc_info=(exc_type, value, tb))
    except Exception:  # pragma: no cover - defensive
        pass


def tk_exception_handler(source: str = "tk callback"):
    """Return a callable matching Tk's `report_callback_exception` signature.

    Tk swallows callback exceptions by printing them to stderr, which under
    `--windowed` means discarding them. Assign this to the Tk root to route
    them into the log instead.
    """

    def handler(
        exc_type: type[BaseException],
        value: BaseException,
        tb: TracebackType | None,
    ) -> None:
        log_exception(exc_type, value, tb, source=source)

    return handler


def _install_excepthooks() -> None:
    def handle_main(
        exc_type: type[BaseException],
        value: BaseException,
        tb: TracebackType | None,
    ) -> None:
        log_exception(exc_type, value, tb, source="main thread")
        sys.__excepthook__(exc_type, value, tb)

    def handle_thread(args: Any) -> None:
        # SystemExit in a worker is a normal shutdown, not a failure.
        if issubclass(args.exc_type, SystemExit):
            return
        name = getattr(args.thread, "name", "unknown")
        log_exception(args.exc_type, args.exc_value, args.exc_traceback, source=f"thread {name!r}")

    sys.excepthook = handle_main
    threading.excepthook = handle_thread


def setup(*, level: int = logging.INFO) -> logging.Logger:
    """Configure file logging and install the global exception hooks.

    Safe to call more than once: existing handlers are cleared first, so a
    second call re-configures rather than double-logging.

    If the log file cannot be opened -- an unwritable or unreachable app data
    dir -- logging degrades to console-only rather than raising. A diagnostic
    aid must never be the reason the app fails to start.
    """
    _log.setLevel(level)
    _log.propagate = False
    for handler in list(_log.handlers):
        _log.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)

    try:
        paths.app_data_dir().mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            paths.log_file(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _log.addHandler(file_handler)
        file_error: Exception | None = None
    except Exception as exc:
        file_error = exc

    # Unfrozen, there is a real console to write to (running `launcher.py`
    # from a terminal), so mirror the log there. Frozen with `--windowed`
    # there is no console and `sys.stderr` may even be None, which would make
    # StreamHandler raise -- hence the guard.
    if not getattr(sys, "frozen", False) and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        _log.addHandler(stream_handler)

    _install_excepthooks()

    if file_error is not None:
        _log.warning("file logging unavailable (%s); logging to console only", file_error)
    else:
        _log.info("logging to %s", paths.log_file())

    return _log
