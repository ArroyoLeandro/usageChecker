"""claude_usage_tray.ui.settings_window -- the "Configuracion" tab's content.

Surfaces what the user-settings and usage-alerts specs require a control for:
the theme preset (`dark`/`light`), the font size, custom colors, and alert
thresholds -- globally and per profile.

**This module no longer owns a window.** It builds its content into a frame
`main_window.py` hands it, which is the `ScrollableView` content frame of the
one Toplevel the app's two tabs now share. Everything geometric -- clamping to
the work area, scrolling, sizing, placement, the Escape binding -- lives there
now; what is left here is the widget tree and the rules for committing it.
`profiles_window.py` was split the same way at the same time.

**Two commit models, on purpose.** The theme buttons, the font-size buttons,
the colour picker and the per-colour "volver" commit immediately, because one
interaction is the whole edit. The hex text fields and the alert thresholds
commit on an explicit "Aplicar", because they are text: every commit rebuilds
this tree in the new theme (see `main_window.show_main_window`), so committing
on `<FocusOut>` would destroy the entry the user was tabbing *to*.

**The hex field survives the picker, and that is not an oversight.** The
picker was added because typing `#1a2b3c` is a poor way to choose a colour.
But `tkinter.colorchooser.askcolor()` on Windows is Tk's `tk_chooseColor`,
which `tkWinDialog.c` implements by calling the Win32 `ChooseColor()` common
dialog -- and that dialog has **no hex input at all**: a colour spectrum, a
luminosity slider, and Hue/Sat/Lum + Red/Green/Blue numeric boxes. So the
native dialog cannot replace the text field; deleting it would take away the
only way to type, paste, or read back an exact hex, and would break the
user-settings spec's own "a hex field exists for every field of the palette".
The two are complements: the picker is for choosing, the field is for saying
exactly.

**Showing a colour is not choosing it.** Every hex field is now pre-filled
with the colour that field actually renders as, rather than sitting empty when
it inherits. That makes "has text" useless as a signal of intent, so intent is
tracked separately and `colors.py` holds the rule -- see its docstring, and
`submitted()` in particular. Nothing here may persist a field the user did not
touch, or the sparse map that makes preset switching work would fill up with
nine overrides nobody chose.

**Contrast is reported, never enforced.** `tests/test_theme.py` gates the two
built-in presets at WCAG AA as a hard failure, and that stays: they are ours.
A palette the user chose is theirs, and the spec grants them the freedom to
pick an unreadable one on purpose. So this window measures and says so, and
then applies exactly what was asked for.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser
from typing import Callable, Mapping, Sequence

from ..config import ClaudeProfile
from ..quotas import QUOTA_WINDOWS, quota_label
from ..settings import (
    MAX_ALERT_INTERVAL_MINUTES,
    MAX_FONT_SIZE,
    MIN_ALERT_INTERVAL_MINUTES,
    MIN_FONT_SIZE,
    THEME_DARK,
    THEME_LIGHT,
    Settings,
    format_thresholds,
    parse_thresholds_text,
    preset_palette,
    with_alert_interval_minutes,
    with_alerts_enabled,
    with_colors,
    with_font_size,
    with_profile_thresholds,
    with_theme,
    with_thresholds,
    without_colors,
)
from ..theme import (
    AA_CONTRAST_RATIO,
    Palette,
    Theme,
    contrast_report,
    parse_hex_color,
    with_overrides,
)
from . import colors as color_model
from .widgets import button as _button

TAB_TITLE = "Configuracion"

OnSettingsChanged = Callable[[Settings], None]

# Spanish labels for the two presets, in display order.
_THEME_LABELS: tuple[tuple[str, str], ...] = ((THEME_DARK, "Oscuro"), (THEME_LIGHT, "Claro"))

# Spanish labels for the nine palette fields. `theme.py` holds the field
# names; user-facing copy lives here, the same split every other window keeps.
_COLOR_LABELS: dict[str, str] = {
    "bg": "Fondo",
    "panel": "Panel",
    "track": "Riel",
    "fg": "Texto",
    "muted": "Texto tenue",
    "accent": "Acento",
    "warn": "Aviso",
    "danger": "Alerta",
    "border": "Borde",
}

# Colours are laid out in two columns rather than nine stacked rows: this tab
# already carries four sections, and a single column would push it past a
# 768px-tall desktop at the default font. The rows grew a picker and a
# "volver" button since that was decided; the picker costs no width (the
# swatch *is* the button), but if two columns now measure too wide on a real
# desktop, `_COLOR_COLUMNS = 1` is the one-line fix -- the content scrolls now,
# so height is no longer the hard constraint it was when this was chosen.
_COLOR_COLUMNS = 2

# A `#rrggbb` is seven characters; the eighth is breathing room.
_ENTRY_WIDTH = 8
_THRESHOLD_ENTRY_WIDTH = 14

# The quota-window label column in the alerts rows. Sized for the longest of
# the three ("Fable en los ultimos 7 dias", 27 characters) so the three fields
# line up under each other within a profile's block.
_QUOTA_LABEL_WIDTH = 28

# The alert rows sit under their profile's name rather than beside it, so they
# are indented to read as belonging to it.
_ROW_INDENT = 12

# "Volver a este color". A glyph rather than a word because it repeats nine
# times in a two-column grid, and the same reason `profiles_window.py` uses
# ▲/▼ for reordering rather than "Subir"/"Bajar".
_REVERT_GLYPH = "↺"

# The −/+ step for the repeat-alert cooldown, in minutes. 15 keeps the stepper
# usable across the 0..1440 range without a text field (which would be
# destroyed on the theme rebuild, the same reason font size uses buttons).
_INTERVAL_STEP_MINUTES = 15


def _section_title(parent: tk.Widget, text: str, theme: Theme, *, top_pad: int) -> None:
    tk.Label(
        parent,
        text=text,
        bg=theme.palette.panel,
        fg=theme.palette.fg,
        font=theme.font("heading"),
        anchor="w",
    ).pack(fill="x", pady=(top_pad, 0))


def _hint(parent: tk.Widget, text: str, theme: Theme) -> None:
    tk.Label(
        parent,
        text=text,
        bg=theme.palette.panel,
        fg=theme.palette.muted,
        font=theme.font("small"),
        wraplength=420,
        justify="left",
        anchor="w",
    ).pack(fill="x", pady=(4, 0))


def _entry(parent: tk.Widget, theme: Theme, *, width: int, fg: str | None = None) -> tk.Entry:
    """The app's one text-field shape, matching the flat button language."""
    palette = theme.palette
    return tk.Entry(
        parent,
        width=width,
        bg=palette.bg,
        fg=palette.fg if fg is None else fg,
        insertbackground=palette.fg,
        disabledbackground=palette.bg,
        disabledforeground=palette.muted,
        font=theme.font("body"),
        bd=0,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
    )


