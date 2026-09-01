"""Consulta el uso de cada proveedor configurado (Claude Code, Codex, ...).

This module owns everything that is the *same* for every provider: the
per-profile response cache, the minimum fetch interval, 429 backoff with
`Retry-After`, the 401-then-refresh-once retry, and the shape of the payload
handed to the UI. Everything that differs -- the endpoint, the headers, where
credentials live, how a response maps onto quota windows -- belongs to an
adapter in `providers/` and reaches this module only through
`profile.adapter`.

That split is why adding a provider does not touch this file. It is also why
the retry/backoff state stays keyed by config directory: two providers are
two directories, so their rate limits can never be confused for each other's.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from .config import ClaudeProfile, seed_profile

MIN_FETCH_INTERVAL_SECONDS = 45
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 30 * 60
_STATE_LOCK = threading.Lock()
_STATE_BY_PROFILE: dict[str, dict[str, Any]] = {}


def _profile_key(profile: ClaudeProfile) -> str:
    return str(profile.config_dir).lower()


def _profile_state(profile: ClaudeProfile) -> dict[str, Any]:
    with _STATE_LOCK:
        return _STATE_BY_PROFILE.setdefault(
            _profile_key(profile),
            {
                "cached_payload": None,
                "last_success_at": None,
                "last_attempt_at": None,
                "consecutive_429": 0,
                "next_retry_at": None,
            },
        )


def read_access_token(profile: ClaudeProfile | None = None) -> str | None:
    """The stored access token for `profile`, via its adapter.

    Kept as a public function because callers outside this module use it as a
    cheap "is there a session at all?" probe. It no longer knows the shape of
    any credentials file -- `providers.Provider.access_token` does.
    """
    profile = profile or seed_profile()
    if profile is None:
        return None
    reader = getattr(profile.adapter, "access_token", None)
    if reader is None:
        return None
    try:
        return reader(profile.config_dir)
    except Exception:
        return None


def refresh_token(profile: ClaudeProfile | None = None) -> bool:
    """Ask the adapter to renew an expired session in place.

    The pre-adapter version gated this on `profile.supports_refresh` and then
    shelled out to `claude update`. Both halves moved into the adapter: only
    it knows whether refreshing is even possible for a given directory, and
    the mechanism differs per provider (a subprocess for Claude, an OAuth
    round trip for Codex).
    """
    profile = profile or seed_profile()
    if profile is None:
        return False
    try:
        return bool(profile.adapter.refresh_credentials(profile.config_dir))
    except Exception:
        return False


def _usage_request(profile: ClaudeProfile):
    try:
        return profile.adapter.usage_request(profile.config_dir)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _format_retry_in(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, rem_seconds = divmod(seconds, 60)
    hours, rem_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {rem_minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{rem_seconds} s"


def _seconds_until(target: datetime | None, *, now: datetime | None = None) -> int:
    if target is None:
        return 0
    now = now or _now_utc()
    return max(0, int((target - now).total_seconds()))


def _copy_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return dict(payload)


def _with_meta(payload: dict[str, Any], **meta: Any) -> dict[str, Any]:
    result = dict(payload)
    result["meta"] = {**result.get("meta", {}), **meta}
    return result


def _recent_cache_payload(profile: ClaudeProfile, now: datetime) -> dict[str, Any] | None:
    state = _profile_state(profile)
    with _STATE_LOCK:
        payload = _copy_payload(state["cached_payload"])
        last_attempt_at = state["last_attempt_at"]
    if payload is None or last_attempt_at is None:
        return None
    age_seconds = max(0, int((now - last_attempt_at).total_seconds()))
    if age_seconds >= MIN_FETCH_INTERVAL_SECONDS:
        return None
    fetched_at = payload.get("meta", {}).get("fetched_at")
    return _with_meta(
        payload,
        cache_age_seconds=age_seconds,
        fetched_at=fetched_at,
        from_cache=True,
        next_retry_in_seconds=0,
        next_retry_text="",
        stale=False,
    )


def _rate_limit_payload(profile: ClaudeProfile, now: datetime) -> dict[str, Any] | None:
    state = _profile_state(profile)
    with _STATE_LOCK:
        next_retry_at = state["next_retry_at"]
        cached_payload = _copy_payload(state["cached_payload"])
        last_success_at = state["last_success_at"]
    if next_retry_at is None or now >= next_retry_at:
        return None
    retry_in_seconds = _seconds_until(next_retry_at, now=now)
    retry_text = _format_retry_in(retry_in_seconds)
    message = f"Ya se alcanzo el limite por ahora. Voy a intentarlo de nuevo en {retry_text}."
    if cached_payload is not None and last_success_at is not None:
        fetched_at = cached_payload.get("meta", {}).get("fetched_at", last_success_at.isoformat())
        return _with_meta(
            cached_payload,
            warning=message,
            fetched_at=fetched_at,
            from_cache=True,
            rate_limited=True,
            stale=True,
            next_retry_at=next_retry_at.isoformat(),
            next_retry_in_seconds=retry_in_seconds,
            next_retry_text=retry_text,
        )
    return {
        "error": message,
        "meta": {
            "fetched_at": None,
            "from_cache": True,
            "rate_limited": True,
            "stale": True,
            "next_retry_at": next_retry_at.isoformat(),
            "next_retry_in_seconds": retry_in_seconds,
            "next_retry_text": retry_text,
        },
    }


def _parse_retry_after_seconds(value: str | None, *, now: datetime) -> int | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        return max(0, int(raw))
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((parsed - now).total_seconds()))
    except Exception:
        return None


def _store_success(profile: ClaudeProfile, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    final_payload = _with_meta(
        payload,
        fetched_at=now.isoformat(),
        from_cache=False,
        rate_limited=False,
        stale=False,
        next_retry_in_seconds=0,
        next_retry_text="",
    )
    state = _profile_state(profile)
    with _STATE_LOCK:
        state["cached_payload"] = dict(final_payload)
        state["last_success_at"] = now
        state["last_attempt_at"] = now
        state["consecutive_429"] = 0
        state["next_retry_at"] = None
    return final_payload


def _store_429(profile: ClaudeProfile, response: requests.Response | None, now: datetime) -> dict[str, Any]:
    header_seconds = _parse_retry_after_seconds(
        response.headers.get("Retry-After") if response is not None else None,
        now=now,
    )
    state = _profile_state(profile)
    with _STATE_LOCK:
        state["last_attempt_at"] = now
        state["consecutive_429"] += 1
        backoff_seconds = min(
            BACKOFF_MAX_SECONDS,
            BACKOFF_BASE_SECONDS * (2 ** (state["consecutive_429"] - 1)),
        )
        retry_in_seconds = max(header_seconds or 0, backoff_seconds)
        state["next_retry_at"] = now + timedelta(seconds=retry_in_seconds)
    return _rate_limit_payload(profile, now) or {
        "error": "Ya se alcanzo el limite por ahora.",
        "meta": {
            "fetched_at": None,
            "from_cache": False,
            "rate_limited": True,
            "stale": True,
            "next_retry_in_seconds": retry_in_seconds,
            "next_retry_text": _format_retry_in(retry_in_seconds),
        },
    }


def fetch_usage(
    profile: ClaudeProfile | None = None,
    *,
    try_refresh: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """One profile's usage, normalized by its adapter.

    The control flow -- cache, backoff, refresh-once on 401 -- is identical
    for every provider and stays here; only the request and the response
    mapping are delegated. Error copy names the provider (`Abri OpenAI Codex`
    rather than a hardcoded `Abri Claude`) so a signed-out Codex profile does
    not tell the user to sign in to the wrong application.
    """
    profile = profile or seed_profile()
    if profile is None:
        return {
            "error": "No se detecto ninguna carpeta de configuracion. Agrega un perfil manualmente.",
            "auth_error": True,
        }

    adapter = profile.adapter
    request = _usage_request(profile)
    if request is None:
        return {
            "error": (
                f"No hay una sesion iniciada para {profile.name}. "
                f"Abri {adapter.display_name} e inicia sesion para ver el uso."
            ),
            "auth_error": True,
        }

    now = _now_utc()
    rate_limited = _rate_limit_payload(profile, now)
    if rate_limited is not None:
        return rate_limited
    if not force:
        cached = _recent_cache_payload(profile, now)
        if cached is not None:
            return cached

    try:
        resp = requests.get(request.url, headers=request.headers, timeout=10)
        if resp.status_code == 401 and try_refresh and refresh_token(profile):
            # Re-ask the adapter rather than reusing `request`: a refresh
            # rewrites the credentials file, so the new token is only visible
            # through a fresh read.
            retried = _usage_request(profile)
            if retried is not None:
                resp = requests.get(retried.url, headers=retried.headers, timeout=10)

        resp.raise_for_status()
        payload = adapter.normalize(resp.json())
        return _store_success(profile, payload, now)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 401:
            return {
                "error": (
                    f"La sesion vencio. Abri {adapter.display_name} y volve a "
                    "iniciar sesion para actualizar el uso."
                ),
                "auth_error": True,
            }
        if code == 429:
            return _store_429(profile, exc.response, now)
        return {"error": "No se pudo actualizar el uso en este momento."}
    except requests.RequestException:
        return {"error": "No se pudo conectar para actualizar el uso."}
    except Exception:
        return {"error": "No se pudo leer el uso"}


def fetch_usage_bundle(
    profiles: list[ClaudeProfile] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if profiles is None:
        fallback = seed_profile()
        active_profiles = [fallback] if fallback is not None else []
    else:
        active_profiles = profiles
    items: list[dict[str, Any]] = []
    for profile in active_profiles:
        items.append({"profile": profile, "data": fetch_usage(profile, force=force)})
    primary = items[0]["data"] if items else {"error": "Todavia no agregaste ninguna carpeta"}
    return {
        "profiles": items,
        "primary": primary,
    }
