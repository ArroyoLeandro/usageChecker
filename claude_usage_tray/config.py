"""Carga configuraciones de perfiles para Claude Usage."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "claude_usage_profiles.json"
DEFAULT_PROFILE_NAME = "Perfil actual"


@dataclass(frozen=True)
class ClaudeProfile:
    id: str
    name: str
    config_dir: Path
    supports_refresh: bool = False

    @property
    def credentials_path(self) -> Path:
        return self.config_dir / ".credentials.json"


def default_claude_dir() -> Path:
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()
    return Path.home() / ".claude"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _config_locations() -> list[Path]:
    return [
        _app_dir() / CONFIG_FILENAME,
        default_claude_dir() / CONFIG_FILENAME,
    ]


def _config_path_for_write() -> Path:
    for path in _config_locations():
        if path.exists():
            return path
    return _app_dir() / CONFIG_FILENAME


def _normalize_config_dir(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.name.lower() == ".credentials.json":
        return path.parent
    return path


def normalize_config_dir(value: str | None) -> Path | None:
    return _normalize_config_dir(value)


def _profile_id_from_name(name: str, index: int) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in name).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or f"perfil-{index}"


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left.expanduser()).lower() == str(right.expanduser()).lower()


def _main_profile_name(config_dir: Path) -> str:
    credentials_path = config_dir / ".credentials.json"
    try:
        if credentials_path.exists():
            creds = json.loads(credentials_path.read_text(encoding="utf-8"))
            oauth = creds.get("claudeAiOauth", {})
            for key in ("email", "username", "name", "displayName"):
                value = oauth.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    for key in ("CLAUDE_PROFILE_NAME", "USERNAME", "USER"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    home_name = Path.home().name.strip()
    if home_name:
        return home_name

    return DEFAULT_PROFILE_NAME


def default_profile() -> ClaudeProfile:
    config_dir = default_claude_dir()
    return ClaudeProfile(
        id="principal",
        name=_main_profile_name(config_dir),
        config_dir=config_dir,
        supports_refresh=True,
    )


def _dedupe_profiles(profiles: list[ClaudeProfile]) -> list[ClaudeProfile]:
    unique: list[ClaudeProfile] = []
    seen: set[str] = set()
    for profile in profiles:
        key = str(profile.config_dir).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(profile)
    return unique


def load_profiles() -> list[ClaudeProfile]:
    base = default_profile()
    config_data: dict[str, object] | None = None
    for path in _config_locations():
        try:
            if path.exists():
                config_data = json.loads(path.read_text(encoding="utf-8"))
                break
        except (OSError, json.JSONDecodeError):
            config_data = None
            break

    if not isinstance(config_data, dict):
        return [base]

    items = config_data.get("profiles")
    if not isinstance(items, list):
        return [base]

    profiles = [base]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        config_dir = _normalize_config_dir(str(item.get("config_dir") or "").strip())
        if config_dir is None:
            continue
        name = str(item.get("name") or "").strip() or f"Perfil {index}"
        profile = ClaudeProfile(
            id=_profile_id_from_name(name, index),
            name=name,
            config_dir=config_dir,
            supports_refresh=config_dir.resolve() == base.config_dir.resolve(),
        )
        profiles.append(profile)
    return _dedupe_profiles(profiles)


def save_profiles(profiles: list[ClaudeProfile]) -> None:
    base = default_profile()
    payload_profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    for profile in profiles:
        config_dir = _normalize_config_dir(str(profile.config_dir))
        if config_dir is None or _same_path(config_dir, base.config_dir):
            continue
        key = str(config_dir).lower()
        if key in seen:
            continue
        seen.add(key)
        payload_profiles.append(
            {
                "name": profile.name.strip() or f"Perfil {len(payload_profiles) + 1}",
                "config_dir": str(config_dir),
            }
        )

    path = _config_path_for_write()
    existing: dict[str, object] = {}
    try:
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                existing = current
    except (OSError, json.JSONDecodeError):
        existing = {}

    existing["profiles"] = payload_profiles
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
