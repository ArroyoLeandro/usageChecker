"""claude_usage_tray.theme -- palette + font roles as data.

No tkinter, ever: `Theme.font(role)` returns a plain `(family, size, weight)`
tuple, never a `tkinter.font.Font` object. Both the popup and the profiles
window are destroyed and rebuilt on every show, so there is nothing to
live-mutate -- a `Font` object would only buy a capability this app never
needs, while dragging tkinter into this module and forfeiting headless
testability. See design.md's "Theme returns font tuples" decision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class Palette:
    bg: str
    panel: str
    track: str
    fg: str
    muted: str
    accent: str
    warn: str
    danger: str
    border: str


# The nine `Palette` fields, in the order the settings UI lists them. Derived
# from the dataclass rather than retyped, so a field added to `Palette` can
# never silently miss the picker or the contrast report.
PALETTE_FIELDS: tuple[str, ...] = tuple(Palette.__dataclass_fields__)


# `app.py:36`'s `LINK = "#8ab4ff"` was dead code (defined, referenced
# nowhere) and is deliberately not carried over -- `Palette` has nine
# fields, not ten.
DARK = Palette(
    bg="#262522",
    panel="#2f2d29",
    track="#4a4743",
    fg="#f7f3ee",
    muted="#b9b1a6",
    accent="#d97757",
    warn="#f59e0b",
    danger="#ef4444",
    border="#3a3733",
)

# The light theme, resolving design.md's open question (slice c).
#
# Derived from DARK rather than invented beside it: every neutral keeps the
# same warm hue family (h ~20-45 deg, the same low-saturation stone the dark
# palette uses), and the luminance *relationships* are mirrored, not merely
# inverted -- `panel` stays the elevated surface (lighter than `bg`, 1.12:1
# in both themes), `track` stays the recessed groove, `border` stays a hair
# off `panel`.
#
# Contrast, measured (WCAG 2.x relative luminance), AA needs 4.5:1 for text:
#   fg    on bg    13.72:1     fg    on panel 15.36:1
#   muted on bg     6.21:1     muted on panel  6.95:1
#   warn  on bg     5.00:1     warn  on panel  5.59:1
#
# `accent` stays #d97757 exactly -- it is Claude's orange and the one value
# that must stay recognizable across both themes. That pins a real tradeoff
# rather than hiding it: the accent is BOTH a fill behind `fg` text (the
# primary buttons) and a text color on `bg` (the profile status line). One
# hex cannot serve both on a light background -- a lighter orange fails as
# text, a darker one fails under the button's dark label. Holding it fixed
# picks the button (fg on accent: 2.83:1 dark -> 5.14:1 light, a real
# improvement) and concedes the status line (accent on bg: 4.91:1 dark ->
# 2.67:1 light, below AA). Recorded here so it is a decision, not a defect.
#
# `warn` and `danger` are NOT held fixed: both are used as text or as a small
# glyph, and the dark theme's values (#f59e0b, #ef4444) are tuned for a dark
# backdrop -- #f59e0b as text on #f0ede7 is 1.9:1, unreadable. They darken to
# the same hue at a light-appropriate luminance.
#
# These now reach the progress-bar fills too: `formatting.bar_color()` takes
# an optional palette and `ui/widgets.py` passes one, so bars are themed
# rather than identical everywhere (they were the latter for as long as no
# custom palette could exist to expose it). That is an improvement here and
# not merely a change: a bar fill is a graphical object, which WCAG 1.4.11
# asks for 3:1 against its surround, and #f59e0b on this palette's `panel`
# (#fbfaf7) is 1.9:1 -- the darkened value clears the bar that the dark
# preset's value never could on a light backdrop.
LIGHT = Palette(
    bg="#f0ede7",
    panel="#fbfaf7",
    track="#dcd7cd",
    fg="#232120",
    muted="#5c564d",
    accent="#d97757",
    warn="#a8480a",
    danger="#c62828",
    border="#d5cfc5",
)


FontRole = Literal["small", "small_bold", "body", "body_bold", "heading", "title"]

# (size offset from Theme.base_size, is_bold). Measured directly from
# app.py: 26 `font=` occurrences, all "Segoe UI", in exactly these seven
# distinct shapes collapsed to six roles (small/small_bold share a size).
_FONT_OFFSETS: dict[FontRole, tuple[int, bool]] = {
    "small": (-1, False),
    "small_bold": (-1, True),
    "body": (0, False),
    "body_bold": (0, True),
    "heading": (1, True),
    "title": (2, True),
}


@dataclass(frozen=True)
class Theme:
    palette: Palette
    family: str = "Segoe UI"
    base_size: int = 9

    def font(self, role: FontRole) -> tuple[str, int] | tuple[str, int, str]:
        offset, bold = _FONT_OFFSETS[role]
        size = self.base_size + offset
        if bold:
            return (self.family, size, "bold")
        return (self.family, size)


# The default every UI entry point should use. A `Palette` is NOT a usable
# default for a `theme:` parameter: it carries colors but no `.palette` and
# no `.font()`, so passing one raises `AttributeError` at render time --
# which is exactly the bug that shipped, in `ui/popup.py` and
# `ui/profiles_window.py`'s own defaults and in `app.py`'s call sites.
DEFAULT_THEME = Theme(palette=DARK)


# ---------------------------------------------------------------------------
# Custom hex colors
#
# `DARK`/`LIGHT` above are presets -- a starting point, not the whole set. A
# user may override any subset of the nine fields with their own hex value
# (user-settings spec, "Custom Hex Palettes"). Parsing and contrast math live
# here rather than in `settings.py` because they are palette facts, not
# preference facts: `icons.py` and `ui/` reason about a `Palette` and must be
# able to ask the same questions without importing the settings model.
# ---------------------------------------------------------------------------

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def parse_hex_color(value: Any) -> str | None:
    """Normalize a user-entered hex color to canonical `#rrggbb`, or `None`.

    Accepts `#rgb` and `#rrggbb`, case-insensitively, and expands the short
    form (`#f0a` -> `#ff00aa`) so everything downstream -- storage, contrast
    math, Tk -- only ever sees one spelling. Returns `None` for anything
    unusable rather than raising: every caller has a preset to fall back to,
    and a mistyped color is not worth an exception.

    Deliberately stricter than Tk, which also accepts named colors
    (`"red"`), `#rrrgggbbb` and `#rrrrggggbbbb`. Narrow is right here: the
    settings UI shows a hex field, `contrast_ratio` below needs 8 bits per
    channel to compute anything, and accepting a spelling we cannot measure
    would put an unauditable color in a persisted palette.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw.startswith("#"):
        return None
    body = raw[1:]
    if len(body) not in (3, 6) or any(char not in _HEX_DIGITS for char in body):
        return None
    if len(body) == 3:
        body = "".join(char * 2 for char in body)
    return f"#{body.lower()}"


