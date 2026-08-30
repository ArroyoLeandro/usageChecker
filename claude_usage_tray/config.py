"""Carga configuraciones de perfiles para Claude Usage."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import jsonstore, paths

SCHEMA_VERSION = 2
DEFAULT_PROFILE_NAME = "Perfil actual"


@dataclass(frozen=True)
class ClaudeProfile:
    """A user-configured Claude account/config-dir pair.

    `id` is a stable, opaque identity assigned once at creation (a uuid4).
    It is never derived from `name` or `config_dir` and never changes when
    either is edited -- see profile-management spec, "Stable Opaque
    Identity". There is no reserved id and no "principal" concept: every
    profile, including a first-run seeded one, is an ordinary entry.
    """

    id: str
    name: str
    config_dir: Path

    @property
    def credentials_path(self) -> Path:
        return self.config_dir / ".credentials.json"

    @property
    def is_ambient_default(self) -> bool:
        """Whether this profile points at the machine's own Claude directory
        (as opposed to a second account, a copy, or a mounted WSL path).

        The condition `supports_refresh` has always tested, given its own
        name because a second caller now needs it for an unrelated reason:
        on macOS the credentials live in a Keychain entry that belongs to
        whichever account Claude Code itself is logged into, so only the
        ambient default profile may read it. Two callers asking the same
        question through one property beats two copies of `_paths_equal`.
        """
        return _paths_equal(self.config_dir, default_claude_dir())

    @property
    def supports_refresh(self) -> bool:
        """Derived, never stored. True iff `config_dir` is the ambient
        default Claude directory -- see profile-management spec,
        "supports_refresh Is Derived". Computed fresh on every read so an
        edited `config_dir` immediately reflects the correct value; the old
        code hardcoded this to `True` for the one profile whose `config_dir`
        could never change, which made the hardcode tautologically safe.
        Editable paths (this slice's feature) make that assumption false.
        """
        return self.is_ambient_default


@dataclass(frozen=True)
class AppConfig:
    """The whole of `config.json`: the profile list and the settings blob.

    `settings` is carried **raw and uninterpreted** on purpose. `settings.py`
    owns that schema, but `config` and `settings` are both L2, and the
    layering gate forbids an edge between same-layer modules -- so the
    composition root (`app.py`) maps raw <-> `Settings` and this module just
    round-trips the mapping. Storing it rather than dropping it is what makes
    "settings survive a profile edit" true by construction: every caller that
    edits profiles does so with `dataclasses.replace`, and settings ride
    along untouched (user-settings spec, "Settings Live in config.json").
    """

    profiles: list[ClaudeProfile] = field(default_factory=list)
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigCorrupted:
    """Notice: `config.json` failed to parse or validate and was quarantined.

    `quarantined_to` is `None` only when quarantine itself failed (locked
    file, denied permission) -- in that case `ConfigLoad.read_only` is
    `True` and the app must not write `config.json` this session.
    """

    quarantined_to: Path | None


Notice = ConfigCorrupted  # union grows as later slices add load-time conditions


@dataclass(frozen=True)
class ConfigLoad:
    """Result of `load_config()`. Never touches UI -- `notices` are data for
    the caller to show once a window/root exists (see design's "Load-time
    notices are queued, not shown" decision).
    """

    config: AppConfig
    notices: list[Notice]
    read_only: bool


class DuplicateConfigDirError(ValueError):
    """Raised by `add_profile`/`edit_profile` when the normalized
    `config_dir` collides with another profile's -- config_dir uniqueness
    is a validation rule, never an identity (see profile-management spec).
    """

    def __init__(self, config_dir: Path):
        super().__init__(f"config_dir already in use by another profile: {config_dir}")
        self.config_dir = config_dir


class ProfileNotFoundError(KeyError):
    """Raised by `edit_profile` when no profile with the given id exists."""


def default_claude_dir() -> Path:
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()
    return Path.home() / ".claude"


def normalize_config_dir(value: str | None) -> Path | None:
    """Expand `~` and, if the user pasted a path to the credentials file
    itself, fall back to its parent directory.
    """
    if not value:
        return None
    path = Path(value).expanduser()
    if path.name.lower() == ".credentials.json":
        return path.parent
    return path


def _paths_equal(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left.expanduser()).lower() == str(right.expanduser()).lower()


def _normalized_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).lower()
    except OSError:
        return str(path.expanduser()).lower()


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


def seed_profile() -> ClaudeProfile | None:
    """Attempt to auto-detect the ambient Claude config directory and build
    a candidate profile from it.

    Returns `None` when no such directory is detectable -- callers
    (`load_config`) must handle that by starting with an empty profile
    list, without error, per profile-management spec's First-Run Seed
    requirement ("if found" is a real condition, not always-true: the old
    `default_profile()` built a profile unconditionally, even when
    `~/.claude` did not exist).
    """
    config_dir = default_claude_dir()
    if not config_dir.is_dir():
        return None
    return ClaudeProfile(
        id=str(uuid.uuid4()),
        name=_main_profile_name(config_dir),
        config_dir=config_dir,
    )


class _SchemaInvalid(Exception):
    """Internal: raw JSON parsed but does not match the expected shape.

    Handled identically to a JSON parse failure by `load_config` -- the
    config-store spec requires quarantine+notice for either case ("fails to
    parse as JSON or fails schema validation").
    """


def _parse_app_config(raw: Any) -> AppConfig:
    if not isinstance(raw, dict):
        raise _SchemaInvalid("config root is not an object")

    items = raw.get("profiles")
    if not isinstance(items, list):
        raise _SchemaInvalid("'profiles' is missing or not a list")

    profiles: list[ClaudeProfile] = []
    for item in items:
        if not isinstance(item, dict):
            raise _SchemaInvalid("profile entry is not an object")
        profile_id = item.get("id")
        name = item.get("name")
        raw_config_dir = item.get("config_dir")
        if not isinstance(profile_id, str) or not profile_id:
            raise _SchemaInvalid("profile entry missing a valid 'id'")
        if not isinstance(name, str):
            raise _SchemaInvalid("profile entry missing a valid 'name'")
        if not isinstance(raw_config_dir, str) or not raw_config_dir:
            raise _SchemaInvalid("profile entry missing a valid 'config_dir'")
        profiles.append(
            ClaudeProfile(
                id=profile_id,
                name=name,
                config_dir=Path(raw_config_dir).expanduser(),
            )
        )

    # Settings are read tolerantly, never strictly: a `settings` key that is
    # absent (a Slice-1 config.json) or the wrong type yields an empty blob,
    # which `settings.from_raw` turns into the documented defaults. It is
    # deliberately NOT a `_SchemaInvalid` -- that would quarantine an
    # irreplaceable profile list over a recoverable preference, inverting the
    # cost the two-file split exists to manage. Profiles stay strict above.
    raw_settings = raw.get("settings")
    settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
    return AppConfig(profiles=profiles, settings=settings)


def _seed_and_maybe_persist(settings: Mapping[str, Any] | None = None) -> ConfigLoad:
    """Re-seed the profile list by auto-detection, preserving `settings`.

    `settings` is carried through because re-seeding is triggered by an empty
    *profile list*, which says nothing about the user's preferences: deleting
    the last profile must not silently reset their theme and font size. The
    corruption path passes nothing, and rightly so -- there the settings are
    unreadable, not merely unaccompanied.
    """
    candidate = seed_profile()
    config = AppConfig(
        profiles=[candidate] if candidate is not None else [],
        settings=dict(settings) if settings else {},
    )
    if config.profiles:
        save_config(config)
    return ConfigLoad(config=config, notices=[], read_only=False)


def _recover_from_corruption(path: Path) -> ConfigLoad:
    try:
        quarantined_to = jsonstore.quarantine(path)
    except OSError:
        # Could not vacate the corrupt file's path -- refuse to write this
        # session, per the design's "if quarantine itself fails" decision.
        return ConfigLoad(
            config=AppConfig(profiles=[]),
            notices=[ConfigCorrupted(quarantined_to=None)],
            read_only=True,
        )

    seeded = _seed_and_maybe_persist()
    return ConfigLoad(
        config=seeded.config,
        notices=[ConfigCorrupted(quarantined_to=quarantined_to), *seeded.notices],
        read_only=seeded.read_only,
    )


def load_config() -> ConfigLoad:
    """Load `config.json` from its single canonical location.

    Never touches UI -- returns `notices` for the caller to surface once a
    window exists. A corrupt or schema-invalid file is quarantined (renamed,
    bytes preserved) before any reseed or write is attempted; a genuinely
    empty or absent file re-seeds by auto-detection.
    """
    path = paths.config_file()
    try:
        raw = jsonstore.read_json(path)
    except jsonstore.CorruptFile:
        return _recover_from_corruption(path)

    if raw is None:
        return _seed_and_maybe_persist()

    try:
        config = _parse_app_config(raw)
    except _SchemaInvalid:
        return _recover_from_corruption(path)

    if not config.profiles:
        return _seed_and_maybe_persist(config.settings)

    return ConfigLoad(config=config, notices=[], read_only=False)


def save_config(config: AppConfig) -> None:
    """Persist `config` atomically. Writes only `id`/`name`/`config_dir` per
    profile -- `supports_refresh` is derived and MUST NOT be persisted.

    The `settings` blob is written back exactly as carried, in the same
    single atomic write as the profiles: one file, one swap, so a profile
    edit and the settings it rode in with can never disagree on disk.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "profiles": [
            {"id": profile.id, "name": profile.name, "config_dir": str(profile.config_dir)}
            for profile in config.profiles
        ],
        "settings": dict(config.settings),
    }
    jsonstore.write_json_atomic(paths.config_file(), payload)


def add_profile(profiles: list[ClaudeProfile], *, name: str, config_dir: Path) -> list[ClaudeProfile]:
    """Return a new profile list with a freshly-created profile appended.

    Raises `DuplicateConfigDirError` if `config_dir` (normalized,
    case-insensitive, resolved) collides with an existing profile's.
    """
    normalized = normalize_config_dir(str(config_dir))
    if normalized is None:
        raise ValueError("config_dir is required")
    _assert_unique(profiles, normalized)
    new_profile = ClaudeProfile(
        id=str(uuid.uuid4()),
        name=name.strip() or DEFAULT_PROFILE_NAME,
        config_dir=normalized,
    )
    return [*profiles, new_profile]


def edit_profile(
    profiles: list[ClaudeProfile],
    profile_id: str,
    *,
    name: str | None = None,
    config_dir: Path | None = None,
) -> list[ClaudeProfile]:
    """Return a new profile list with the entry matching `profile_id`
    updated in place (position preserved). `id` never changes.

    Raises `ProfileNotFoundError` if no profile has `profile_id`, and
    `DuplicateConfigDirError` if the new `config_dir` collides with another
    profile's.
    """
    normalized_dir: Path | None = None
    if config_dir is not None:
        normalized_dir = normalize_config_dir(str(config_dir))
        if normalized_dir is None:
            raise ValueError("config_dir is required")
        _assert_unique(profiles, normalized_dir, exclude_id=profile_id)

    updated: list[ClaudeProfile] = []
    found = False
    for profile in profiles:
        if profile.id != profile_id:
            updated.append(profile)
            continue
        found = True
        updated.append(
            ClaudeProfile(
                id=profile.id,
                name=(name.strip() or profile.name) if name is not None else profile.name,
                config_dir=normalized_dir if normalized_dir is not None else profile.config_dir,
            )
        )
    if not found:
        raise ProfileNotFoundError(profile_id)
    return updated


def delete_profile(profiles: list[ClaudeProfile], profile_id: str) -> list[ClaudeProfile]:
    """Return a new profile list with `profile_id` removed. Deleting the
    last remaining profile is not an error -- it yields an empty list."""
    return [profile for profile in profiles if profile.id != profile_id]


def reorder_profiles(profiles: list[ClaudeProfile], ordered_ids: list[str]) -> list[ClaudeProfile]:
    """Return the same profiles reordered to match `ordered_ids`. Array
    order IS display order -- no index or id is exempt from reordering.

    Raises `ValueError` if `ordered_ids` is not exactly a permutation of the
    current profiles' ids.
    """
    by_id = {profile.id: profile for profile in profiles}
    if set(ordered_ids) != set(by_id) or len(ordered_ids) != len(profiles):
        raise ValueError("ordered_ids must be a permutation of the current profile ids")
    return [by_id[profile_id] for profile_id in ordered_ids]


def _assert_unique(profiles: list[ClaudeProfile], config_dir: Path, *, exclude_id: str | None = None) -> None:
    target_key = _normalized_key(config_dir)
    for profile in profiles:
        if profile.id == exclude_id:
            continue
        if _normalized_key(profile.config_dir) == target_key:
            raise DuplicateConfigDirError(config_dir)
