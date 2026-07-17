"""claude_usage_tray.jsonstore — durability primitives.

The only module in the codebase that writes bytes to disk. Every write goes
through `write_json_atomic`: same-directory temp file, `fsync` before the
swap, `os.replace` (never `os.rename` — see the module-level rationale
below) to perform the swap atomically, and a Windows-only retry for the
transient `PermissionError` an AV scanner or indexer can cause.

Stdlib only — this module must stay importable with zero display server and
zero third-party packages, on every platform.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_WINDOWS_REPLACE_RETRIES = 3
_WINDOWS_REPLACE_BACKOFF_SECONDS = 0.05


class CorruptFile(Exception):
    """Raised by `read_json` when the target exists but is not valid JSON."""

    def __init__(self, path: Path):
        super().__init__(f"{path} is not valid JSON")
        self.path = path


def read_json(path: Path) -> dict[str, Any] | None:
    """Read and parse `path` as JSON.

    Returns `None` if the file does not exist. Raises `CorruptFile` if it
    exists but cannot be read as JSON — callers decide the corruption policy
    (quarantine-and-warn for `config.json`, silent-discard for
    `alert-state.json`); this function only reports the fact.

    Undecodable bytes are `CorruptFile`, not a `UnicodeDecodeError`: a file
    that is not valid UTF-8 has failed to parse just as surely as one with a
    stray brace, and both arrive by the same route (a half-written or
    externally-mangled file). Letting `UnicodeDecodeError` escape would take
    the whole app down at startup — `load_config` and `alerts.load_state`
    both call this before there is any window to report on — and would
    bypass the very quarantine the corrupt file is owed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise CorruptFile(path) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptFile(path) from exc


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write `payload` to `path` as JSON, atomically.

    The temp file is created in the SAME directory as `path` — `os.replace`
    across filesystems is not a rename and raises `OSError` (cross-device
    link). Content is flushed and `fsync`'d before the swap, so a crash
    between "temp written" and "swap complete" cannot leave a truncated file
    at `path`: either the old complete file or the new complete file is
    there, never a partial one.

    `os.replace`, never `os.rename`: `os.rename` does not fail universally
    on an existing target — on POSIX it silently replaces, but it raises
    `FileExistsError` on Windows, the actual target platform. `os.replace`
    is the portable spelling that overwrites on both.

    On Windows, an antivirus scanner or file indexer can transiently hold
    the target open, making `os.replace` raise `PermissionError`. This is
    retried a few times with a short backoff before giving up -- the
    alternative is a spurious "your data didn't save" for the user.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _replace_with_retry(tmp_path: Path, target: Path) -> None:
    attempts = _WINDOWS_REPLACE_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp_path, target)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(_WINDOWS_REPLACE_BACKOFF_SECONDS)


def quarantine(path: Path, *, now: datetime | None = None) -> Path:
    """Rename a corrupt file out of the way, preserving its bytes.

    Returns the quarantine path: `<name>.corrupt-<utc-iso>`, using
    `os.replace` so the destination is guaranteed free before the caller's
    next write, and the original bytes are preserved unmodified (a rename,
    not a copy-then-delete).
    """
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%S%fZ")
    quarantine_path = path.with_name(f"{path.name}.corrupt-{stamp}")
    os.replace(path, quarantine_path)
    return quarantine_path