def with_overrides(palette: Palette, overrides: Mapping[str, str]) -> Palette:
    """Return `palette` with every valid override in `overrides` applied.

    Sparse by design: an absent field keeps the preset's value, so a user who
    only ever changes `accent` still tracks preset improvements to the other
    eight. Unknown keys and unparseable values are ignored -- validation
    belongs to whoever accepted the input (`settings.load`), and this
    function must never be able to build a `Palette` Tk would choke on.
    """
    accepted: dict[str, str] = {}
    for name, value in overrides.items():
        if name not in PALETTE_FIELDS:
            continue
        parsed = parse_hex_color(value)
        if parsed is not None:
            accepted[name] = parsed
    return replace(palette, **accepted) if accepted else palette


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a `#rgb`/`#rrggbb` string.

    Raises `ValueError` on an unparseable color -- unlike `parse_hex_color`,
    which is the tolerant boundary. By the time a color reaches here it came
    off a `Palette`, and every `Palette` is built from parsed values, so an
    unparseable one is a bug in this module rather than bad user input.
    """
    normalized = parse_hex_color(color)
    if normalized is None:
        raise ValueError(f"not a hex color: {color!r}")
    raw = normalized.lstrip("#")
    channels = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two colors. Symmetric; >= 1.0."""
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# WCAG 2.x AA for normal-size text. The built-in presets are gated on this as
# a hard test failure (`tests/test_theme.py`); a user's own colors are only
# *warned* about, never blocked -- see `contrast_report`.
AA_CONTRAST_RATIO = 4.5

# The pairs that actually render as text on a surface in this app. Not every
# combination of the nine fields: `track` is a groove behind a bar, `border`
# is a hairline, and `accent` is both a fill and a text color (see LIGHT's
# comment), so measuring them as text would report failures that mean
# nothing. These five are exactly the pairs the preset gate asserts.
TEXT_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("fg", "bg"),
    ("muted", "bg"),
    ("fg", "panel"),
    ("muted", "panel"),
    ("warn", "panel"),
)


@dataclass(frozen=True)
class ContrastCheck:
    """One measured foreground-on-background pair from `TEXT_CONTRAST_PAIRS`.

    `foreground`/`background` are `Palette` *field names*, not hex values, so
    the UI can label them in Spanish without this module holding user-facing
    copy.
    """

    foreground: str
    background: str
    ratio: float

    @property
    def passes(self) -> bool:
        return self.ratio >= AA_CONTRAST_RATIO


def contrast_report(palette: Palette) -> list[ContrastCheck]:
    """Measure every text pair in `palette`, whether it passes or not.

    Returns measurements, never a verdict, and raises nothing: a custom
    palette that fails AA is a thing the user is entitled to choose, so the
    UI's job is to *say so*, not to refuse it. The presets keep a hard gate
    because they are ours, not theirs.
    """
    return [
        ContrastCheck(
            foreground=foreground,
            background=background,
            ratio=contrast_ratio(getattr(palette, foreground), getattr(palette, background)),
        )
        for foreground, background in TEXT_CONTRAST_PAIRS
    ]
