"""claude_usage_tray.ui.button_state -- what a flat button looks like, decided without a window.

Everything `widgets.FlatButton` has to *decide* lives here, and imports no
tkinter: which surface belongs to which interaction state, whether a mouse
release counts as a click, and which of the options handed to `configure()`
the button owns rather than forwards. What is left in `widgets.py` is the
event wiring those answers drive -- the same split `scroll.py` already has
with `ScrollableView`, and for the same reason: this dev/CI box has no
display, so a rule that lives in a widget is a rule no test can reach.

The button exists because Tk on macOS draws `tk.Button` with the *native*
Aqua control and silently ignores `bg`, `fg`, `activebackground`,
`relief` and `bd`. Every button in this app is a coloured rectangle on a
themed panel, so on macOS every one of them rendered as a grey system pill
regardless of the palette. `tk.Label` is drawn by Tk itself on all three
platforms and honours those options, which is why the replacement is built
on a Label and why it has to re-implement, by hand, the press/hover/focus
behaviour `tk.Button` used to provide.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..theme import Palette, disabled_foreground, focus_ring, interaction_shades


@dataclass(frozen=True)
class ButtonColors:
    """Every surface one button can paint, resolved once from one `(bg, fg)` pair.

    Frozen and precomputed rather than derived per event: the arithmetic is
    cheap but it is also *the* thing that must not drift between a hover and
    the release that follows it, and a value class makes that structural
    instead of careful.
    """

    background: str
    hover: str
    pressed: str
    foreground: str
    disabled: str
    ring: str


def resolve(background: str, foreground: str, palette: Palette) -> ButtonColors:
    """Build the surface set for a button drawn `foreground`-on-`background`.

    `palette` is consulted for nothing but focus-ring fallbacks. The hover
    and pressed surfaces come from `background` alone, which is the property
    that makes a custom palette work at all: a user whose accent is green
    presses a green button and sees a deeper green, because no step of this
    ever reads a preset.
    """
    hover, pressed = interaction_shades(background, foreground)
    return ButtonColors(
        background=background,
        hover=hover,
        pressed=pressed,
        foreground=foreground,
        disabled=disabled_foreground(foreground, background),
        # The text colour first, because on nearly every button it is both
        # the most visible option and the one that looks deliberate. The
        # palette's two strongest colours follow it for the swatches, whose
        # foreground *is* their background and would otherwise ring in a
        # colour indistinguishable from the fill.
        ring=focus_ring(background, foreground, palette.fg, palette.accent),
    )


def surface(
    colors: ButtonColors,
    *,
    enabled: bool,
    hovering: bool,
    pressed: bool,
) -> tuple[str, str]:
    """The `(background, foreground)` to paint for one interaction state.

    `pressed` only darkens while the pointer is still over the button:
    dragging off a held button has to look like the click it is about to
    become, which is none. That is the same fact `released_inside` decides
    for the click itself, expressed for the eye instead of for the handler.
    """
    if not enabled:
        return colors.background, colors.disabled
    if pressed and hovering:
        return colors.pressed, colors.foreground
    if hovering:
        return colors.hover, colors.foreground
    return colors.background, colors.foreground


def released_inside(x: int, y: int, width: int, height: int) -> bool:
    """Does a release at widget-local `(x, y)` land on a `width` x `height` button?

    A real button fires on release, not on press, and only if the release
    happens where the press did -- pressing and dragging away is how a user
    takes back a click they did not mean. `tk.Button` gave this for free;
    a Label does not, so it is stated here where it can be tested.

    The bounds are half-open on purpose. Tk reports a release at `x == width`
    when the pointer has just left the right edge, so treating that as inside
    would fire the command for a click on the neighbouring widget.
    """
    return 0 <= x < width and 0 <= y < height


# Options `FlatButton` answers itself instead of handing to `tk.Label`.
#
# `bg`/`fg` (and their long spellings) are owned because changing either has
# to rebuild the whole `ButtonColors` set, not just repaint one field --
# `settings_window`'s colour picker reconfigures a swatch's `bg` on every
# keystroke, and a swatch whose hover still belonged to the previous colour
# would be a live lie about what was typed.
#
# `command` and `state` are owned because `tk.Label` has no such options at
# all, and `activebackground`/`activeforeground` because it has no such
# options *either* -- the pre-existing call in `settings_window.refresh`
# passes `activebackground` verbatim, and a `TclError` there would take out
# the colour section on a machine no headless test can stand in for.
OWNED_OPTIONS = frozenset(
    {
        "command",
        "state",
        "bg",
        "background",
        "fg",
        "foreground",
        "activebackground",
        "activeforeground",
    }
)

# Accepted and then dropped, deliberately. On `tk.Button` these named the
# pressed surface, and every call site in this app set them equal to the
# button's own background -- which is exactly why nothing here ever
# acknowledged a click. Honouring them would faithfully reproduce the defect,
# so the derived shades win and these are tolerated for compatibility only.
IGNORED_OPTIONS = frozenset({"activebackground", "activeforeground"})

# Tk's own spellings, normalised so a caller may use either.
ALIASES = {"background": "bg", "foreground": "fg"}


def split_options(options: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """Partition `configure()` keywords into `(owned, forwarded)`.

    Owned keys come back under their short spelling (`background` -> `bg`)
    and with the ignored ones already dropped, so the caller has one shape to
    branch on rather than four.
    """
    owned: dict[str, object] = {}
    forwarded: dict[str, object] = {}
    for name, value in options.items():
        if name in IGNORED_OPTIONS:
            continue
        if name in OWNED_OPTIONS:
            owned[ALIASES.get(name, name)] = value
        else:
            forwarded[name] = value
    return owned, forwarded


def is_enabled(state: object) -> bool:
    """Read Tk's `state` option as the one bit this button acts on.

    `tk.Button` distinguishes `normal` from `active` (the latter meaning
    "the pointer is over me"), but this button tracks hover from real
    `<Enter>`/`<Leave>` events, so `active` is just another way of saying not
    disabled. Anything unrecognised counts as enabled: a mistyped state must
    not silently produce a button that cannot be clicked.
    """
    return str(state) != "disabled"
