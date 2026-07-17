"""claude_usage_tray.settings -- the user-settings model over config.json.

Owns the `settings` key's schema: validation, defaults, serialization, and
the settings -> `Theme` mapping. Pure -- no tkinter, no display server, no
disk (user-settings spec, "Headless Testability"). `config.py` performs the
actual read/write; this module only interprets the blob.

**Why `config.py` does not import this module**: `config`, `settings`,
`alerts` and `icons` all sit on L2, and the layering gate forbids an edge
between same-layer modules (`tests/test_layering.py`). So `AppConfig.settings`
carries the raw mapping uninterpreted, and the composition root (`app.py`,
L5) is what maps raw <-> `Settings`. That keeps the one file with two owners
honest: `config.py` owns durability and the profile schema, this module owns
the settings schema, and neither reaches into the other.

**Tolerance policy**: an unusable settings *value* degrades to its default
rather than raising. Deliberate: `config.py` treats a schema violation as
corruption and quarantines the file, and quarantining an irreplaceable
profile list over a mistyped theme name would be wildly disproportionate.
Profiles stay strict; settings degrade. Every default here is documented in
the user-settings spec's "Defined Defaults" requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Collection, Mapping, Sequence

from .quotas import QUOTA_WINDOWS
from .theme import DARK, LIGHT, PALETTE_FIELDS, Palette, Theme, parse_hex_color, with_overrides

THEME_DARK = "dark"
THEME_LIGHT = "light"

# The two built-in presets. These are *starting points*, not the whole set:
# `Settings.colors` may override any subset of the nine palette fields with
# the user's own hex (user-settings spec, "Custom Hex Palettes"). The preset
# name is still what a palette is anchored to, so an un-overridden field keeps
# tracking the preset.
THEME_NAMES: tuple[str, ...] = (THEME_DARK, THEME_LIGHT)

_PALETTES: dict[str, Palette] = {THEME_DARK: DARK, THEME_LIGHT: LIGHT}

DEFAULT_THEME_NAME = THEME_DARK

# 9 is the app's current hardcoded base size -- the default MUST reproduce
# today's rendering exactly. The range is bounded because `Theme.font()`
# derives `small` as `base - 1`: a base below 7 would start producing
# unreadable 6pt text, and design.md's known limitation (pixel-literal
# `wraplength` and the 252px bar do not reflow) makes very large bases
# increasingly ragged.
DEFAULT_FONT_SIZE = 9
MIN_FONT_SIZE = 7
MAX_FONT_SIZE = 16

DEFAULT_WINDOW_SIZE: str | None = None

# Sanity bounds for a persisted "<width>x<height>". Anything outside them is
# discarded rather than handed to Tk's `geometry()`, which is a seam no
# headless test can reach.
_MIN_WINDOW_EDGE = 120
_MAX_WINDOW_EDGE = 20000

DEFAULT_ALERTS_ENABLED = True
DEFAULT_THRESHOLDS: tuple[int, ...] = (80, 95)

_MIN_THRESHOLD = 1
_MAX_THRESHOLD = 100

# Minimum time between two notifications for the SAME (profile x quota window),
# in minutes. Unit is minutes because that is the granularity a user reasons
# about and the phrasing they used ("tiempo minimo entre alerta y alerta").
# `0` disables the cooldown; the upper bound is 24h. The default is 60: a
# conservative value that collapses rapid same-window bursts. Note that a quota
# window resets in *hours* (session ~5h), so cross-reset repeats need a value
# above that reset period -- the field is exposed precisely so the user can
# raise it.
DEFAULT_MIN_INTERVAL_MINUTES = 60
MIN_ALERT_INTERVAL_MINUTES = 0
MAX_ALERT_INTERVAL_MINUTES = 1440


def _default_window_thresholds() -> dict[str, tuple[int, ...]]:
    return {window: DEFAULT_THRESHOLDS for window in QUOTA_WINDOWS}


@dataclass(frozen=True)
class AlertSettings:
    """Alert preferences.

    `thresholds` is the **global default, per quota window**: window name ->
    tiers. It is *complete* -- every window in `QUOTA_WINDOWS` always has a
    list, because alerts being enabled with no tiers for a window would leave
    that window silently dead, which is a state that lies about itself.

    `profiles` holds per-profile overrides keyed by `profile_id`, each itself
    a per-window map. It is **sparse at both levels**: an absent profile
    inherits every window, and a profile present with only `session` inherits
    `weekly` and `weekly_fable`. That is what makes inheritance per (profile,
    window) rather than per profile -- see `thresholds_for`.

    Both maps are treated as immutable, like `AppConfig.settings`: every
    producer here builds fresh dicts, and no consumer may mutate them.
    """

    enabled: bool = DEFAULT_ALERTS_ENABLED
    thresholds: Mapping[str, tuple[int, ...]] = field(default_factory=_default_window_thresholds)
    profiles: Mapping[str, Mapping[str, tuple[int, ...]]] = field(default_factory=dict)
    # Minimum minutes between repeat notifications for the same (profile,
    # window). `0` disables the cooldown. See `DEFAULT_MIN_INTERVAL_MINUTES`.
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES

    def thresholds_for(self, profile_id: str, window: str) -> tuple[int, ...]:
        """The tiers for one (profile, quota window): override, else global.

        Resolved per pair, not per profile: `p1` may override `session` and
        still inherit `weekly`. That is the whole point of the per-window
        split -- the state machine has always keyed on `(profile_id,
        quota_window)`, and until now the config could only speak in profiles.
        """
        override = self.profiles.get(profile_id, {}).get(window)
        if override is not None:
            return override
        return self.thresholds.get(window, DEFAULT_THRESHOLDS)

    def has_override(self, profile_id: str, window: str) -> bool:
        """Whether this (profile, window) sets its own tiers, or inherits."""
        return window in self.profiles.get(profile_id, {})


@dataclass(frozen=True)
class Settings:
    """The `settings` blob, interpreted.

    `colors` is a sparse map of `Palette` field name -> canonical `#rrggbb`.
    Empty (the default, and what every pre-existing config yields) means the
    `theme` preset renders exactly as it always did.
    """

    theme: str = DEFAULT_THEME_NAME
    font_size: int = DEFAULT_FONT_SIZE
    window_size: str | None = DEFAULT_WINDOW_SIZE
    colors: Mapping[str, str] = field(default_factory=dict)
    alerts: AlertSettings = field(default_factory=AlertSettings)


@dataclass(frozen=True)
class InvalidColors:
    """Notice: stored custom colors could not be used and were dropped.

    `fields` names them. The palette still renders -- each dropped field
    falls back to its preset value -- so this is information, never a
    failure: it exists because silently ignoring a hand-edited color leaves
    the user staring at a window that did not change, with nothing to read.

    Mirrors `config.ConfigCorrupted`'s shape (a frozen notice dataclass the
    composition root surfaces once a window exists) rather than reusing it:
    that one means "irreplaceable data was quarantined", and saying that
    about a typo'd hex would be a lie about severity.
    """

    fields: tuple[str, ...]


SettingsNotice = InvalidColors  # union grows as later conditions are added


@dataclass(frozen=True)
class SettingsLoad:
    """Result of `load()`: the settings, plus anything worth telling the user."""

    settings: Settings
    notices: list[SettingsNotice]


def build_theme(settings: Settings) -> Theme:
    """Map settings onto a renderable `Theme`.

    The preset named by `settings.theme` supplies every field the user has
    not overridden; `settings.colors` replaces the rest.

    Returns a `Theme`, never a `Palette`. The distinction is load-bearing:
    `ui/*` calls `theme.palette` and `theme.font(role)`, so handing it a
    bare `Palette` raises `AttributeError` at render time -- on a seam no
    Linux test can reach. See `theme.DEFAULT_THEME`'s note.
    """
    preset = _PALETTES.get(settings.theme, DARK)
    return Theme(palette=with_overrides(preset, settings.colors), base_size=settings.font_size)


def preset_palette(settings: Settings) -> Palette:
    """The un-overridden preset behind `settings` -- what "reset" restores to."""
    return _PALETTES.get(settings.theme, DARK)


def parse_window_size(value: Any) -> tuple[int, int] | None:
    """Parse a persisted `"<width>x<height>"` into `(width, height)`.

    Returns `None` for anything unusable, so the caller falls back to
    auto-sizing the window to its content instead of raising.
    """
    if not isinstance(value, str):
        return None
    parts = value.split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = (int(part) for part in parts)
    except ValueError:
        return None
    if not _MIN_WINDOW_EDGE <= width <= _MAX_WINDOW_EDGE:
        return None
    if not _MIN_WINDOW_EDGE <= height <= _MAX_WINDOW_EDGE:
        return None
    return width, height


def format_window_size(width: int, height: int) -> str:
    """The inverse of `parse_window_size`."""
    return f"{int(width)}x{int(height)}"


def _theme_name(value: Any) -> str:
    return value if isinstance(value, str) and value in THEME_NAMES else DEFAULT_THEME_NAME


def _font_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_FONT_SIZE
    return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(value)))


def _window_size(value: Any) -> str | None:
    parsed = parse_window_size(value)
    return None if parsed is None else format_window_size(*parsed)


def _usable_tiers(value: Any) -> tuple[int, ...] | None:
    """Validate a tier list, or `None` if it yields nothing usable.

    `None` and "the default" are different answers, which is why this is
    separate from `_thresholds`: an unusable *global* list must fall back to
    `DEFAULT_THRESHOLDS` (alerts are on, so they must have tiers), but an
    unusable *per-profile override* must fall back to the global list, which
    may not be the default at all. Collapsing the two would silently pin a
    profile to `[80, 95]` while every other profile used the user's own tiers.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    tiers = {
        int(tier)
        for tier in value
        if not isinstance(tier, bool) and isinstance(tier, (int, float)) and _MIN_THRESHOLD <= tier <= _MAX_THRESHOLD
    }
    return tuple(sorted(tiers)) if tiers else None


