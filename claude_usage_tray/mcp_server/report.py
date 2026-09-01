"""Turns an `api.fetch_usage` payload into the object a model reads.

Pure: no clock of its own (`now` is always passed), no I/O, no network. The
same reason `alerts.py` is pure -- the interesting cases here are "quota
already reset", "field missing", "session expired", and none of them should
need a live account to test.

**Why this does not reuse `formatting.py`'s renderers.** `reset_text` and
`reset_label` exist to fit a tooltip: they are Spanish, they are truncated
to a pixel budget, and they collapse an unknown reset into `"—"`. All three
are right for a human reading an icon and wrong for a caller that has to
*compare* the number to a threshold. So this module reuses `formatting.pct`
-- the parsing, which is the part that has a single correct answer -- and
emits its own machine-facing fields next to a rendered string, rather than
asking a display helper to be an API.

The labels here are English, unlike `quotas.QUOTA_WINDOW_LABELS`. Those name
the windows for the user, in the tray's language; these name them for a
model, and per repo convention generated artifacts and machine-facing copy
default to English. The **ids** still come from `quotas.QUOTA_WINDOWS`, so
the tray and this server can never disagree about what a window is called or
in what order the windows are read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..budget import highest_used_percent
from ..formatting import pct
from ..quotas import QUOTA_WINDOWS

#: Machine-facing names for the ids in `quotas.QUOTA_WINDOWS`, same order.
WINDOW_LABELS: dict[str, str] = {
    "session": "Last 5 hours",
    "weekly": "Last 7 days",
    "weekly_fable": "Fable, last 7 days",
}

#: Short forms used in the one-line `summary`.
WINDOW_SHORT: dict[str, str] = {
    "session": "5h",
    "weekly": "7d",
    "weekly_fable": "Fable",
}

#: `raw` keys already reported as their own window; everything else in `raw`
#: that carries a utilization is a secondary limit (per-model cuts merged in
#: by the Claude adapter's `_merge_scoped_limits`, plus whatever the account has).
_PRIMARY_RAW_KEYS = frozenset({"five_hour", "seven_day", "seven_day_fable"})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def minutes_until(iso_str: str | None, *, now: datetime) -> int | None:
    """Whole minutes from `now` to `iso_str`, floored at 0; None if absent.

    Returns None rather than 0 for a missing or unparseable timestamp, so a
    caller can tell "resets any moment now" apart from "the API did not say"
    -- a distinction `formatting.reset_text` deliberately throws away,
    because a tooltip has nothing useful to do with it.
    """
    if not iso_str:
        return None
    try:
        reset = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return None
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    return max(0, int((reset - now).total_seconds() // 60))


def humanize_minutes(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    if minutes <= 0:
        return "any moment now"
    hours, mins = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def _window(window_id: str, entry: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    used = pct(entry)
    resets_at = (entry or {}).get("resets_at")
    minutes = minutes_until(resets_at, now=now)
    return {
        "id": window_id,
        "label": WINDOW_LABELS.get(window_id, window_id),
        "available": used is not None,
        "used_percent": used,
        # Precomputed rather than left as an exercise: the whole point of
        # this server is a caller deciding "can I afford another turn", and
        # making it do arithmetic on a percentage is an invitation to get
        # the direction wrong.
        "remaining_percent": None if used is None else round(max(0.0, 100.0 - used), 2),
        "resets_at": resets_at,
        "resets_in_minutes": minutes,
        "resets_in": humanize_minutes(minutes),
    }


def _other_limits(raw: Any, *, now: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    limits: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _PRIMARY_RAW_KEYS or not isinstance(value, dict):
            continue
        used = pct(value) if value.get("utilization") is not None else None
        if used is None:
            continue
        resets_at = value.get("resets_at")
        minutes = minutes_until(resets_at, now=now)
        limits[key] = {
            "used_percent": used,
            "resets_at": resets_at,
            "resets_in_minutes": minutes,
            "resets_in": humanize_minutes(minutes),
        }
    return limits


def _summary(windows: list[dict[str, Any]]) -> str:
    parts = [
        f"{WINDOW_SHORT.get(window['id'], window['id'])} {window['used_percent']:.0f}%"
        for window in windows
        if window["available"]
    ]
    return " · ".join(parts) if parts else "no usage data"


def build_report(
    payload: dict[str, Any],
    *,
    config_dir: str,
    source: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Shape one `api.fetch_usage` result for a tool response.

    An `error` payload is returned as `ok: false` with the message intact
    rather than raised. A model asking "how much quota is left" and getting
    "your session expired, log in again" has received a useful answer; an
    exception at the protocol layer would just look like a broken server.
    """
    now = now or _now_utc()
    account = {"config_dir": config_dir, "source": source}

    if payload.get("error") and not payload.get("meta", {}).get("from_cache"):
        return {
            "ok": False,
            "account": account,
            "error": payload["error"],
            "auth_error": bool(payload.get("auth_error")),
        }

    meta = payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {}
    windows = [_window(window_id, payload.get(window_id), now=now) for window_id in QUOTA_WINDOWS]

    return {
        "ok": True,
        "account": account,
        "windows": windows,
        # The single number a budget check should read, computed by the same
        # L2 function the enforcing hook uses. Deliberately not recomputed
        # here from `windows`: if the number shown to the agent and the
        # number that stops it were derived separately, they could disagree,
        # and the disagreement would only ever surface at 3am.
        "highest_used_percent": highest_used_percent(payload),
        "other_limits": _other_limits(payload.get("raw"), now=now),
        "summary": _summary(windows),
        "fetched_at": meta.get("fetched_at"),
        "stale": bool(meta.get("stale")),
        "rate_limited": bool(meta.get("rate_limited")),
        "warning": payload.get("error") or meta.get("warning"),
    }
