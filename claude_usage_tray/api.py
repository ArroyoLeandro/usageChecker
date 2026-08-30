"""Lee credenciales locales de Claude Code y consulta el uso en Anthropic."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

from . import platform
from .config import ClaudeProfile, seed_profile

API_URL_USAGE = "https://api.anthropic.com/api/oauth/usage"
MIN_FETCH_INTERVAL_SECONDS = 45
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 30 * 60
FALLBACK_USER_AGENT = "claude-code/2.1.201"
#: The Keychain service name Claude Code stores its credentials JSON under on
#: macOS. Fixed by Claude Code, not by us -- it takes no config-dir suffix,
#: which is why only the ambient default profile can read it.
KEYCHAIN_SERVICE = "Claude Code-credentials"
_STATE_LOCK = threading.Lock()
_STATE_BY_PROFILE: dict[str, dict[str, Any]] = {}


def _claude_cli_path() -> Path | None:
    import shutil

    found = shutil.which("claude")
    if found:
        path = Path(found)
        if path.suffix.lower() == ".ps1":
            for ext in (".cmd", ".exe"):
                alt = path.with_suffix(ext)
                if alt.is_file():
                    return alt
        return path

    appdata = os.environ.get("APPDATA")
    if appdata:
        for name in ("claude.cmd", "claude.exe"):
            candidate = Path(appdata) / "npm" / name
            if candidate.is_file():
                return candidate
    return None


def _cli_version() -> str:
    cli = _claude_cli_path()
    if not cli or not cli.is_file():
        return FALLBACK_USER_AGENT.removeprefix("claude-code/")

    try:
        proc = subprocess.run(
            [str(cli), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=platform.subprocess_flags(),
        )
        match = re.match(r"(\d+\.\d+\.\d+)", proc.stdout.strip())
        return match.group(1) if match else FALLBACK_USER_AGENT.removeprefix("claude-code/")
    except Exception:
        return FALLBACK_USER_AGENT.removeprefix("claude-code/")


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


def _read_credentials_blob(profile: ClaudeProfile) -> str | None:
    """The raw credentials JSON for `profile`, from wherever this OS keeps it.

    The file comes first and always wins: it is per-profile, so a user who
    points a profile at a copied `.claude` directory gets that profile's
    token, and a WSL directory mounted on a Mac keeps working.

    The Keychain is the fallback, and only for the ambient default profile.
    Claude Code on macOS writes no `.credentials.json` at all -- the blob
    lives in the login Keychain under a single, fixed service name, with no
    room for a config-dir qualifier. So it can only answer for the one
    profile that *is* the ambient default; letting any other profile fall
    back to it would make every macOS profile silently report the default
    account's usage, which is worse than reporting no session at all.
    """
    try:
        if profile.credentials_path.exists():
            return profile.credentials_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if profile.is_ambient_default:
        return platform.read_secret(KEYCHAIN_SERVICE)
    return None


def read_access_token(profile: ClaudeProfile | None = None) -> str | None:
    profile = profile or seed_profile()
    if profile is None:
        return None
    blob = _read_credentials_blob(profile)
    if not blob:
        return None
    try:
        creds = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(creds, dict):
        return None
    return creds.get("claudeAiOauth", {}).get("accessToken") or None


def refresh_token(profile: ClaudeProfile | None = None) -> bool:
    profile = profile or seed_profile()
    if profile is None or not profile.supports_refresh:
        return False
    cli = _claude_cli_path()
    if not cli or not cli.is_file():
        return False
    try:
        proc = subprocess.run(
            [str(cli), "update"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=platform.subprocess_flags(),
        )
        return proc.returncode == 0
    except Exception:
        return False


def _headers(profile: ClaudeProfile | None = None) -> dict[str, str] | None:
    token = read_access_token(profile)
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": f"claude-code/{_cli_version()}",
        "anthropic-beta": "oauth-2025-04-20",
    }


def _quota(data: dict[str, Any], field: str) -> dict[str, Any] | None:
    entry = data.get(field)
    if not isinstance(entry, dict):
        return None
    if entry.get("utilization") is None:
        return None
    return entry


def _model_slug(display_name: str) -> str:
    cleaned = "".join(char if char.isalnum() else " " for char in display_name.lower())
    return "_".join(cleaned.split())


def _merge_scoped_limits(data: dict[str, Any]) -> dict[str, Any]:
    limits = data.get("limits")
    if not isinstance(limits, list):
        return data

    reset_to_field: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict) and value.get("utilization") is not None:
            resets_at = value.get("resets_at")
            if resets_at:
                reset_to_field.setdefault(resets_at, key)

    group_prefix: dict[str, str] = {}
    for limit in limits:
        if not isinstance(limit, dict) or limit.get("scope"):
            continue
        group = limit.get("group")
        resets_at = limit.get("resets_at")
        if group and resets_at and resets_at in reset_to_field:
            group_prefix.setdefault(group, reset_to_field[resets_at])

    merged = dict(data)
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        model = (limit.get("scope") or {}).get("model") or {}
        display_name = model.get("display_name")
        prefix = group_prefix.get(limit.get("group"))
        if not display_name or not prefix:
            continue

        field = f"{prefix}_{_model_slug(display_name)}"
        if merged.get(field) is not None:
            continue
        merged[field] = {
            "utilization": float(limit.get("percent") or 0),
            "resets_at": limit.get("resets_at"),
        }

    return merged


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
    profile = profile or seed_profile()
    if profile is None:
        return {
            "error": "No se detecto ninguna carpeta de configuracion de Claude. Agrega un perfil manualmente.",
            "auth_error": True,
        }
    headers = _headers(profile)
    if not headers:
        return {
            "error": f"No hay una sesion iniciada para {profile.name}. Abri Claude e inicia sesion para ver el uso.",
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
        resp = requests.get(API_URL_USAGE, headers=headers, timeout=10)
        if resp.status_code == 401 and try_refresh and refresh_token(profile):
            headers = _headers(profile)
            if headers:
                resp = requests.get(API_URL_USAGE, headers=headers, timeout=10)

        resp.raise_for_status()
        data = _merge_scoped_limits(resp.json())
        session = _quota(data, "five_hour")
        weekly = _quota(data, "seven_day")
        weekly_fable = _quota(data, "seven_day_fable")
        return _store_success(
            profile,
            {
                "session": session,
                "weekly": weekly,
                "weekly_fable": weekly_fable,
                "raw": data,
            },
            now,
        )
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 401:
            return {
                "error": "La sesion vencio. Abri Claude y volve a iniciar sesion para actualizar el uso.",
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