def _bare_tier_list(value: Any) -> tuple[int, ...] | None:
    """`value` read as a bare tier list, or `None` if it is not one.

    A bare list is the spelling that predates per-window thresholds, and it
    means **these tiers, in every quota window** -- because that is precisely
    what it did when one list governed all three. So it is re-read, never
    rewritten: no migration pass, no schema bump, no version check (see the
    usage-alerts spec, "A Bare Tier List Means 'Every Window'"). The same
    device `theme` uses in "An Existing theme Value Keeps Its Meaning".

    A `Mapping` is not a `Sequence`, so the per-window spelling can never be
    mistaken for a bare list here -- which is the only reason one reader can
    safely accept both.
    """
    return _usable_tiers(value)


def _thresholds(value: Any) -> dict[str, tuple[int, ...]]:
    """Read the global default tier list for every quota window.

    Complete by construction: a window with nothing usable stored falls back
    to `DEFAULT_THRESHOLDS`, because a tier list that survives validation
    empty would leave alerts `enabled` yet permanently silent for that window
    -- a state that lies about itself, exactly as it would have globally.
    """
    shared = _bare_tier_list(value)
    if shared is not None:
        return {window: shared for window in QUOTA_WINDOWS}
    if not isinstance(value, Mapping):
        return _default_window_thresholds()
    resolved: dict[str, tuple[int, ...]] = {}
    for window in QUOTA_WINDOWS:
        tiers = _usable_tiers(value.get(window))
        resolved[window] = DEFAULT_THRESHOLDS if tiers is None else tiers
    return resolved


