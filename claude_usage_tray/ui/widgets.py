"""claude_usage_tray.ui.widgets -- shared widget factories.

Shared by `popup.py` and `profiles_window.py`: the progress-row builder used
by the usage popup, and the window-icon application helper both windows
need. Neither holds a palette/font literal of its own -- `theme.py` (L1) is
the single source for those; this module only ever reads them off the
`Theme` it is given.

Move-only extraction of `app.py`'s `_create_progress_row`/
`_apply_window_icon`, now parameterized by `Theme` instead of the
module-level color/font literals they used to close over. `apply_window_icon`
receiving a pre-built icon image (rather than building one itself) is what
keeps this module free of a PIL import -- `test_import_hygiene.py` allowlists
PIL only for `icons.py`, and `ui/*` is only exempted for `tkinter`.

`button` lives here rather than in one window because all three windows need
the same flat, borderless button; it was `profiles_window._button` until the
settings window (slice c) became its second caller.

`ScrollableView` joins them for the same reason -- the settings window
overflowed a 1050px work area at font 16, and the profiles window overflows
at any font size given enough profiles. All of its arithmetic lives in
`scroll.py`, which imports no tkinter and is therefore testable on a box with
no display; what is left here is the widget tree that arithmetic drives.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from .. import formatting
from ..theme import DEFAULT_THEME, FontRole, Theme
from . import scroll


def create_progress_row(
    parent: tk.Widget,
    label: str,
    entry: dict[str, Any] | None,
    *,
    theme: Theme = DEFAULT_THEME,
    top_pad: int = 8,
    emphasis: bool = False,
) -> tk.Frame:
    """Build one labeled progress bar row (label + reset time + bar + %).

    Move-only extraction of `app.py`'s `_create_progress_row`. The one
    conditional font site (`emphasis`) becomes `theme.font("body_bold" if
    emphasis else "body")`, per design.md's "Theme returns font tuples"
    decision -- this is the exact site design.md calls out by name.
    """
    palette = theme.palette
    pct = formatting.pct(entry)
    value = f"{pct:.0f}% usado" if pct is not None else "—"

    row = tk.Frame(parent, bg=palette.panel)
    row.pack(fill="x", pady=(top_pad, 0))

    header = tk.Frame(row, bg=palette.panel)
    header.pack(fill="x")

    tk.Label(
        header,
        text=label,
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("body_bold" if emphasis else "body"),
        anchor="w",
    ).pack(side="left")

    tk.Label(
        header,
        text=formatting.reset_label(entry.get("resets_at") if entry else None),
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("small"),
        anchor="e",
    ).pack(side="right")

    bar_row = tk.Frame(row, bg=palette.panel)
    bar_row.pack(fill="x", pady=(2, 0))

    canvas = tk.Canvas(
        bar_row,
        width=252,
        height=10,
        bg=palette.panel,
        highlightthickness=0,
        bd=0,
    )
    canvas.pack(side="left", fill="x", expand=True)

    width = 252
    bar_height = 6
    y = 2
    canvas.create_rectangle(0, y, width, y + bar_height, fill=palette.track, outline=palette.track)
    if pct is not None:
        fill_width = max(0, min(width, int(width * pct / 100)))
        # The palette is passed rather than left to `bar_color`'s DARK
        # constants: without it the fills are identical in every theme, so a
        # custom `accent` would visibly fail to reach the app's most
        # prominent element and the light preset would stay half-themed.
        color = formatting.bar_color(pct, palette)
        canvas.create_rectangle(0, y, fill_width, y + bar_height, fill=color, outline=color)

    tk.Label(
        bar_row,
        text=value,
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("small_bold"),
        anchor="e",
    ).pack(side="right", padx=(8, 0))

    return row


def button(
    parent: tk.Widget,
    text: str,
    command: Callable[[], None] | None,
    *,
    theme: Theme,
    bg: str,
    fg: str,
    font_role: FontRole = "body",
    padx: int = 4,
    pady: int = 0,
    highlightthickness: int = 0,
    highlightbackground: str | None = None,
) -> tk.Button:
    """The app's one button shape: flat, borderless, hand cursor.

    `theme` is required rather than defaulted: every call site already has
    one, and a default here would be the third place to get `Palette` vs
    `Theme` wrong.

    `font_role` is the `FontRole` literal, not `str`: a mistyped role is a
    `KeyError` raised inside `Theme.font`, at render time, in a window no
    headless test can build. Naming the type is what moves that failure to
    where it can be seen.
    """
    kwargs: dict[str, Any] = dict(
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=bg,
        activeforeground=theme.palette.fg,
        bd=0,
        padx=padx,
        pady=pady,
        font=theme.font(font_role),
        highlightthickness=highlightthickness,
        relief="flat",
        cursor="hand2",
    )
    if highlightbackground is not None:
        kwargs["highlightbackground"] = highlightbackground
    return tk.Button(parent, **kwargs)


def apply_window_icon(window: tk.Misc, window_icon: Any | None) -> None:
    """Set the window's title-bar/taskbar icon from a palette-generated image.

    `window_icon` is a pre-built `ImageTk.PhotoImage` (or `None`) supplied by
    the caller -- rendered from the *current* palette (`icons.window_icon`), so
    a user's custom colors reach the title bar. This module never builds one
    itself, so it never imports PIL.

    **No `iconbitmap` here, deliberately.** It used to also call
    `window.iconbitmap(default=<bundled .ico>)` on Windows -- but that `.ico`
    has fixed colors, and on Windows `iconbitmap` wins over `iconphoto` for the
    title-bar icon, so the palette-generated image was silently overridden and
    the icon never reflected a custom palette. Tk 8.6 (this app's floor) honors
    `iconphoto` for both the title bar and the taskbar on Windows, so dropping
    the static `iconbitmap` is what lets the live icon follow the palette. The
    bundled `.ico` in `build.py` still gives the frozen EXE its own file icon;
    that is a separate concern and stays.
    """
    if window_icon is not None:
        try:
            window.iconphoto(True, window_icon)
        except tk.TclError:
            pass


class ScrollableView:
    """A viewport that scrolls its content, but only once it has to.

    Pack the caller's widgets into `.content` exactly as they were packed
    into the plain `tk.Frame` this replaces; the view sizes and scrolls
    itself from there. `natural_size()` reports what the content wants, which
    is what the caller clamps against the work area -- a `create_window`
    canvas item propagates no geometry, so the enclosing Toplevel has no
    natural size of its own to ask for.

    **Engagement is measured, never configured.** Every `<Configure>` asks
    one question -- is the content taller than the viewport -- and packs or
    forgets the scrollbar on the answer. So a short window carries no
    vestigial bar, a long one scrolls, and the resizable profiles window
    crosses between the two as the user drags its edge, with no caller
    obliged to notice. The check is height-only and every wrapping label in
    this app has a fixed `wraplength`, so the answer cannot depend on the
    width that packing the bar changes: no feedback loop, and the idempotence
    guard in `_set_scrolling` closes it regardless.

    **The wheel is bound to the Toplevel, once, and never unbound.** The
    Toplevel sits in the bindtags of every widget inside it and of nothing
    outside it, so that single binding catches the wheel anywhere in this
    window and nowhere else -- no `bind_all`. It is never removed because
    `Misc.unbind(sequence, funcid)` before Python 3.12 clears *every*
    callback for that sequence on the widget, which is precisely the
    scope-unsafety `dismiss.py` exists to document; the handler consults
    `_scrolls` instead, so the binding that is never removed cannot be
    removed wrongly. The binding dies with the Toplevel that owns it, which is
    the invariant that makes never unbinding safe -- and therefore the one
    callers must not break: **at most one view per Toplevel, for that
    Toplevel's whole life.** A second view built on a live Toplevel stacks a
    second wheel handler on it and scrolls twice as fast, once more per
    rebuild, with no way to take either back. `main_window.py` builds its view
    once and calls `content_rebuilt()` when it swaps tabs, rather than
    building a fresh view, for exactly this reason.

    **The scrollbar is drawn, not instantiated.** Tk's own `Scrollbar` is
    painted by the native theme engine on Windows, which ignores the colour
    options -- a stock grey bar in a window whose every other pixel the user
    is entitled to re-colour. Drawing it on a canvas is also the app's
    existing visual language rather than a new one: `create_progress_row`
    already draws its groove as a `palette.track` rectangle on a canvas, and
    the trough here is the same field for the same reason.
    """

    def __init__(self, parent: tk.Widget, theme: Theme) -> None:
        self._palette = theme.palette
        self._scrolls = False
        self._first = 0.0
        self._last = 1.0
        self._grab_offset = 0

        self.frame = tk.Frame(parent, bg=self._palette.panel)
        self.frame.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            self.frame,
            bg=self._palette.panel,
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        self._bar = tk.Canvas(
            self.frame,
            width=scroll.SCROLLBAR_WIDTH,
            bg=self._palette.track,
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )

        self.content = tk.Frame(self._canvas, bg=self._palette.panel)
        self._item = self._canvas.create_window((0, 0), window=self.content, anchor="nw")

        self._canvas.configure(yscrollcommand=self._on_view_changed)
        self._canvas.bind("<Configure>", self._on_viewport_configure, add="+")
        self._bar.bind("<Configure>", lambda _event: self._draw_thumb(), add="+")
        self._bar.bind("<Button-1>", self._on_bar_press, add="+")
        self._bar.bind("<B1-Motion>", self._on_bar_drag, add="+")

        toplevel = self._canvas.winfo_toplevel()
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            toplevel.bind(sequence, self._on_wheel, add="+")

    def natural_size(self) -> tuple[int, int]:
        """The `(width, height)` the content asks for, in pixels."""
        self._canvas.update_idletasks()
        return self.content.winfo_reqwidth(), self.content.winfo_reqheight()

    def content_rebuilt(self) -> None:
        """Re-measure and return to the top after `content`'s children changed.

        `<Configure>` fires when the *viewport* changes, which is the only
        thing that used to change: both windows destroyed and rebuilt their
        whole Toplevel, taking the view with it. `main_window.py` swaps the
        content of a view that stays put when the user switches tab, and
        nothing about the viewport changes to announce that -- so the
        scrollregion would still describe the tree that is gone, leaving
        content unreachable or a scrollbar for content that is no longer
        there. This is that announcement.

        The reset to the top is part of the same fact: the old offset was a
        position in the old content, and carrying it into new content lands
        wherever that many pixels happens to be.
        """
        self._canvas.yview_moveto(0.0)
        self._canvas.update_idletasks()
        self._measure(self._canvas.winfo_width(), self._canvas.winfo_height())

    def _on_viewport_configure(self, event: tk.Event) -> None:
        self._measure(event.width, event.height)

    def _measure(self, viewport_width: int, viewport_height: int) -> None:
        content_height = self.content.winfo_reqheight()
        # `fill="both"` on a packed frame is what the content used to get from
        # its parent; the canvas item has to be told the same width by hand.
        self._canvas.itemconfigure(self._item, width=viewport_width)
        self._canvas.configure(scrollregion=(0, 0, viewport_width, content_height))
        self._set_scrolling(content_height > viewport_height)
        self._draw_thumb()

    def _set_scrolling(self, enabled: bool) -> None:
        if enabled == self._scrolls:
            return
        self._scrolls = enabled
        if enabled:
            self._bar.pack(side="right", fill="y", padx=(scroll.SCROLLBAR_GAP, 0))
        else:
            self._bar.pack_forget()
            # A view that stops scrolling while scrolled would otherwise keep
            # its offset and show a permanently blank strip where the content
            # it no longer needs to hide used to be.
            self._canvas.yview_moveto(0.0)

    def _on_view_changed(self, first: Any, last: Any) -> None:
        """Tk's `yscrollcommand`, reporting the visible fraction of content.

        `Any`, and converted rather than trusted, because the two authorities
        disagree: typeshed types this callback `(float, float)`, while Tcl
        hands every callback argument across as a string, so at runtime these
        arrive as `"0.0"`/`"0.5"`. `float()` is correct for either, which is
        the only reason this seam is safe -- and it is exactly the sort of
        boundary no test on this box can reach.
        """
        self._first, self._last = float(first), float(last)
        self._draw_thumb()

    def _draw_thumb(self) -> None:
        self._bar.delete("thumb")
        if not self._scrolls:
            return
        top, bottom = scroll.thumb_span(self._first, self._last, self._bar.winfo_height())
        if bottom <= top:
            return
        self._bar.create_rectangle(
            0,
            top,
            scroll.SCROLLBAR_WIDTH,
            bottom,
            fill=self._palette.muted,
            outline=self._palette.muted,
            tags="thumb",
        )

    def _on_wheel(self, event: tk.Event) -> None:
        if not self._scrolls:
            return
        units = scroll.wheel_units(getattr(event, "delta", 0), getattr(event, "num", 0))
        if units:
            self._canvas.yview_scroll(units, "units")

    def _on_bar_press(self, event: tk.Event) -> None:
        if not self._scrolls:
            return
        top, bottom = scroll.thumb_span(self._first, self._last, self._bar.winfo_height())
        if top <= event.y <= bottom:
            self._grab_offset = event.y - top  # grab the thumb where it was held
        else:
            self._grab_offset = (bottom - top) // 2  # a trough click centres it
            self._on_bar_drag(event)

    def _on_bar_drag(self, event: tk.Event) -> None:
        if not self._scrolls:
            return
        self._canvas.yview_moveto(
            scroll.drag_fraction(
                event.y,
                self._grab_offset,
                self._bar.winfo_height(),
                max(self._last - self._first, 0.0),
            )
        )
