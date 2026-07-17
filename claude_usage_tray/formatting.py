"""claude_usage_tray.formatting -- pure text/format helpers, injectable clock.

Every function here is free of I/O, UI, and network: no tkinter, no PIL, no
pystray, no `requests`. Move-only extraction from `app.py`; no output
changed for any existing call site (see
`openspec/changes/foundations-refactor/design.md` for the
characterize-then-move sequence this followed and
`tests/test_formatting.py` for the characterization evidence).

Time-dependent functions (`reset_text`, `reset_label`, `updated_text`,
`parse_meta_datetime`) accept keyword-only `now`/`tz`, following the
`now: datetime | None = None` convention `api.py`'s `_seconds_until` already
established: `None` means "use the real clock" / "use the machine's local
timezone", so every existing call site keeps its exact current behavior
unless it opts into injection (tests do; production code does not).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import ClaudeProfile
    from .theme import Palette

# Same hex values as theme.DARK's accent/warn/danger fields. Duplicated
# rather than imported: formatting.py is L1 (stdlib-only, see design.md's
# layering table) and must not depend on theme.py's Palette -- that is a
# separate seam with its own reason to exist. If a color ever changes,
# update both.
#
# These are the fallback for `bar_color(value)` called with no palette. Every
# real call site now passes one (see `bar_color`), so they describe the
# DARK preset's bars and nothing else.
_ACCENT = "#d97757"
_WARN = "#f59e0b"
_DANGER = "#ef4444"

DAY_NAMES = ["lun.", "mar.", "mie.", "jue.", "vie.", "sab.", "dom."]


def pct(entry: dict[str, Any] | None) -> float | None:
    if not entry:
        return None
    try:
        return float(entry.get("utilization", 0))
    except (TypeError, ValueError):
        return None


def reset_text(iso_str: str | None, *, now: datetime | None = None, tz: tzinfo | None = None) -> str:
    if not iso_str:
        return "—"
    try:
        reset = datetime.fromisoformat(iso_str)
        current = now if now is not None else datetime.now(timezone.utc)
        diff = reset - current
        total_min = max(0, int(diff.total_seconds() // 60))
        clock = reset.astimezone(tz).strftime("%H:%M")
        if total_min >= 60:
            return f"en {total_min // 60}h {total_min % 60}m ({clock})"
        if total_min > 0:
            return f"en {total_min}m ({clock})"
        return f"pronto ({clock})"
    except Exception:
        return "—"


def reset_label(iso_str: str | None, *, now: datetime | None = None, tz: tzinfo | None = None) -> str:
    if not iso_str:
        return "Se restablece: —"
    try:
        reset = datetime.fromisoformat(iso_str).astimezone(tz)
        current = (now if now is not None else datetime.now()).astimezone(tz)
        if reset.date() == current.date():
            total_sec = max(0, int((reset - current).total_seconds()))
            if total_sec < 60:
                return "Se restablece pronto"
            total_min = total_sec // 60
            if total_min < 60:
                return f"Se restablece en {total_min} min"
            hours = total_min // 60
            mins = total_min % 60
            if mins == 0:
                return f"Se restablece en {hours} h"
            return f"Se restablece en {hours} h {mins} min"
        day = DAY_NAMES[reset.weekday()]
        return f"Se restablece {day} {reset.strftime('%H:%M')} hs"
    except Exception:
        return "Se restablece: —"


def bar_color(value: float, palette: "Palette | None" = None) -> str:
    """The progress-bar fill for `value` percent used.

    `palette` is duck-typed (`.danger`/`.warn`/`.accent`) and imported only
    under `TYPE_CHECKING`, the same device `_profile_display_name` below uses
    for `ClaudeProfile`: this module is L1 and `theme.py` is L1 too, so a
    runtime edge between them would point sideways and `test_layering.py`
    forbids it. Passing the palette in keeps the arrow pointing down from
    `ui/` (L4), which has one already.

    `palette=None` reproduces the three baked-in DARK constants exactly, so
    every pre-existing call site and its characterization tests are
    unchanged. It is the fallback, not the intended path: bars that ignore
    the palette render identically in every theme, which made the light
    preset only partly themed and would make a custom `accent` visibly
    fail to apply to the app's most prominent element.
    """
    danger = _DANGER if palette is None else palette.danger
    warn = _WARN if palette is None else palette.warn
    accent = _ACCENT if palette is None else palette.accent
    if value >= 90:
        return danger
    if value >= 70:
        return warn
    return accent


def _profile_display_name(profile: "ClaudeProfile | None", fallback: str) -> str:
    """Stand-in for `isinstance(profile, ClaudeProfile)`.

    `formatting.py` is L1 and must not import `config.py` (L2) at runtime --
    that would point a dependency edge upward, which `test_layering.py`
    forbids. Every real caller only ever passes a `ClaudeProfile` (always
    has a string `.name`) or `None`/a plain dict (never does), so duck
    typing here is behavior-preserving.
    """
    name = getattr(profile, "name", None)
    return name if isinstance(name, str) else fallback


# `NOTIFYICONDATA.szTip` is 128 WCHARs; the 128th is the OS's null
# terminator (tray-tooltip spec, "Hard Character Budget"). Everything below
# reduces to fit deterministically rather than letting the shell truncate
# silently at a byte we did not choose.
TOOLTIP_BUDGET = 127

_TOOLTIP_HEADER = "Claude — uso"
_STALE_DATA_NOTE = "Mostrando la ultima informacion disponible"

# A warning is reduced to this marker in the multi-profile branch rather than
# dropped: the spec ranks an abbreviated warning above a precise number
# ("Warnings Take Priority Over Precision"), and a per-profile line has no
# room for the sentence the single-profile branch spells out.
_WARNING_MARK = " ⚠"

_ELLIPSIS = "…"

# Reduction levels, applied uniformly to every profile line, least lossy
# first. These are the order in which detail is surrendered when content will
# not fit; when both are exhausted, whole profiles go -- with a marker.
_LEVEL_FULL = 0      # name: 5h 12% · 7d 62% · F 30% [⚠]
_LEVEL_NO_FABLE = 1  # drop the tertiary quota, and clip a long error

# A sanity bound on the profile name, applied at every level -- never a
# compression lever. Clipping a name to buy characters is a false economy:
# the name IS the row's identity, so squeezing "Cuenta numero 1" and "Cuenta
# numero 2" down to a shared "Cuenta …" produces rows that are within budget
# and indistinguishable, which is worse than honestly showing fewer of them.
# 20 characters only bites for a name no taskbar could show anyway.
_MAX_NAME_CHARS = 20
_MAX_ERROR_CHARS = 24
_MAX_WARNING_CHARS = 60


def _clip(text: str, limit: int) -> str:
    """Shorten `text` to `limit` characters, marking that it was shortened.

    Explicit, never the OS's silent buffer truncation -- the distinction the
    budget requirement exists to draw.
    """
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + _ELLIPSIS


def _meta_warning(data: dict[str, Any]) -> str | None:
    meta = data.get("meta")
    warning = meta.get("warning") if isinstance(meta, dict) else None
    return warning if isinstance(warning, str) and warning else None


def _profile_tooltip_line(name: str, data: dict[str, Any], *, level: int) -> str:
    """One profile's line, reduced to `level`.

    Every quota the profile has appears at `_LEVEL_FULL`, `weekly_fable`
    included, and an active warning appears at every level -- the two things
    the previous multi-profile branch dropped outright (tray-tooltip spec,
    "Multi-Profile Content Parity").
    """
    display = _clip(name, _MAX_NAME_CHARS)
    session = pct(data.get("session"))
    weekly = pct(data.get("weekly"))
    fable = pct(data.get("weekly_fable"))
    suffix = _WARNING_MARK if _meta_warning(data) else ""

    if session is None and weekly is None and fable is None:
        error = str(data.get("error") or "Sin datos")
        if level > _LEVEL_FULL:
            error = _clip(error, _MAX_ERROR_CHARS)
        return f"{display}: {error}"

    parts: list[str] = []
    if session is not None:
        parts.append(f"5h {session:.0f}%")
    if weekly is not None:
        parts.append(f"7d {weekly:.0f}%")
    if fable is not None and level < _LEVEL_NO_FABLE:
        parts.append(f"F {fable:.0f}%")
    return f"{display}: {' · '.join(parts)}{suffix}"


def _render(lines: list[str], omitted: int) -> str:
    body = [_TOOLTIP_HEADER, *lines]
    if omitted > 0:
        body.append(f"+{omitted} mas")
    return "\n".join(body)


def _fit_profiles(lines: list[str]) -> str:
    """Keep as many profile lines as fit, and say how many were left out.

    The marker is accounted for *before* a line is accepted, so the returned
    string is always within budget and the count it reports is always the
    truth -- no profile is ever dropped without the marker being present
    (tray-tooltip spec, "Explicit Truncation When Profile Count Exceeds
    Capacity").
    """
    kept: list[str] = []
    for index, line in enumerate(lines):
        candidate = [*kept, line]
        if len(_render(candidate, len(lines) - len(candidate))) > TOOLTIP_BUDGET:
            break
        kept = candidate
    return _render(kept, len(lines) - len(kept))


def _multi_profile_tooltip(profiles: list[Any]) -> str:
    named = [
        (_profile_display_name(item.get("profile"), "Perfil"), item.get("data") or {})
        for item in profiles
        if isinstance(item, dict)
    ]
    lines: list[str] = []
    for level in (_LEVEL_FULL, _LEVEL_NO_FABLE):
        lines = [_profile_tooltip_line(name, data, level=level) for name, data in named]
        rendered = _render(lines, 0)
        if len(rendered) <= TOOLTIP_BUDGET:
            return rendered
    # Fully reduced and still over: the profile count itself is the problem,
    # so drop whole profiles and mark it. `profiles[:4]`, which the old code
    # did silently and unconditionally, is exactly what this replaces.
    return _fit_profiles(lines)


def _single_profile_tooltip(data: dict[str, Any]) -> str:
    session = pct(data.get("session"))
    weekly = pct(data.get("weekly"))
    fable = pct(data.get("weekly_fable"))
    warning = _meta_warning(data)

    # (priority, text). Priority 0 never drops: the header, and both halves of
    # a warning. Higher numbers are surrendered first, highest first -- the
    # tertiary quota before the secondary, numbers before warnings.
    lines: list[tuple[int, str]] = [(0, _TOOLTIP_HEADER)]
    if warning:
        lines.append((0, _STALE_DATA_NOTE))
    if session is not None:
        lines.append((2, f"5h: {session:.0f}%"))
    if weekly is not None:
        lines.append((3, f"7d: {weekly:.0f}%"))
    if fable is not None:
        lines.append((4, f"Fable: {fable:.0f}%"))
    if warning:
        lines.append((0, _clip(warning, _MAX_WARNING_CHARS)))

    def rendered() -> str:
        return "\n".join(text for _, text in lines)

    while len(rendered()) > TOOLTIP_BUDGET:
        droppable = max((priority for priority, _ in lines), default=0)
        if droppable == 0:
            break  # only warning-related content left; it outranks the budget
        for index, (priority, _) in enumerate(lines):
            if priority == droppable:
                del lines[index]
                break

    # Backstop for the unreachable: the loop above cannot leave anything over
    # budget for realistic input (the header plus a clipped warning is ~115
    # characters at worst), but the requirement is absolute -- never hand the
    # OS something to truncate for us.
    return _clip(rendered(), TOOLTIP_BUDGET)


def tooltip(bundle: dict[str, Any]) -> str:
    """The native tray tooltip's content, always within `TOOLTIP_BUDGET`.

    This is the fallback path: when hover tracking cannot resolve the tray
    icon's rectangle, `app.py` leaves this string on `Icon.title` and the OS
    renders it (tray-tooltip spec, "Native Tooltip Remains the Fallback").
    The custom hover popup uses `hover_sections` below, which has no budget
    to spend.
    """
    profiles = bundle.get("profiles")
    if isinstance(profiles, list) and profiles:
        return _multi_profile_tooltip(profiles)

    data = bundle.get("primary", bundle)
    if data.get("error") and pct(data.get("session")) is None and pct(data.get("weekly")) is None:
        return _clip(f"Claude uso\n{data['error']}", TOOLTIP_BUDGET)

    return _single_profile_tooltip(data)


# ---------------------------------------------------------------------------
# Custom hover popup content
#
# Structured, not a string, and with no character budget: the popup is our own
# window, so the only limits are the screen's. Quota windows are named by key
# rather than by a Spanish label because `ui/hover_popup.py` owns that copy,
# exactly as `ui/popup.py` already owns its own row labels.
# ---------------------------------------------------------------------------

# In the order the popup renders them. Matches `alerts.QUOTA_WINDOWS` and
# `api.fetch_usage`'s payload keys.
HOVER_QUOTA_KEYS: tuple[str, ...] = ("session", "weekly", "weekly_fable")


@dataclass(frozen=True)
class HoverSection:
    """One profile's block in the hover popup.

    `quotas` carries only the windows the poll actually reported, so a
    profile without Fable data renders without a Fable row rather than with
    an empty one.
    """

    title: str
    quotas: tuple[tuple[str, float], ...]
    warning: str | None = None
    error: str | None = None


def hover_sections(bundle: dict[str, Any]) -> list[HoverSection]:
    """Build the hover popup's content from a usage bundle.

    Every configured profile gets a section -- there is no `[:4]` here, and
    no budget that could justify one. Each section carries every quota the
    poll reported plus that profile's own warning, which is the same parity
    rule the native tooltip follows under far tighter constraints.
    """
    profiles = bundle.get("profiles")
    if isinstance(profiles, list) and profiles:
        return [
            _hover_section(
                _profile_display_name(item.get("profile"), f"Perfil {index + 1}"),
                item.get("data") or {},
            )
            for index, item in enumerate(profiles)
            if isinstance(item, dict)
        ]
    return [_hover_section("Perfil", bundle.get("primary", bundle))]


def _hover_section(title: str, data: dict[str, Any]) -> HoverSection:
    quotas: list[tuple[str, float]] = []
    for key in HOVER_QUOTA_KEYS:
        value = pct(data.get(key))
        if value is not None:
            quotas.append((key, value))
    error = data.get("error")
    return HoverSection(
        title=title,
        quotas=tuple(quotas),
        warning=_meta_warning(data),
        error=str(error) if error and not quotas else None,
    )


def availability_pct(data: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in ("session", "weekly", "weekly_fable"):
        used = pct(data.get(key))
        if used is None:
            continue
        values.append(max(0.0, min(100.0, 100.0 - used)))
    if not values:
        return None
    return min(values)


def availability_pct_from_bundle(bundle: dict[str, Any]) -> float | None:
    profile_items = bundle.get("profiles")
    values: list[float] = []
    if isinstance(profile_items, list) and profile_items:
        for item in profile_items:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if isinstance(data, dict):
                availability = availability_pct(data)
                if availability is not None:
                    values.append(availability)
    else:
        data = bundle.get("primary", bundle)
        if isinstance(data, dict):
            availability = availability_pct(data)
            if availability is not None:
                values.append(availability)
    if not values:
        return None
    return min(values)


def tray_level(availability: float | None) -> int:
    if availability is None:
        return 0
    if availability >= 80:
        return 4
    if availability >= 50:
        return 3
    if availability >= 20:
        return 2
    return 1


def updated_text(updated_at: datetime | None, *, now: datetime | None = None) -> str:
    if updated_at is None:
        return "Ultima actualizacion: sin datos"
    current = now if now is not None else datetime.now()
    diff_seconds = max(0, int((current - updated_at).total_seconds()))
    if diff_seconds < 60:
        human = "hace menos de un minuto"
    else:
        minutes = diff_seconds // 60
        human = f"hace {minutes} min"
    return f"Ultima actualizacion: {human}"


def parse_meta_datetime(value: str | None, *, tz: tzinfo | None = None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(tz).replace(tzinfo=None)
    except Exception:
        return None