def _profile_windows(value: Any) -> dict[str, tuple[int, ...]]:
    """Read one profile's overrides, per quota window.

    Sparse: an absent (or unusable) window is simply not present, which is
    what "inherit that window's global list" means. An entry that does not
    validate is *dropped*, not defaulted -- dropping it means that window
    inherits, which is the same thing an absent entry means. There is no third
    state to invent.
    """
    shared = _bare_tier_list(value)
    if shared is not None:
        # The pre-per-window spelling: this profile overrode all three.
        return {window: shared for window in QUOTA_WINDOWS}
    if not isinstance(value, Mapping):
        return {}
    windows: dict[str, tuple[int, ...]] = {}
    for window in QUOTA_WINDOWS:
        tiers = _usable_tiers(value.get(window))
        if tiers is not None:
            windows[window] = tiers
    return windows


def _profile_thresholds(value: Any) -> dict[str, dict[str, tuple[int, ...]]]:
    """Read the sparse per-profile override map, sparse per window inside it.

    A profile whose entry yields no usable window at all is dropped entirely,
    so `has_override` and `prune_profile_thresholds` never have to reason
    about an empty override that means nothing.
    """
    if not isinstance(value, Mapping):
        return {}
    overrides: dict[str, dict[str, tuple[int, ...]]] = {}
    for profile_id, raw in value.items():
        if not isinstance(profile_id, str) or not profile_id:
            continue
        windows = _profile_windows(raw)
        if windows:
            overrides[profile_id] = windows
    return overrides


