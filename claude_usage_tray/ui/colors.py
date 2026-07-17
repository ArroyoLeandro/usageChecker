"""claude_usage_tray.ui.colors -- the colour section's model, as pure logic.

No tkinter, ever -- the same split `scroll.py`, `hover.py` and `dismiss.py`
keep, and for the same reason: tkinter is not installed in this dev/CI
environment, so anything that must be *decided* correctly has to be decidable
without a display. `settings_window.py`'s colour section is the widget half
and holds no rule of its own.

**The rule this module exists to hold.** `Settings.colors` is a *sparse* map:
an absent field takes the preset's value, which is what makes switching
Oscuro -> Claro work at all, and what let every pre-existing `config.json`
migrate with no code. The colour rows now *display* the preset's colour
instead of an empty box -- and displaying a value must not mean the user
pinned it. Pre-filling all nine fields and then persisting them would leave
nine dark overrides sitting on top of the light preset: the theme switch would
look broken, and the user would have "chosen" nine colours they never picked.

So the two questions are kept apart, by name:

* `ColorRow.value` -- what the field **shows**: the override if there is one,
  else the preset's own colour. Always a real, displayable hex.
* `ColorRow.pinned` -- whether the user **chose** it, i.e. whether the field
  is present in the stored sparse map. This, and only this, is what
  `submitted()` will persist.

`with_pick` and `with_revert` are the only two ways a field becomes (or stops
being) pinned, and both take the stored map rather than the displayed text --
so no amount of pre-filling can pin anything by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Mapping

from ..theme import PALETTE_FIELDS, Palette, parse_hex_color


@dataclass(frozen=True)
class ColorRow:
    """One palette field, as the settings UI needs to draw it.

    `field` is the `Palette` field name, not user-facing copy --
    `settings_window.py` owns the Spanish label, the same split `theme.py`'s
    `ContrastCheck` already keeps.
    """

    field: str
    # The hex this field renders as. Never blank: an un-overridden field
    # reports the preset's colour rather than "" (that is the whole point of
    # the pre-fill), so the UI has something real to show and to seed the
    # colour picker with.
    value: str
    # Whether `value` came from the user's stored override rather than from
    # the preset. The distinction the sparse map lives or dies by: `value` is
    # what you see, `pinned` is what gets saved.
    pinned: bool
    # Whether this field has moved since the window was opened -- i.e.
    # whether "volver al anterior color" has anything to go back to.
    revertable: bool


def effective(colors: Mapping[str, str], preset: Palette, field: str) -> str:
    """The hex `field` actually renders as: the override, else the preset's.

    Mirrors `theme.with_overrides` for a single field, and tolerates the same
    junk for the same reason: an unparseable stored value falls back to the
    preset rather than raising, so a hand-edited `config.json` can never make
    this return something Tk would choke on.
    """
    override = colors.get(field)
    parsed = parse_hex_color(override) if override is not None else None
    if parsed is not None:
        return parsed
    return getattr(preset, field)


def color_rows(
    colors: Mapping[str, str],
    preset: Palette,
    baseline: Mapping[str, str],
) -> tuple[ColorRow, ...]:
    """The nine rows to draw, in `PALETTE_FIELDS` order.

    `baseline` is the stored map as it was when the window was opened -- see
    `with_revert` for why that is the definition of "previous".
    """
    return tuple(
        ColorRow(
            field=field,
            value=effective(colors, preset, field),
            pinned=field in colors,
            revertable=colors.get(field) != baseline.get(field),
        )
        for field in PALETTE_FIELDS
    )


def with_pick(colors: Mapping[str, str], field: str, value: Any) -> dict[str, str]:
    """`colors` with `field` pinned to `value` -- the user choosing a colour.

    Returns a fresh dict; `colors` is never mutated. An unknown field or an
    unparseable value yields `colors` unchanged rather than raising: the only
    caller is a picker whose reply we do not control, and a colour we cannot
    read is a colour not picked.
    """
    updated = dict(colors)
    if field not in PALETTE_FIELDS:
        return updated
    parsed = parse_hex_color(value)
    if parsed is None:
        return updated
    updated[field] = parsed
    return updated


def with_revert(
    colors: Mapping[str, str],
    baseline: Mapping[str, str],
    field: str,
) -> dict[str, str]:
    """`colors` with `field` put back the way it was when the window opened.

    "Previous" is the baseline, not one step of undo history: the user asked
    to "volver al anterior color", and with the window rebuilding itself on
    every pick there is exactly one previous colour worth naming -- the one
    they walked in with. A stack would also have to survive those rebuilds and
    then answer what a second press means; a baseline answers "nothing, you
    are already there" and needs no state at all.

    Note this restores *pinnedness*, not merely the hex. A field the baseline
    never pinned goes back to being absent -- inheriting the preset again --
    rather than being pinned to the colour the preset happened to have. Those
    two look identical until the user switches preset, at which point only the
    first one is right.
    """
    updated = dict(colors)
    if field not in PALETTE_FIELDS:
        return updated
    original = baseline.get(field)
    parsed = parse_hex_color(original) if original is not None else None
    if parsed is None:
        updated.pop(field, None)
    else:
        updated[field] = parsed
    return updated


def submitted(
    texts: Mapping[str, str],
    pinned: Collection[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """The overrides to persist, plus the fields whose text was unreadable.

    **Only pinned fields are submitted.** This is the trap the whole module
    exists for: every row now carries text, because every row is pre-filled
    with the colour it renders as, so "has text" stopped meaning "the user
    chose this" the moment the pre-fill landed. Submitting on text would
    persist all nine and quietly break the preset switch.

    Returns `(overrides, invalid_field_names)`. `invalid` is reported rather
    than dropped, and the caller is expected to apply nothing while it is
    non-empty: a partial apply would leave the window rebuilt, the bad field
    reverted, and no way to tell which of the nine had been mistyped.
    """
    overrides: dict[str, str] = {}
    invalid: list[str] = []
    for field in PALETTE_FIELDS:
        if field not in pinned:
            continue
        parsed = parse_hex_color(texts.get(field, ""))
        if parsed is None:
            invalid.append(field)
            continue
        overrides[field] = parsed
    return overrides, tuple(invalid)


def normalize_choice(rgb: Any, text: Any) -> str | None:
    """Normalize `colorchooser.askcolor()`'s reply to `#rrggbb`, or `None`.

    `None` means "no colour": the user cancelled, or the reply was something
    we cannot read. Both end the same way -- nothing is pinned.

    Both halves of that reply are less dependable than they look, and this is
    a seam no test on a display-less box can reach, which is exactly the class
    of bug this codebase keeps paying for:

    * The **text** is whatever Tcl handed back. Tk's `tk_chooseColor` spells
      it `#rrggbb` on the platforms this app ships to, but Tk colours are also
      legally `#rrrgggbbb` / `#rrrrggggbbbb`, and `parse_hex_color` rejects
      those by design (it needs 8 bits per channel to measure contrast). A
      reply we merely could not *spell* would look exactly like a cancel.
    * The **tuple** is `(r/256, g/256, b/256)` -- `tkinter.colorchooser`
      divides `winfo_rgb`'s 16-bit channels, so these are **floats** in
      `0..255.996`, not the ints the shape suggests.

    So the text is tried first (it is the canonical answer when it is
    readable) and the tuple is the fallback, coerced and clamped rather than
    trusted. Either path yields the one spelling everything downstream --
    storage, contrast math, Tk -- is allowed to see.
    """
    parsed = parse_hex_color(text)
    if parsed is not None:
        return parsed
    channels = _channels(rgb)
    if channels is None:
        return None
    return "#" + "".join(f"{value:02x}" for value in channels)


def _channels(rgb: Any) -> tuple[int, int, int] | None:
    """`rgb` as three 0..255 ints, or `None` if it is not a colour triple."""
    if not isinstance(rgb, (tuple, list)) or len(rgb) != 3:
        return None
    values: list[int] = []
    for channel in rgb:
        if isinstance(channel, bool) or not isinstance(channel, (int, float)):
            return None
        values.append(max(0, min(255, int(channel))))
    return values[0], values[1], values[2]
