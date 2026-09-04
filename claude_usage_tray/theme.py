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


# ---------------------------------------------------------------------------
# Interaction shades
#
# A button has to look different while the pointer is over it and different
# again while it is held down, and those two surfaces cannot be palette
# fields: the palette has nine colors and `ui/widgets.py` builds buttons on
# `bg`, `panel`, `accent` and -- in the colour picker -- on whatever hex the
# user just typed. There is no table that covers that; the shades have to be
# *derived* from the background the call site passes, or a user whose accent
# is green gets an orange hover.
#
# They live here, next to `contrast_ratio`, for the same reason parsing does:
# they are palette facts, they are pure, and they are decidable with no
# display -- which is the only way any of this gets tested at all.
# ---------------------------------------------------------------------------

_WHITE = "#ffffff"
_BLACK = "#000000"

# How far toward that extreme each state moves. Small on purpose: these are
# feedback, not a second palette. The pressed step is roughly twice the hover
# step so the two read as one gesture deepening rather than two colors.
HOVER_MIX = 0.09
PRESSED_MIX = 0.18

# How far a disabled label falls back toward its own surface.
DISABLED_MIX = 0.55

# The contrast a surface must have against an extreme for a `PRESSED_MIX`
# step toward it to be *visible*. Below this the surface is already sitting
# on that extreme and the step rounds away to nothing: `#fbfaf7` moved 9% of
# the way to white is `#fbfaf7` again. 2:1 is the point where a 9% step is
# still a couple of levels per channel on every palette this app ships.
MIN_SHADE_CONTRAST = 2.0

# WCAG 2.x 1.4.11 (non-text contrast): what a focus ring, being a graphical
# indicator rather than text, has to clear. Distinct from
# `AA_CONTRAST_RATIO` above, which governs label text at 4.5:1.
RING_MIN_CONTRAST = 3.0


def mix_colors(color: str, other: str, amount: float) -> str:
    """Blend `color` `amount` of the way toward `other`, per channel.

    `amount` is clamped to `[0, 1]`; 0 returns `color`, 1 returns `other`.
    Raises `ValueError` on an unparseable input, like `relative_luminance`
    and for the same reason -- both ends come off a `Palette` or off a value
    `parse_hex_color` already accepted.

    Linear in sRGB space rather than in luminance: this is a nudge of a
    surface, not a contrast calculation, and sRGB is the space the result is
    handed back to Tk in. Doing it in linear light would make the same
    `amount` produce visibly different steps on dark and light palettes.
    """
    start, end = parse_hex_color(color), parse_hex_color(other)
    if start is None:
        raise ValueError(f"not a hex color: {color!r}")
    if end is None:
        raise ValueError(f"not a hex color: {other!r}")
    ratio = min(1.0, max(0.0, amount))
    channels = (
        round(
            int(start[index : index + 2], 16)
            + (int(end[index : index + 2], 16) - int(start[index : index + 2], 16)) * ratio
        )
        for index in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def shade_target(background: str, foreground: str) -> str:
    """Which extreme -- `#000000` or `#ffffff` -- `background` shades toward.

    The first choice is *away from the text*: moving the surface away from
    the colour written on it can only make the label easier to read while it
    is being pressed, never harder.

    The second choice overrules it when the first has nowhere to go. A
    surface already near an extreme has no room to move further that way --
    `panel` in the light preset is `#fbfaf7`, and 9% of the remaining
    distance to white is less than one level per channel, so "away from the
    dark text" would render a button that does not visibly react at all.
    `MIN_SHADE_CONTRAST` is the test for that, and it is measured with
    `contrast_ratio` rather than by comparing hex values so the answer is the
    same fact the rest of this module reasons in.

    Both branches matter in practice. A user running a `#000000` panel gets
    the second (black has no room to darken, so its buttons lighten); the
    default dark preset gets it too; a saturated accent like `#11a800` under
    light text gets the first, and deepens.
    """
    away = _BLACK if relative_luminance(foreground) > relative_luminance(background) else _WHITE
    if contrast_ratio(background, away) >= MIN_SHADE_CONTRAST:
        return away
    return _WHITE if away == _BLACK else _BLACK


def interaction_shades(background: str, foreground: str) -> tuple[str, str]:
    """The `(hover, pressed)` surfaces for a button drawn `foreground`-on-`background`.

    Derived, never looked up: the caller's own `background` is the only input
    that decides the hue, so a custom accent shades to itself and a custom
    panel shades to itself. The pair is always two *distinct* colours -- that
    is the whole defect being fixed, since the buttons this replaces set
    `activebackground` equal to their own background and therefore
    acknowledged a click with nothing at all.
    """
    target = shade_target(background, foreground)
    return mix_colors(background, target, HOVER_MIX), mix_colors(background, target, PRESSED_MIX)


def disabled_foreground(foreground: str, background: str) -> str:
    """The label colour of a disabled button: `foreground`, half dissolved into its surface.

    Dimming toward the *surface* rather than toward grey is what keeps this
    honest on a custom palette -- a fixed grey would be a foreign colour on a
    green button and, on a grey panel, would not read as disabled at all.
    """
    return mix_colors(foreground, background, DISABLED_MIX)


def focus_ring(background: str, *candidates: str) -> str:
    """The first of `candidates` visible enough to ring `background`, else the most visible.

    A keyboard focus ring that cannot be seen is not a focus ring, so this
    ends in a guarantee: black and white are appended to whatever the caller
    offered, and one of them always clears the floor -- the worst possible
    surface still measures 4.58:1 against the better of the two. Nothing a
    user can put in a palette can produce an invisible ring.

    *First* clearing the floor rather than *most* contrasting, because these
    are ranked by intent and not only by measurement. A ring in the button's
    own text colour looks deliberate; a black ring is merely legible. Taking
    the maximum would pick black nearly every time and throw the palette
    away, so the palette wins whenever it is good enough and only then.

    Two candidates cannot be assumed to work and are why this is measured at
    all: the colour-picker swatches pass a foreground that *is* their
    background, and `accent` on the dark preset carries `fg` at 2.83:1 --
    documented above as a conceded tradeoff, and below this floor.

    `RING_MIN_CONTRAST` is WCAG 2.x 1.4.11, which governs non-text
    indicators like this one rather than the 4.5:1 that governs label text.
    """
    if not candidates:
        raise ValueError("focus_ring needs at least one candidate")
    ranked = (*candidates, _WHITE, _BLACK)
    for candidate in ranked:
        if contrast_ratio(candidate, background) >= RING_MIN_CONTRAST:
            return candidate
    return max(ranked, key=lambda candidate: contrast_ratio(candidate, background))


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