def _min_interval_minutes(value: Any) -> int:
    """Clamp a stored min-interval into `[MIN, MAX]`, defaulting on nonsense.

    Degrades rather than raises, like every other settings value: a mistyped
    interval is a bad value, not a corrupt file.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_MIN_INTERVAL_MINUTES
    return max(MIN_ALERT_INTERVAL_MINUTES, min(MAX_ALERT_INTERVAL_MINUTES, int(value)))


def _alert_settings(value: Any) -> AlertSettings:
    if not isinstance(value, Mapping):
        return AlertSettings()
    enabled = value.get("enabled")
    return AlertSettings(
        enabled=enabled if isinstance(enabled, bool) else DEFAULT_ALERTS_ENABLED,
        thresholds=_thresholds(value.get("thresholds")),
        profiles=_profile_thresholds(value.get("profiles")),
        min_interval_minutes=_min_interval_minutes(value.get("min_interval_minutes")),
    )


def _colors(value: Any) -> tuple[dict[str, str], tuple[str, ...]]:
    """Read the sparse custom-color map, reporting what it had to drop.

    Returns `(accepted, rejected_field_names)`. A malformed hex is rejected
    rather than passed through: an unparseable color reaching `Tk`'s `bg=`
    raises `TclError` at render time, on a seam no headless test can reach --
    which is exactly the class of bug this codebase keeps paying for. An
    unknown key is rejected too, because a `"background": "#fff"` typo is the
    same user error wearing a different hat, and silently ignoring it looks
    identical to the app being broken.
    """
    if not isinstance(value, Mapping):
        return {}, ()
    accepted: dict[str, str] = {}
    rejected: set[str] = set()
    for name, raw in value.items():
        if not isinstance(name, str) or name not in PALETTE_FIELDS:
            rejected.add(str(name))
            continue
        parsed = parse_hex_color(raw)
        if parsed is None:
            rejected.add(name)
            continue
        accepted[name] = parsed
    return accepted, tuple(sorted(rejected))


def load(raw: Any) -> SettingsLoad:
    """Build `Settings` from `config.json`'s `settings` blob, with notices.

    An absent key (a Slice-1 config.json), an empty object, or a value of the
    wrong type all yield the documented defaults -- never an error, and never
    a reason to quarantine a config file whose profiles are fine.

    The one condition worth reporting is a stored color that could not be
    used: it is the only degradation here the user deliberately authored and
    would otherwise watch fail in silence. A bad theme name or font size is
    still degraded quietly -- those have obvious visible defaults, and the
    picker cannot produce them in the first place.
    """
    if not isinstance(raw, Mapping):
        return SettingsLoad(settings=Settings(), notices=[])
    colors, rejected = _colors(raw.get("colors"))
    settings = Settings(
        theme=_theme_name(raw.get("theme")),
        font_size=_font_size(raw.get("font_size")),
        window_size=_window_size(raw.get("window_size")),
        colors=colors,
        alerts=_alert_settings(raw.get("alerts")),
    )
    notices: list[SettingsNotice] = [InvalidColors(fields=rejected)] if rejected else []
    return SettingsLoad(settings=settings, notices=notices)


def from_raw(raw: Any) -> Settings:
    """`load(raw).settings` -- for callers with nothing to report a notice to."""
    return load(raw).settings


def to_raw(settings: Settings) -> dict[str, Any]:
    """Serialize `Settings` into the `settings` blob `config.py` persists.

    JSON-native types only (no tuples): the round trip through
    `json.dump`/`json.load` must be an identity, or an unrelated profile edit
    would silently rewrite the user's settings.
    """
    return {
        "theme": settings.theme,
        "font_size": settings.font_size,
        "window_size": settings.window_size,
        "colors": dict(settings.colors),
        "alerts": {
            "enabled": settings.alerts.enabled,
            "min_interval_minutes": settings.alerts.min_interval_minutes,
            # Always written in the per-window form, including for a config
            # that came in as a bare list. That is a change of *spelling*, not
            # of behaviour: the three windows are written holding exactly what
            # the single list already applied to all three, and reading it back
            # yields the same tiers. Nothing is lost and nothing is reset.
            "thresholds": {
                window: list(tiers) for window, tiers in settings.alerts.thresholds.items()
            },
            "profiles": {
                profile_id: {window: list(tiers) for window, tiers in windows.items()}
                for profile_id, windows in settings.alerts.profiles.items()
            },
        },
    }


def parse_thresholds_text(text: Any) -> tuple[int, ...] | None:
    """Parse a user-typed `"80, 95"` into validated tiers, or `None`.

    `None` means "nothing usable here" and is the caller's cue to say so --
    it is deliberately distinct from `()`, which this never returns. Blank
    input is `None` too: at a per-profile row blank means "inherit the global
    list", and the UI reads it that way before it ever calls this.

    The inverse of `format_thresholds`, and the same shape as
    `parse_window_size`/`format_window_size` above: text validation lives in
    the model, where a test can reach it, not in the widget.
    """
    if not isinstance(text, str):
        return None
    tiers: list[int] = []
    for chunk in text.replace(";", ",").split(","):
        token = chunk.strip().rstrip("%").strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            return None  # a typo must be reported, never silently dropped
        tiers.append(value)
    return _usable_tiers(tiers)


def format_thresholds(tiers: Sequence[int]) -> str:
    """The inverse of `parse_thresholds_text`."""
    return ", ".join(str(int(tier)) for tier in tiers)


def with_theme(settings: Settings, theme_name: str) -> Settings:
    """Return `settings` with a validated theme name applied.

    Custom colors survive a preset switch on purpose: they are overrides
    *on top of* a preset, so a user who set `accent` and then previews the
    light theme keeps their accent. `without_colors` is how you drop them,
    and it is one button away.
    """
    return replace(settings, theme=_theme_name(theme_name))


def with_font_size(settings: Settings, font_size: int) -> Settings:
    """Return `settings` with `font_size` clamped into the supported range."""
    return replace(settings, font_size=_font_size(font_size))


def with_window_size(settings: Settings, window_size: str | None) -> Settings:
    """Return `settings` with a validated window size (or `None`) applied."""
    return replace(settings, window_size=_window_size(window_size))


def with_colors(settings: Settings, colors: Mapping[str, str]) -> Settings:
    """Return `settings` with `colors` as the complete override set.

    Replaces rather than merges: the picker always submits every field it
    knows about, so a merge would make a cleared field impossible to express.
    Invalid entries are dropped here as well as at load -- this is a public
    entry point, not only the UI's.
    """
    accepted, _ = _colors(colors)
    return replace(settings, colors=accepted)


def without_colors(settings: Settings) -> Settings:
    """Return `settings` with every custom color dropped, back to the preset."""
    return replace(settings, colors={})


def with_alerts_enabled(settings: Settings, enabled: bool) -> Settings:
    """Return `settings` with alert delivery switched on or off."""
    return replace(settings, alerts=replace(settings.alerts, enabled=bool(enabled)))


def with_alert_interval_minutes(settings: Settings, minutes: int) -> Settings:
    """Return `settings` with the repeat-alert cooldown clamped into range.

    `0` disables the cooldown; anything unusable falls back to the default,
    for the same reason the load path does -- a bad value is degraded, never
    fatal.
    """
    return replace(
        settings, alerts=replace(settings.alerts, min_interval_minutes=_min_interval_minutes(minutes))
    )


def with_thresholds(settings: Settings, window: str, tiers: Sequence[int]) -> Settings:
    """Return `settings` with one quota window's **global default** tier list.

    Only `window` moves; the other two keep whatever they held. An unusable
    `tiers` falls back to `DEFAULT_THRESHOLDS` for that window rather than
    clearing it, for the reason `_thresholds` gives: a window with no tiers
    and alerts enabled is silently dead.
    """
    if window not in QUOTA_WINDOWS:
        return settings
    usable = _usable_tiers(tiers)
    thresholds = dict(settings.alerts.thresholds)
    thresholds[window] = DEFAULT_THRESHOLDS if usable is None else usable
    return replace(settings, alerts=replace(settings.alerts, thresholds=thresholds))


def with_profile_thresholds(
    settings: Settings,
    profile_id: str,
    window: str,
    tiers: Sequence[int] | None,
) -> Settings:
    """Return `settings` with one (profile, window) override set, or removed.

    `tiers=None` (or anything that validates to nothing) removes *that
    window's* override, so the profile inherits that window's global list
    again. That is the only way back: there is no "empty override" state,
    because a window with zero tiers and alerts enabled would be silently dead
    in exactly the way `_thresholds` refuses to allow globally.

    A profile left with no overridden window at all is dropped from the map
    entirely, so "has no overrides" has exactly one spelling rather than two
    (absent, or present-and-empty) that `prune`, `to_raw` and `has_override`
    would each have to handle.
    """
    if window not in QUOTA_WINDOWS:
        return settings
    overrides = {
        existing_id: dict(windows) for existing_id, windows in settings.alerts.profiles.items()
    }
    windows = overrides.get(profile_id, {})
    usable = None if tiers is None else _usable_tiers(tiers)
    if usable is None:
        windows.pop(window, None)
    else:
        windows[window] = usable
    if windows:
        overrides[profile_id] = windows
    else:
        overrides.pop(profile_id, None)
    return replace(settings, alerts=replace(settings.alerts, profiles=overrides))


def prune_profile_thresholds(settings: Settings, known_profile_ids: Collection[str]) -> Settings:
    """Drop overrides for profiles that no longer exist.

    Mirrors `alerts.prune` for the other half of a deleted profile's
    footprint: without it, `config.json` accumulates tier lists keyed by uuids
    nothing can ever reach again. Re-creating a profile mints a fresh uuid, so
    it correctly comes back on the global default rather than inheriting a
    dead namesake's tiers.
    """
    known = set(known_profile_ids)
    overrides = {
        profile_id: tiers for profile_id, tiers in settings.alerts.profiles.items() if profile_id in known
    }
    if len(overrides) == len(settings.alerts.profiles):
        return settings
    return replace(settings, alerts=replace(settings.alerts, profiles=overrides))
