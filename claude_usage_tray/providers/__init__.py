"""claude_usage_tray.providers -- the adapter registry.

**Adding a provider is a two-step change, on purpose.** Write a module in
this package exposing a `PROVIDER` singleton that satisfies
`_types.Provider`, then add it to `_REGISTRY` below. Nothing else in the
codebase mentions a provider by name: `api.py` dispatches through
`get()`, `config.py` asks the adapter where credentials live, and the
profiles window renders whatever `all_providers()` returns. An `opencode` or
`cursor` adapter is that one file plus that one line.

`DEFAULT_ID` is `"claude"` and must stay so: it is the value a profile
written before the `provider` field existed migrates to (see
`config.SCHEMA_VERSION` 3), so changing it would silently repoint every
pre-existing profile at a different service.

`get()` never raises. A `config.json` naming a provider this build does not
have -- an older exe reading a newer config, or a provider removed between
versions -- degrades to the default rather than crashing the tray on
startup, which is the same tolerance `config.py` already applies to an
unreadable `settings` blob.
"""

from __future__ import annotations

from pathlib import Path

from ._claude import PROVIDER as CLAUDE
from ._codex import PROVIDER as CODEX
from ._types import Provider, UsageRequest, epoch_to_iso, quota_entry

#: Registration order is display order in the profiles window.
_REGISTRY: dict[str, Provider] = {
    CLAUDE.id: CLAUDE,
    CODEX.id: CODEX,
}

DEFAULT_ID = CLAUDE.id


def all_providers() -> tuple[Provider, ...]:
    """Every registered adapter, in registration order."""
    return tuple(_REGISTRY.values())


def provider_ids() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def get(provider_id: str | None) -> Provider:
    """The adapter for `provider_id`, falling back to the default.

    Tolerant by design -- see the module docstring.
    """
    if provider_id:
        found = _REGISTRY.get(provider_id)
        if found is not None:
            return found
    return _REGISTRY[DEFAULT_ID]


def is_known(provider_id: str | None) -> bool:
    """Whether `provider_id` names a registered adapter. Distinct from
    `get()` returning something: callers that want to *report* an unknown
    provider (rather than silently substitute one) need to tell the two
    apart."""
    return bool(provider_id) and provider_id in _REGISTRY


def display_name(provider_id: str | None) -> str:
    return get(provider_id).display_name


def credentials_path(provider_id: str | None, config_dir: Path) -> Path:
    """Where `provider_id` keeps its session inside `config_dir`."""
    return config_dir / get(provider_id).credentials_filename


def windows_for(provider_id: str | None) -> tuple[str, ...]:
    """The quota windows `provider_id` can report -- a subset of
    `quotas.QUOTA_WINDOWS`. The settings window renders threshold fields for
    exactly these, so a Codex profile shows no Fable row."""
    return get(provider_id).windows


def detect_installed() -> tuple[Provider, ...]:
    """Every provider whose default config directory exists on this machine.

    Drives first-run seeding and the schema-3 migration: a user running both
    CLIs gets both profiles without configuring anything, which is the point
    of the multi-provider work. Existence of the directory, not of a valid
    session -- a signed-out account is a profile that shows "sign in", not a
    profile that should be missing.
    """
    return tuple(p for p in _REGISTRY.values() if p.default_config_dir().is_dir())


__all__ = [
    "DEFAULT_ID",
    "Provider",
    "UsageRequest",
    "all_providers",
    "credentials_path",
    "detect_installed",
    "display_name",
    "epoch_to_iso",
    "get",
    "is_known",
    "provider_ids",
    "quota_entry",
    "windows_for",
]