def _build_theme_selector(
    parent: tk.Widget,
    settings: Settings,
    theme: Theme,
    *,
    on_select: Callable[[str], None],
) -> None:
    palette = theme.palette
    row = tk.Frame(parent, bg=palette.panel)
    row.pack(fill="x", pady=(8, 0))

    for name, label in _THEME_LABELS:
        selected = settings.theme == name
        _button(
            row,
            label,
            lambda n=name: on_select(n),
            theme=theme,
            bg=palette.accent if selected else palette.bg,
            fg=palette.fg if selected else palette.muted,
            font_role="body_bold" if selected else "body",
            padx=14,
            pady=6,
            highlightthickness=0 if selected else 1,
            highlightbackground=None if selected else palette.border,
        ).pack(side="left", padx=(0, 8))


def _build_font_size_control(
    parent: tk.Widget,
    settings: Settings,
    theme: Theme,
    *,
    on_change: Callable[[int], None],
) -> None:
    palette = theme.palette
    row = tk.Frame(parent, bg=palette.panel)
    row.pack(fill="x", pady=(8, 0))

    # The buttons clamp here as well as in the model: a disabled-looking
    # button that still fires is worse than one that cannot overshoot.
    _button(
        row,
        "−",
        lambda: on_change(settings.font_size - 1),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg if settings.font_size > MIN_FONT_SIZE else palette.muted,
        font_role="body_bold",
        padx=12,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left")

    tk.Label(
        row,
        text=str(settings.font_size),
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("body_bold"),
        width=3,
        anchor="center",
    ).pack(side="left", padx=(10, 10))

    _button(
        row,
        "+",
        lambda: on_change(settings.font_size + 1),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg if settings.font_size < MAX_FONT_SIZE else palette.muted,
        font_role="body_bold",
        padx=12,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left")

    # Renders in the theme's own body font, so the number above stops being
    # abstract: this line is what `base_size` actually does.
    tk.Label(
        row,
        text="Asi se ve el texto",
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("body"),
        anchor="w",
    ).pack(side="left", padx=(14, 0))


def _contrast_text(theme_palette: Palette) -> str:
    """The Spanish contrast notice for a candidate palette, or "" if it passes.

    Names the pairs that fall below AA in the user's own vocabulary, and says
    nothing at all when there is nothing to say -- a warning that is always
    on is a warning nobody reads.
    """
    failures = [check for check in contrast_report(theme_palette) if not check.passes]
    if not failures:
        return ""
    pairs = ", ".join(
        f"{_COLOR_LABELS.get(check.foreground, check.foreground)} sobre "
        f"{_COLOR_LABELS.get(check.background, check.background)} ({check.ratio:.1f}:1)"
        for check in failures
    )
    return (
        f"Poco contraste (menos de {AA_CONTRAST_RATIO:g}:1), puede costar leer: {pairs}. "
        "Se aplica igual si asi lo queres."
    )


def _build_colors_section(
    parent: tk.Widget,
    settings: Settings,
    theme: Theme,
    *,
    baseline: Mapping[str, str],
    on_apply: Callable[[Mapping[str, str]], None],
    on_reset: Callable[[], None],
) -> None:
    palette = theme.palette
    preset = preset_palette(settings)
    window = parent.winfo_toplevel()
    rows = color_model.color_rows(settings.colors, preset, baseline)

    grid = tk.Frame(parent, bg=palette.panel)
    grid.pack(fill="x", pady=(8, 0))

    variables: dict[str, tk.StringVar] = {}
    swatches: dict[str, tk.Button] = {}
    # Seeded from the stored map, never from the text: a pre-filled field is
    # showing you the theme's colour, not claiming you chose it. This set is
    # the only thing `apply` is allowed to persist.
    pinned: set[str] = {row.field for row in rows if row.pinned}

    feedback = tk.Label(
        parent,
        text="",
        bg=palette.panel,
        fg=palette.warn,
        font=theme.font("small"),
        wraplength=420,
        justify="left",
        anchor="w",
    )

    def candidate_palette() -> Palette:
        """The palette the current field text *would* produce, uncommitted."""
        overrides = {}
        for name, variable in variables.items():
            parsed = parse_hex_color(variable.get())
            if parsed is not None:
                overrides[name] = parsed
        return with_overrides(preset, overrides)

    def refresh(_event: tk.Event | None = None) -> None:
        """Live feedback for the text path: swatches and the contrast notice.

        The picker does not need this -- it commits, and the whole window is
        rebuilt in the new palette, which is a better preview than any swatch.
        This is what keeps an explicit "Aplicar" affordable for the fields
        that are still text.
        """
        for name, variable in variables.items():
            parsed = parse_hex_color(variable.get())
            swatch = swatches[name]
            if parsed is None:
                swatch.configure(bg=palette.panel, text="?", fg=palette.danger)
            else:
                swatch.configure(bg=parsed, activebackground=parsed, text=" ", fg=parsed)
        feedback.configure(text=_contrast_text(candidate_palette()), fg=palette.warn)

    def handle_type(name: str) -> None:
        # Typing into a field is the user choosing that colour, so it pins it.
        # Before the pre-fill, a non-empty field carried this meaning by
        # itself; now that every field is filled, the keystroke is the signal.
        pinned.add(name)
        refresh()

    def apply() -> None:
        overrides, invalid = color_model.submitted(
            {name: variable.get() for name, variable in variables.items()},
            pinned,
        )
        if invalid:
            # Apply nothing while anything is wrong: a partial apply would
            # leave the window rebuilt, the bad field reverted, and no way to
            # tell which of the nine the user had actually mistyped.
            names = ", ".join(_COLOR_LABELS.get(name, name) for name in invalid)
            feedback.configure(
                text=f"Revisa estos colores, tienen que ser hex (#1a2b3c): {names}.",
                fg=palette.danger,
            )
            return
        on_apply(overrides)

    def handle_pick(name: str) -> None:
        """Open the native picker for one field and commit what comes back.

        `askcolor` is **modal**, so there is no previewing *during* the drag:
        nothing in this process runs until the dialog returns. The preview is
        therefore on return, and it is the real thing rather than a swatch --
        committing rebuilds every window in the new palette, which is exactly
        what "ver como va quedando" asks for.

        Committing from the stored map rather than from the fields is
        deliberate: a half-typed hex two rows down must not decide whether
        this pick lands.
        """
        initial = color_model.effective(settings.colors, preset, name)
        label = _COLOR_LABELS.get(name, name)
        # This window is `-topmost`. A native modal dialog owned by it can be
        # drawn *underneath* it, which does not read as a dialog -- it reads
        # as an app that froze. Dropped for the dialog, restored the moment it
        # returns and before anything can destroy the window.
        window.attributes("-topmost", False)
        try:
            rgb, text = colorchooser.askcolor(color=initial, parent=window, title=f"Color: {label}")
        finally:
            if window.winfo_exists():
                window.attributes("-topmost", True)
        picked = color_model.normalize_choice(rgb, text)
        if picked is None:
            return  # cancelled, or a reply we could not read -- same ending
        on_apply(color_model.with_pick(settings.colors, name, picked))

    def handle_revert(name: str) -> None:
        # A no-op when the field never moved: the reverted map equals the
        # stored one, and the caller's own equality guard drops it without a
        # rebuild. So the button needs no disabled state to be safe to press.
        on_apply(color_model.with_revert(settings.colors, baseline, name))

    for index, row in enumerate(rows):
        grid_row, column = divmod(index, _COLOR_COLUMNS)
        cell = tk.Frame(grid, bg=palette.panel)
        cell.grid(row=grid_row, column=column, sticky="w", padx=(0, 16), pady=2)

        tk.Label(
            cell,
            text=_COLOR_LABELS.get(row.field, row.field),
            bg=palette.panel,
            fg=palette.muted,
            font=theme.font("small"),
            width=11,
            anchor="w",
        ).pack(side="left")

        # The swatch *is* the picker. It already existed, it already shows the
        # colour, and clicking the colour you want to change is the whole
        # affordance -- so the picker costs this crowded grid no width at all.
        swatch = _button(
            cell,
            " ",
            lambda name=row.field: handle_pick(name),
            theme=theme,
            bg=row.value,
            fg=row.value,
            font_role="small",
            padx=6,
            pady=2,
            highlightthickness=1,
            highlightbackground=palette.border,
        )
        swatch.pack(side="left", padx=(0, 6))
        swatches[row.field] = swatch

        variable = tk.StringVar(value=row.value)
        variables[row.field] = variable

        # Muted while the colour is the theme's, solid once it is the user's:
        # the placeholder idiom, carrying the same fact the word below it
        # carries, for a reader who never reads the word.
        field = _entry(
            cell,
            theme,
            width=_ENTRY_WIDTH,
            fg=palette.fg if row.pinned else palette.muted,
        )
        field.configure(textvariable=variable)
        field.pack(side="left")
        field.bind("<KeyRelease>", lambda _event, name=row.field: handle_type(name))
        field.bind("<Return>", lambda _event: apply())

        _button(
            cell,
            _REVERT_GLYPH,
            lambda name=row.field: handle_revert(name),
            theme=theme,
            bg=palette.panel,
            # Lit only when there is something to go back to. The button is
            # always present so the grid does not reflow as colours change.
            fg=palette.fg if row.revertable else palette.border,
            font_role="small",
            padx=4,
        ).pack(side="left", padx=(4, 0))

        tk.Label(
            cell,
            # Said in words, not only implied by the muted text above: the
            # user has to be able to tell "this is the theme's colour" from "I
            # chose this", and the alerts section below already says exactly
            # this about an inherited threshold with exactly this word.
            text="propio" if row.pinned else "del tema",
            bg=palette.panel,
            fg=palette.muted,
            font=theme.font("small"),
            width=8,
            anchor="w",
        ).pack(side="left", padx=(6, 0))

    feedback.pack(fill="x", pady=(8, 0))

    actions = tk.Frame(parent, bg=palette.panel)
    actions.pack(fill="x", pady=(6, 0))

    _button(
        actions,
        "Aplicar colores",
        apply,
        theme=theme,
        bg=palette.accent,
        fg=palette.fg,
        font_role="body_bold",
        padx=12,
        pady=5,
    ).pack(side="left")

    _button(
        actions,
        "Volver al tema",
        on_reset,
        theme=theme,
        bg=palette.bg,
        fg=palette.muted,
        padx=12,
        pady=5,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left", padx=(8, 0))

    refresh()


def _build_threshold_rows(
    parent: tk.Widget,
    theme: Theme,
    *,
    variables: dict[str, tk.StringVar],
    hint_of: Callable[[str], str],
    on_submit: Callable[[], None],
) -> None:
    """One labelled tier field per quota window, stacked.

    **Stacked, not three across, and the inheritance hint is why.** Three
    fields on one row leaves nowhere to say "vacio = general (80, 95)" three
    times -- that hint is per field, because inheritance is now per (profile,
    window), and it is the app's existing word for inheritance (the colour
    rows say "del tema" the same way). A column-header grid could carry three
    fields but not three hints, so it would have to drop the one thing the
    spec requires the control to make clear.

    It is also the cheaper axis. Height is clamped and scrolls, and that is
    verified on real hardware; **width is clamped nowhere** -- the window
    measured 878px at font 16 with 20 profiles, and three fields plus three
    hints abreast would have multiplied the widest thing in the window. So
    this spends the resource we have and keeps the one we do not.
    """
    palette = theme.palette
    for window in QUOTA_WINDOWS:
        row = tk.Frame(parent, bg=palette.panel)
        row.pack(fill="x", pady=(4, 0))

        tk.Label(
            row,
            # The same Spanish name the toast and the popup use, out of the
            # same dict -- `quotas.py` exists so this cannot drift.
            text=quota_label(window),
            bg=palette.panel,
            fg=palette.muted,
            font=theme.font("small"),
            width=_QUOTA_LABEL_WIDTH,
            anchor="w",
        ).pack(side="left", padx=(_ROW_INDENT, 0))

        field = _entry(row, theme, width=_THRESHOLD_ENTRY_WIDTH)
        field.configure(textvariable=variables[window])
        field.pack(side="left")
        field.bind("<Return>", lambda _event: on_submit())

        hint = hint_of(window)
        if hint:
            tk.Label(
                row,
                text=hint,
                bg=palette.panel,
                fg=palette.muted,
                font=theme.font("small"),
                anchor="w",
            ).pack(side="left", padx=(10, 0))


def _format_interval(minutes: int) -> str:
    """The Spanish label for a cooldown value; `0` reads as "no limit"."""
    if minutes <= 0:
        return "Sin limite"
    return f"{minutes} min"


def _build_interval_control(
    parent: tk.Widget,
    settings: Settings,
    theme: Theme,
    *,
    on_change: Callable[[int], None],
) -> None:
    """A −/+ stepper for the repeat-alert cooldown, minutes.

    Immediate-commit like the font-size control (and for the same reason: a
    text field would be destroyed by the theme rebuild each commit triggers).
    """
    palette = theme.palette
    current = settings.alerts.min_interval_minutes

    row = tk.Frame(parent, bg=palette.panel)
    row.pack(fill="x", pady=(8, 0))

    _button(
        row,
        "−",
        lambda: on_change(current - _INTERVAL_STEP_MINUTES),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg if current > MIN_ALERT_INTERVAL_MINUTES else palette.muted,
        font_role="body_bold",
        padx=12,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left")

    tk.Label(
        row,
        text=_format_interval(current),
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("body_bold"),
        width=10,
        anchor="center",
    ).pack(side="left", padx=(10, 10))

    _button(
        row,
        "+",
        lambda: on_change(current + _INTERVAL_STEP_MINUTES),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg if current < MAX_ALERT_INTERVAL_MINUTES else palette.muted,
        font_role="body_bold",
        padx=12,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left")


def _build_alerts_section(
    parent: tk.Widget,
    settings: Settings,
    profiles: Sequence[ClaudeProfile],
    theme: Theme,
    *,
    on_toggle: Callable[[bool], None],
    on_interval_change: Callable[[int], None],
    on_apply: Callable[[dict[str, tuple[int, ...]], dict[str, dict[str, tuple[int, ...] | None]]], None],
) -> None:
    palette = theme.palette
    alerts = settings.alerts

    toggle_row = tk.Frame(parent, bg=palette.panel)
    toggle_row.pack(fill="x", pady=(8, 0))
    for enabled, label in ((True, "Activadas"), (False, "Desactivadas")):
        selected = alerts.enabled is enabled
        _button(
            toggle_row,
            label,
            lambda value=enabled: on_toggle(value),
            theme=theme,
            bg=palette.accent if selected else palette.bg,
            fg=palette.fg if selected else palette.muted,
            font_role="body_bold" if selected else "body",
            padx=14,
            pady=6,
            highlightthickness=0 if selected else 1,
            highlightbackground=None if selected else palette.border,
        ).pack(side="left", padx=(0, 8))

    _hint(
        parent,
        "Tiempo minimo entre un aviso y el siguiente del mismo perfil y la misma "
        "cuota. En 0 no hay limite. Como una cuota se renueva cada varias horas, "
        "subilo por encima de eso si te llegan avisos repetidos al renovarse.",
        theme,
    )
    _build_interval_control(
        parent,
        settings,
        theme,
        on_change=on_interval_change,
    )

    feedback = tk.Label(
        parent,
        text="",
        bg=palette.panel,
        fg=palette.danger,
        font=theme.font("small"),
        wraplength=420,
        justify="left",
        anchor="w",
    )

    # Keyed by quota window for the general rows, and by (profile_id, window)
    # for the per-profile ones. Three fields where there was one, because
    # inheritance is now resolved per (profile, window) -- which is what the
    # state machine has always keyed on.
    global_vars: dict[str, tk.StringVar] = {
        window: tk.StringVar(value=format_thresholds(alerts.thresholds.get(window, ())))
        for window in QUOTA_WINDOWS
    }
    profile_vars: dict[tuple[str, str], tk.StringVar] = {}

    def apply() -> None:
        general: dict[str, tuple[int, ...]] = {}
        invalid: list[str] = []
        for window in QUOTA_WINDOWS:
            parsed = parse_thresholds_text(global_vars[window].get())
            if parsed is None:
                invalid.append(f"General / {quota_label(window)}")
                continue
            general[window] = parsed

        per_profile: dict[str, dict[str, tuple[int, ...] | None]] = {}
        for profile in profiles:
            windows: dict[str, tuple[int, ...] | None] = {}
            for window in QUOTA_WINDOWS:
                raw = profile_vars[(profile.id, window)].get().strip()
                if not raw:
                    windows[window] = None  # blank == inherit this window
                    continue
                parsed = parse_thresholds_text(raw)
                if parsed is None:
                    # Named by profile AND window: with 63 fields on screen,
                    # "revisa los umbrales de PC" is not enough to find one.
                    invalid.append(f"{profile.name} / {quota_label(window)}")
                    continue
                windows[window] = parsed
            per_profile[profile.id] = windows

        if invalid:
            # Apply nothing while anything is wrong, exactly as the colour
            # section does: a partial apply would leave the window rebuilt,
            # the bad field reverted, and no way to tell which of the 63 the
            # user had actually mistyped.
            feedback.configure(
                text=(
                    f"Revisa estos umbrales: {', '.join(invalid)}. "
                    "Numeros entre 1 y 100, separados por comas."
                )
            )
            return
        on_apply(general, per_profile)

    general_block = tk.Frame(parent, bg=palette.panel)
    general_block.pack(fill="x", pady=(10, 0))

    tk.Label(
        general_block,
        text="General",
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("body_bold"),
        anchor="w",
    ).pack(fill="x")

    _build_threshold_rows(
        general_block,
        theme,
        variables=global_vars,
        hint_of=lambda _window: "",
        on_submit=apply,
    )

    tk.Label(
        general_block,
        text="se usa en los perfiles sin umbral propio",
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("small"),
        anchor="w",
    ).pack(fill="x", pady=(4, 0))

    if not profiles:
        _hint(parent, "Todavia no hay perfiles configurados.", theme)
    for profile in profiles:
        block = tk.Frame(parent, bg=palette.panel)
        block.pack(fill="x", pady=(10, 0))

        tk.Label(
            block,
            text=profile.name,
            bg=palette.panel,
            fg=palette.fg,
            font=theme.font("body"),
            anchor="w",
        ).pack(fill="x")

        for window in QUOTA_WINDOWS:
            profile_vars[(profile.id, window)] = tk.StringVar(
                value=(
                    format_thresholds(alerts.profiles[profile.id][window])
                    if alerts.has_override(profile.id, window)
                    else ""
                )
            )

        def hint_of(window: str, profile_id: str = profile.id) -> str:
            # The inherited value is shown rather than merely implied: an
            # empty field is otherwise indistinguishable from a broken one.
            # Per window now, because a profile can inherit one window and
            # override another -- "propio" on the row that is its own.
            if alerts.has_override(profile_id, window):
                return "propio"
            return f"vacio = general ({format_thresholds(alerts.thresholds.get(window, ()))})"

        _build_threshold_rows(
            block,
            theme,
            variables={
                window: profile_vars[(profile.id, window)] for window in QUOTA_WINDOWS
            },
            hint_of=hint_of,
            on_submit=apply,
        )

    feedback.pack(fill="x", pady=(8, 0))

    _button(
        parent,
        "Aplicar umbrales",
        apply,
        theme=theme,
        bg=palette.accent,
        fg=palette.fg,
        font_role="body_bold",
        padx=12,
        pady=5,
    ).pack(anchor="w", pady=(6, 0))


def build_settings_content(
    parent: tk.Widget,
    settings: Settings,
    on_settings_changed: OnSettingsChanged,
    *,
    profiles: Sequence[ClaudeProfile],
    theme: Theme,
    baseline: Mapping[str, str],
) -> None:
    """Build the Configuracion tab's widgets into `parent`.

    `on_settings_changed(new_settings)` is called once, synchronously, on
    every committed change. The caller persists it and rebuilds this tree with
    the new `Theme`, so the settings being edited are the ones they are drawn
    in.

    That rebuild is the caller's to own and not this module's, which is why
    this takes a callback rather than re-rendering itself: the reply to a
    theme change is a *different Theme*, and only the caller can construct
    one. A self-rerender would redraw the new settings in the old theme.

    `baseline` is `settings.colors` as it stood when the window was opened --
    what every per-colour "volver" goes back to. It is threaded in from the
    caller rather than captured here for exactly the same reason: this tree is
    rebuilt on every pick, so anything captured here would reset on the first
    one and revert would have nothing to return to.

    `profiles` is read-only here -- this tab never edits the profile list, it
    only needs the ids and names to offer a per-profile threshold row.
    """
    palette = theme.palette

    def apply_change(new_settings: Settings) -> None:
        if new_settings == settings:
            return
        on_settings_changed(new_settings)

    tk.Label(
        parent,
        text="Se guarda al instante y se aplica cada vez que abris una ventana.",
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("body"),
        anchor="w",
    ).pack(fill="x")

    _section_title(parent, "Tema", theme, top_pad=14)
    _hint(parent, "Elegi el punto de partida. Los colores de abajo se aplican encima.", theme)
    _build_theme_selector(
        parent,
        settings,
        theme,
        on_select=lambda name: apply_change(with_theme(settings, name)),
    )

    _section_title(parent, "Tamano de letra", theme, top_pad=14)
    _hint(parent, f"Entre {MIN_FONT_SIZE} y {MAX_FONT_SIZE}. Afecta a todas las ventanas.", theme)
    _build_font_size_control(
        parent,
        settings,
        theme,
        on_change=lambda size: apply_change(with_font_size(settings, size)),
    )

    _section_title(parent, "Colores", theme, top_pad=14)
    _hint(
        parent,
        "Toca el cuadrito para elegir un color, o escribi el hex (#1a2b3c o #abc). "
        "Los que dicen \"del tema\" son los del tema elegido y cambian con el; "
        f"cuando tocas uno pasa a ser \"propio\". {_REVERT_GLYPH} vuelve al color anterior.",
        theme,
    )
    _build_colors_section(
        parent,
        settings,
        theme,
        baseline=baseline,
        on_apply=lambda overrides: apply_change(with_colors(settings, overrides)),
        on_reset=lambda: apply_change(without_colors(settings)),
    )

    _section_title(parent, "Alertas", theme, top_pad=14)
    _hint(
        parent,
        "Avisos cuando el uso cruza un umbral. Cada cuota tiene los suyos, y podes "
        "ponerlos distintos en cada perfil. Podes poner varios separados por comas "
        "(80, 95). Dejalo vacio para usar el general de esa cuota.",
        theme,
    )

    def apply_thresholds(
        general: dict[str, tuple[int, ...]],
        per_profile: dict[str, dict[str, tuple[int, ...] | None]],
    ) -> None:
        updated = settings
        for window, tiers in general.items():
            updated = with_thresholds(updated, window, tiers)
        for profile_id, windows in per_profile.items():
            for window, tiers in windows.items():
                updated = with_profile_thresholds(updated, profile_id, window, tiers)
        apply_change(updated)

    _build_alerts_section(
        parent,
        settings,
        profiles,
        theme,
        on_toggle=lambda enabled: apply_change(with_alerts_enabled(settings, enabled)),
        on_interval_change=lambda minutes: apply_change(with_alert_interval_minutes(settings, minutes)),
        on_apply=apply_thresholds,
    )


__all__ = ["TAB_TITLE", "OnSettingsChanged", "build_settings_content"]
