"""claude_usage_tray.alerts -- the edge-triggered threshold state machine.

`evaluate()` is a pure function: no clock, no I/O, no mutation of its
argument. It compares strings and floats only, which is what makes the whole
alert feature deterministically testable without a display server, a network,
or a frozen clock. `load_state`/`save_state` do touch disk, but only through
`jsonstore` -- the state machine itself never does.

The caller (`app.py`) owns everything this module deliberately does not:
building the `(profile_id, profile_data)` pairs, persisting the returned
state, and delivering the returned events as notifications. Keeping delivery
out here is what keeps `pystray` out of the tested path.

Two states per key -- UNSEEN (absent) and TRACKING -- and four rules per
(profile, quota window), per poll (design.md, "Alert evaluation is a pure
function with no clock"):

  1. window missing, `resets_at` absent/null, or `pct` None -> SKIP: do not
     create, mutate, or fire. An error poll leaves state exactly as it was.
  2. key UNSEEN                                -> create, max_notified = 0
  3. canonical `resets_at` differs from stored -> RE-ARM, max_notified = 0
  4. highest = max(t for t in tiers if pct >= t, default=0); if
     highest > max_notified, emit ONE event naming `highest` and store it.

`tiers` is resolved per **(profile, quota window)**: that window's own
override if `thresholds_by_profile` names it, else that window's entry in the
global `thresholds` map. Nothing else about the machine changes -- state was
already keyed by `(profile_id, quota_window)`, so per-window tiers needed no
new key, no migration, and no second state shape. That is not a coincidence:
the config was the half that could not express what this rule always could,
and closing that gap is the whole of the change.

`max_notified_threshold` is monotonically non-decreasing within a window --
that single property *is* the hysteresis rule. Falling usage never re-arms;
only a rise into a higher, not-yet-notified tier fires. Rule 4 takes the max
rather than the set, so a 10% -> 97% jump emits one event (95), never a
backlog burst.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

from . import jsonstore
from .quotas import QUOTA_WINDOWS, QuotaWindow

SCHEMA_VERSION = 1

# `QuotaWindow`/`QUOTA_WINDOWS` are re-exported rather than defined here now.
# They moved to `quotas.py` (L1) when `settings.py` had to name the same three
# windows to store tiers per window: `settings` and `alerts` are both L2, so
# neither may import the other, and the constant had to fall to a layer they
# can both reach. Re-exported because this module is where every existing
# caller and test already looks for it, and that is not worth churning.
__all__ = [
    "QUOTA_WINDOWS",
    "SCHEMA_VERSION",
    "AlertEvent",
    "AlertState",
    "QuotaWindow",
    "canonical_resets_at",
    "evaluate",
    "invalidate_profile",
    "load_state",
    "prune",
    "save_state",
    "state_key",
]

# "<profile_id>::<quota_window>" ->
#   {"resets_at": str|None, "max_notified_threshold": int,
#    "last_notified_at": str  (optional; absent means "never notified")}
AlertState = dict[str, dict[str, Any]]

_KEY_SEPARATOR = "::"


@dataclass(frozen=True)
class AlertEvent:
    """One threshold crossing, ready for the caller to deliver."""

    profile_id: str
    window: QuotaWindow
    threshold: int
    pct: float


def state_key(profile_id: str, window: QuotaWindow) -> str:
    """The state dict key for one (profile, quota window) pair.

    Keyed by `profile_id`, never `config_dir`: both name and path are
    user-editable, so neither is a stable identity -- a path edit would
    orphan the old entry and mint a duplicate (usage-alerts spec, "Keyed by
    profile_id").
    """
    return f"{profile_id}{_KEY_SEPARATOR}{window}"


def _profile_id_of(key: str) -> str:
    return key.split(_KEY_SEPARATOR, 1)[0]


def canonical_resets_at(value: Any) -> str | None:
    """Normalize a `resets_at` timestamp to one canonical UTC spelling.

    Returns `None` when the value is absent, null, or blank -- the caller
    reads that as rule 1 (SKIP), never as a window change.

    Two encodings of the same instant MUST collapse to the same string, or
    they compare unequal and spuriously re-arm every poll. `Z` is rewritten
    to `+00:00` explicitly because `datetime.fromisoformat` rejects the `Z`
    suffix before Python 3.11, and this app targets 3.10+.

    A naive timestamp (no offset) is read as UTC rather than machine-local.
    design.md does not name this case; local time is rejected for it because
    it would make the state machine's output depend on the machine's TZ,
    forfeiting the "no clock, no environment" property that makes this
    module deterministic.

    An unparseable value falls back to its raw string: it still compares
    equal to itself poll over poll, so an odd-but-stable timestamp tracks a
    window correctly instead of re-arming forever.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return raw
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse_moment(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp into a UTC-aware `datetime`, or `None`.

    Mirrors `canonical_resets_at`'s tolerance (the pre-3.11 `Z` rewrite, a
    naive value read as UTC) but returns an actual `datetime` for arithmetic,
    where `canonical_resets_at` returns a canonical *string* for equality. A
    value that is absent, blank, or unparseable yields `None`, which the
    cooldown reads as "no usable timestamp" and therefore "allow" -- never a
    crash, and never a spurious suppression.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _within_cooldown(
    last_notified_at: Any, now: datetime | None, cooldown: timedelta | None
) -> bool:
    """Whether a repeat of this entry is still inside its cooldown window.

    Returns `False` -- allow the notification -- whenever the cooldown is off
    (`None` or non-positive), no clock was injected, or the entry has no
    usable `last_notified_at` (which is what a state file written before the
    cooldown existed looks like: absent means "never notified", never "notified
    at epoch"). Only a parseable, recent-enough timestamp suppresses.
    """
    if cooldown is None or cooldown <= timedelta(0) or now is None:
        return False
    last = _parse_moment(last_notified_at)
    if last is None:
        return False
    return now - last < cooldown


def _utilization(entry: Mapping[str, Any]) -> float | None:
    """The quota window's used-percentage, or `None` if it cannot be read.

    Deliberately NOT `formatting.pct`, which defaults a missing
    `utilization` to `0.0`: for a *display* helper that reads as "nothing
    used", but here it would be a lie the machine acts on -- rule 1 requires
    an unknown pct to SKIP, not to fire a 0% window.
    """
    value = entry.get("utilization")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    """Tolerant int read for a value that came off disk."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _tiers(thresholds: Sequence[int]) -> list[int]:
    return sorted({_as_int(tier) for tier in thresholds})


def _is_bare_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _tiers_by_window(value: Any) -> dict[QuotaWindow, list[int]]:
    """Global tiers per quota window, from either spelling.

    A bare sequence means "these tiers, in every window" -- the spelling that
    predates per-window thresholds, and the one `settings.py` still reads out
    of a `config.json` written before them. Accepting both here means the rule
    is the same at both boundaries, rather than a config-loader convention the
    state machine happens not to share.
    """
    if isinstance(value, Mapping):
        return {window: _tiers(value.get(window) or ()) for window in QUOTA_WINDOWS}
    if _is_bare_list(value):
        shared = _tiers(value)
        return {window: list(shared) for window in QUOTA_WINDOWS}
    return {window: [] for window in QUOTA_WINDOWS}


def _override_by_window(value: Any) -> dict[QuotaWindow, list[int] | None]:
    """One profile's override tiers per window; `None` means "inherit".

    `None` and `[]` are different answers and must stay that way: absent means
    take the global list, whereas an empty list would mean this window has no
    tiers at all and can never fire. Only the first is expressible.
    """
    if isinstance(value, Mapping):
        return {
            window: (None if value.get(window) is None else _tiers(value[window]))
            for window in QUOTA_WINDOWS
        }
    if _is_bare_list(value):
        shared = _tiers(value)
        return {window: list(shared) for window in QUOTA_WINDOWS}
    return {window: None for window in QUOTA_WINDOWS}


def evaluate(
    state: AlertState,
    items: Sequence[tuple[str, Mapping[str, Any]]],
    thresholds: Mapping[str, Sequence[int]] | Sequence[int],
    *,
    thresholds_by_profile: Mapping[str, Mapping[str, Sequence[int]] | Sequence[int]] | None = None,
    now: datetime | None = None,
    cooldown: timedelta | None = None,
) -> tuple[AlertState, list[AlertEvent]]:
    """Apply one poll to `state`, returning a NEW state and its events.

    `items` is `(profile_id, profile_data)`, where `profile_data` is the
    per-profile payload `api.fetch_usage` returns. `state` is never mutated:
    the caller can therefore persist iff `new_state != state` and that
    comparison is trivially correct (design.md's persistence cadence, which
    turns ~288 writes/day into the ~6-10 that reflect a real change).

    `thresholds` is the global default, **per quota window** (`{"session":
    [80, 95], ...}`). `thresholds_by_profile` overrides it, per profile and
    per window (usage-alerts spec, "Per-Profile, Per-Window Thresholds With a
    Global Default"); a window absent from a profile's map -- which is every
    window of every profile, for a config written before overrides existed --
    uses that window's global list unchanged. Resolution is per (profile,
    window) and per poll, so a tier list edited mid-window needs no migration:
    it simply falls out of rule 4 on the next poll, exactly as an edited global
    list already did.

    Both parameters also accept a **bare tier list** in place of a per-window
    map, meaning "these tiers, in every window" -- the spelling that predates
    per-window thresholds. See `_tiers_by_window`.

    Events are returned even when alerts are disabled -- dropping them is the
    caller's job. That keeps the state warm, so re-enabling alerts cannot
    produce a burst of stale notifications.

    `now` and `cooldown` add the **repeat-notification cooldown**, per (profile,
    window). The clock is *injected*, never read here, so this function stays
    pure and deterministic under test (the same convention `api.py` uses for
    its `now` argument). When a crossing would fire but this entry's
    `last_notified_at` is within `cooldown` of `now`, the event is suppressed
    *without* advancing `max_notified_threshold`, so it re-fires on a later
    poll once the interval elapses. `last_notified_at` survives a `resets_at`
    rollover on purpose -- the cooldown exists to damp exactly the re-fire a
    window reset produces while usage stays high. With `now=None` or
    `cooldown` absent/non-positive the machine behaves exactly as before and
    records no `last_notified_at`.
    """
    new_state: AlertState = {key: dict(entry) for key, entry in state.items()}
    events: list[AlertEvent] = []
    default_tiers = _tiers_by_window(thresholds)
    overrides = thresholds_by_profile or {}

    for profile_id, data in items:
        if not isinstance(data, Mapping):
            continue
        profile_tiers = _override_by_window(overrides.get(profile_id))
        for window in QUOTA_WINDOWS:
            override = profile_tiers[window]
            tiers = default_tiers[window] if override is None else override
            entry = data.get(window)
            if not isinstance(entry, Mapping):
                continue  # rule 1: window missing (or the poll errored)

            resets_at = canonical_resets_at(entry.get("resets_at"))
            if resets_at is None:
                continue  # rule 1: error state -- neither fire nor re-arm

            pct = _utilization(entry)
            if pct is None:
                continue  # rule 1: pct unknown

            key = state_key(profile_id, window)
            stored = new_state.get(key)
            if stored is None or stored.get("resets_at") != resets_at:
                # rule 2 (UNSEEN) / rule 3 (window rolled over, or the path
                # was repointed at a different account -- same code path, no
                # special case).
                rearmed: dict[str, Any] = {"resets_at": resets_at, "max_notified_threshold": 0}
                if stored is not None and stored.get("last_notified_at") is not None:
                    # Carry the last-notified time across the rollover so the
                    # cooldown can span it -- damping the re-fire on reset is
                    # the whole reason the cooldown exists.
                    rearmed["last_notified_at"] = stored["last_notified_at"]
                stored = rearmed
                new_state[key] = stored

            max_notified = _as_int(stored.get("max_notified_threshold"))
            highest = max((tier for tier in tiers if pct >= tier), default=0)
            if highest > max_notified:  # rule 4
                if _within_cooldown(stored.get("last_notified_at"), now, cooldown):
                    # A repeat of THIS entry within its cooldown: suppress the
                    # event but leave max_notified_threshold alone, so the same
                    # crossing re-fires on a later poll once the interval has
                    # elapsed. Scoped per entry -- a different (profile, window)
                    # is never touched by this one's recent notification.
                    continue
                stored["max_notified_threshold"] = highest
                if now is not None:
                    stored["last_notified_at"] = now.isoformat()
                events.append(AlertEvent(profile_id=profile_id, window=window, threshold=highest, pct=pct))

    return new_state, events


def prune(state: AlertState, known_profile_ids: Collection[str]) -> AlertState:
    """Drop every entry whose profile is no longer in the config.

    Bounds the file's growth over time. Re-creating a deleted profile mints a
    fresh uuid, so it comes back UNSEEN and therefore armed -- which is the
    correct behavior, not a side effect to work around.
    """
    known = set(known_profile_ids)
    return {key: dict(entry) for key, entry in state.items() if _profile_id_of(key) in known}


def invalidate_profile(state: AlertState, profile_id: str) -> AlertState:
    """Drop every window entry for `profile_id`, re-arming it from scratch.

    Called by the edit handler when a profile's `config_dir` changed. The
    spec relies on the new account's `resets_at` differing "naturally" to
    re-arm; weekly windows can legitimately coincide across accounts, so
    explicit invalidation removes that coincidence dependency (design.md
    strengthens the proposal here). The differing-`resets_at` path remains as
    a backstop.
    """
    return {key: dict(entry) for key, entry in state.items() if _profile_id_of(key) != profile_id}


def load_state(path: Path) -> AlertState:
    """Read persisted alert state. Corrupt, unreadable, or absent -> `{}`.

    Silently, with no quarantine, no backup, and no user-visible warning --
    the exact opposite of `config.json`'s policy, and deliberately so
    (config-store spec, "Corrupt alert-state.json Is Discarded Without
    Backup"). This file is machine-written and disposable: losing it costs at
    most one duplicate notification, so preservation overhead is not
    justified. `config.json` is hand-authored and irreplaceable, so it gets
    quarantine-and-warn. Do not "fix" this asymmetry -- it is the entire
    point of the two-file split.
    """
    try:
        raw = jsonstore.read_json(path)
    except (jsonstore.CorruptFile, OSError):
        return {}
    if not isinstance(raw, Mapping):
        return {}

    entries = raw.get("entries")
    if not isinstance(entries, Mapping):
        return {}

    state: AlertState = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, Mapping):
            continue
        resets_at = entry.get("resets_at")
        if resets_at is not None and not isinstance(resets_at, str):
            continue
        loaded: dict[str, Any] = {
            "resets_at": resets_at,
            "max_notified_threshold": _as_int(entry.get("max_notified_threshold")),
        }
        # Optional, and only carried when it is a usable string. A missing
        # value is left absent rather than defaulted, so the cooldown reads it
        # as "never notified" (allow) -- the migration path for every
        # alert-state.json written before the cooldown existed.
        last_notified_at = entry.get("last_notified_at")
        if isinstance(last_notified_at, str):
            loaded["last_notified_at"] = last_notified_at
        state[key] = loaded
    return state


def save_state(path: Path, state: AlertState) -> None:
    """Persist alert state atomically, via `jsonstore`'s durable write."""
    jsonstore.write_json_atomic(path, {"schema_version": SCHEMA_VERSION, "entries": state})
