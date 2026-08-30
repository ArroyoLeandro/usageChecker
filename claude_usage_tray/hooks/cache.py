"""A usage figure cheap enough to read on every prompt and every tool call.

`api.py` already caches, per process, for 45 seconds. For a hook that is
worth exactly nothing: the harness spawns a fresh interpreter for each
event, so every single one would start with an empty cache and make its own
HTTPS round trip. On a busy turn that is a request per tool call -- slow in
front of everything the user types, and a fast route to the 429 backoff that
would then make the gate useless anyway.

So the figure lands on disk, and the freshness question is answered there.
The TTL is a deliberate trade: quota percentages move slowly (a 7-day window
does not jump five points in two minutes), and being a couple of minutes
stale is harmless when the ceiling is a threshold rather than an exact
accounting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import budget as budget_module
from .. import jsonstore, paths
from ..accounts import profile_for, resolve_config_dir

#: How long a cached usage figure is treated as current.
CACHE_TTL_SECONDS = 120

#: Upper bound on the fetch a cache miss triggers. Shorter than `api.py`'s
#: own 10s timeout on purpose: this runs in front of a user's keystroke, and
#: a gate that adds ten seconds of silence to a prompt would be turned off
#: within a day -- at which point it protects nothing.
FETCH_TIMEOUT_SECONDS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def account_token() -> str:
    """Which account the cache entry belongs to.

    The config directory, not the profile name: two installations can share
    a name and never share a quota.
    """
    return str(resolve_config_dir().config_dir)


def read_cached(path: Path | None = None, *, ttl_seconds: int = CACHE_TTL_SECONDS, now: datetime | None = None) -> float | None:
    """The cached percentage if it is still fresh, else None."""
    target = path or paths.usage_cache_file(account_token())
    now = now or _now()
    try:
        raw = jsonstore.read_json(target)
    except (jsonstore.CorruptFile, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        used = float(raw["used_percent"])
        stamped = datetime.fromisoformat(str(raw["at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    if now - stamped > timedelta(seconds=ttl_seconds):
        return None
    return used


def write_cached(used_percent: float, path: Path | None = None, *, now: datetime | None = None) -> None:
    target = path or paths.usage_cache_file(account_token())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        jsonstore.write_json_atomic(target, {"used_percent": used_percent, "at": (now or _now()).isoformat()})
    except OSError:
        # An unwritable cache costs a round trip next time. It is not a
        # reason to fail a prompt.
        return


def fetch_used_percent() -> float | None:
    """Ask the API. Imported lazily -- see below."""
    # `api` pulls in `requests`, and through it a chunk of import time this
    # package pays on *every* hook invocation. Deferring the import to the
    # cache-miss path keeps the common case (cache hit) to stdlib only,
    # which is the difference between a gate that is invisible and one the
    # user notices before every prompt. (`accounts` is imported at module
    # scope instead -- it is stdlib-clean, and the cache-hit path needs it
    # to know which account's entry to read.)
    from .. import api

    resolution = resolve_config_dir()
    if not resolution.exists:
        return None
    payload: dict[str, Any] = api.fetch_usage(profile_for(resolution), try_refresh=False)
    return budget_module.highest_used_percent(payload)


def used_percent(*, ttl_seconds: int = CACHE_TTL_SECONDS, now: datetime | None = None) -> float | None:
    """Cached-or-fetched binding quota percentage; None if unknown.

    None is the fail-open signal, and it is returned for every failure mode
    -- no session, no network, a 429, a malformed payload. `budget.evaluate`
    turns None into `allow`.
    """
    cached = read_cached(ttl_seconds=ttl_seconds, now=now)
    if cached is not None:
        return cached
    try:
        fresh = fetch_used_percent()
    except Exception:  # noqa: BLE001 -- see the fail-open note above
        return None
    if fresh is not None:
        write_cached(fresh, now=now)
    return fresh
