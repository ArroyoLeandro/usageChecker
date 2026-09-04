"""claude_usage_tray.providers._types -- the adapter contract.

One provider = one CLI whose quota the tray can report on (Claude Code,
OpenAI Codex, and whatever comes next). Everything a provider must answer
lives in this Protocol, so "add support for opencode" is a new module in
this package plus one line in `__init__._REGISTRY` -- never an edit to
`api.py`, `config.py` or the UI.

**Why this package is L1, and stdlib-only.** `config.py` (L2) has to ask a
provider where its credentials live in order to build a profile at all, so
the contract cannot sit at `api.py`'s layer (L3). That in turn forbids
`requests` here (`tests/test_import_hygiene.py`): the network call belongs
to `api.py`, which is why `usage_request` returns a *description* of the
request rather than performing one. `refresh_credentials` is the one
exception and it is deliberate -- refreshing is a rare, provider-shaped
side effect (a subprocess for Claude, an OAuth round trip for Codex) with
no shared shape worth inventing a second seam for, so adapters do it
themselves using the standard library only.

**Why quota windows are plain `str` here rather than `quotas.QuotaWindow`.**
`quotas.py` is also L1, and the layering gate forbids same-layer edges. It
costs nothing: `quotas.quota_label` already accepts a plain `str` precisely
so an unknown window degrades to its own id instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageRequest:
    """A GET the caller should perform to read this provider's usage.

    Returned instead of performed so that this package never imports
    `requests` -- see the module docstring. `api.py` owns the timeout, the
    retry/backoff state and the 401 handling, and it owns them once for
    every provider rather than once per adapter.
    """

    url: str
    headers: dict[str, str]


@runtime_checkable
class Provider(Protocol):
    """What the rest of the app may ask of a provider.

    Implementations are module-level singletons (see `_claude.py` /
    `_codex.py`): they hold no per-profile state, and the one piece of
    mutable state in the fetch path -- the cache and 429 backoff -- already
    lives in `api.py`, keyed by config directory.
    """

    #: Stable, persisted in `config.json`. Never renamed once shipped.
    id: str

    #: What the user reads in the profiles window and the usage popup.
    display_name: str

    #: File inside `config_dir` that holds the session, used both to build
    #: `credentials_path` and to tell "signed out" from "wrong folder".
    credentials_filename: str

    #: The subset of `quotas.QUOTA_WINDOWS` this provider can report. The
    #: settings window renders threshold fields for exactly these, so a
    #: Codex profile shows no Fable row.
    windows: tuple[str, ...]

    #: Field label and helper copy for the add/edit profile form.
    config_dir_label: str
    config_dir_hint: str

    def default_config_dir(self) -> Path:
        """Where this provider stores its config for the current user."""
        ...

    def account_label(self, config_dir: Path) -> str | None:
        """A human name for the signed-in account (email or username), or
        `None` when it cannot be read. Used to seed a profile's name."""
        ...

    def usage_request(self, config_dir: Path) -> UsageRequest | None:
        """The usage GET, or `None` when there is no usable session."""
        ...

    def normalize(self, raw: Any) -> dict[str, Any]:
        """Map this provider's response onto the shared payload shape:
        `{"session": entry|None, "weekly": entry|None,
          "weekly_fable": entry|None, "raw": <original>}`, where an entry is
        `{"utilization": float, "resets_at": isoformat str}`.

        Every consumer downstream -- tooltip, popup, alerts, budget, the MCP
        report -- already treats a `None` window as "this account has no such
        quota", so a provider simply omits what it does not have."""
        ...

    def refresh_credentials(self, config_dir: Path) -> bool:
        """Attempt to renew an expired session in place. `True` if the
        stored credentials were refreshed and are worth re-reading."""
        ...


def quota_entry(percent: Any, resets_at: str | None) -> dict[str, Any] | None:
    """Build one normalized quota entry, or `None` if there is no number.

    Shared by every adapter so that "no data" is expressed one way. The
    `resets_at` contract is an ISO-8601 string because `formatting.reset_text`
    parses one with `datetime.fromisoformat`; adapters holding a unix
    timestamp convert with `epoch_to_iso` below.
    """
    if percent is None:
        return None
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return None
    return {"utilization": value, "resets_at": resets_at}


def epoch_to_iso(value: Any) -> str | None:
    """Unix seconds -> UTC ISO-8601, or `None` when unusable.

    Codex reports resets as epoch seconds while Anthropic reports them as
    ISO strings; normalizing here keeps the difference out of `formatting.py`,
    which must stay a pure text layer with one input shape.
    """
    from datetime import datetime, timezone

    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


__all__ = ["Provider", "UsageRequest", "epoch_to_iso", "quota_entry"]
