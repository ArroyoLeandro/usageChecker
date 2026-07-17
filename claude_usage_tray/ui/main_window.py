"""claude_usage_tray.ui.main_window -- the one managed window, and its tabs.

"Perfiles" and "Configuracion" were two Toplevels with two of everything:
two lifecycles in `app.py`, two sets of geometry arithmetic, two "Cerrar"
buttons, and two answers to what the window's size means. They are one window
with two tabs now. The tray menu still lists both entries, because that is how
the user reaches the thing they have in mind; each opens this window on its
own tab.

**The tab strip is hand-built, not a `ttk.Notebook`.** This is the same
decision, for the same reason, that `widgets.ScrollableView` already recorded
about the scrollbar: `ttk` widgets on Windows are painted by the native theme
engine, which ignores the colour options -- a stock grey-and-blue tab strip in
a window whose every other pixel the user is entitled to re-colour, and whose
whole settings tab exists to let them. This app is `tk` end to end and holds
no `ttk` import; a Notebook would be the first, and would need `ttk.Style`
plumbing to approximate what two `widgets.button` calls already do exactly.
And they *do* do it exactly: a selected tab is `accent`/`fg`/`body_bold` and
an unselected one is `bg`/`muted` with a `border` hairline -- byte for byte
the idiom `settings_window`'s theme selector and alert toggle already use, so
the strip introduces no new visual language at all.

**Scrolling is per tab, and the strip is never inside it.** Both windows were
already clamped to the work area and scrolled (settings wants 1061px at font
16 on a 1050px desktop; either tab overflows at ~20 profiles), and that must
not regress. So the strip and the "Cerrar" footer sit outside the viewport and
stay put, and only the tab's own content scrolls -- a tab strip you have to
scroll back up to reach is not a tab strip.

**One `ScrollableView`, built once per Toplevel, content rebuilt in place.**
Deliberate, and the wheel is why. `ScrollableView` binds `<MouseWheel>` to the
Toplevel and never unbinds -- it cannot: before Python 3.12 (this project runs
3.10) `Misc.unbind(seq, funcid)` clears *every* callback for that sequence,
which is the scope-unsafety `dismiss.py` exists to document. That binding is
safe only because it dies with the Toplevel that owns it. So a tab switch must
not build a second view on the same Toplevel -- that would stack a second
wheel handler and scroll twice as fast, once more per switch. It rebuilds the
one view's *content* instead, and `view.content_rebuilt()` re-measures.

**Sizing happens once per show, not per tab switch.** The window opens at the
persisted `window_size`, or auto-fits the tab it was asked for. Switching tabs
after that leaves the geometry alone: re-fitting would either snap away a size
the user chose by dragging, or make the window jump every time they looked at
the other tab. Tabs do not resize their window; the viewport scrolls, which is
what it is for.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Mapping

from .. import platform
from ..config import ClaudeProfile
from ..settings import Settings, format_window_size, parse_window_size
from ..theme import DEFAULT_THEME, Theme
from . import profiles_window, scroll, settings_window
from .profiles_window import OnProfilesChanged, OnWindowSizeChanged
from .settings_window import OnSettingsChanged
from .widgets import ScrollableView, apply_window_icon
from .widgets import button as _button

WINDOW_TITLE = "ClaudeUsage — Perfiles y configuracion"

TAB_PROFILES = "profiles"
TAB_SETTINGS = "settings"
TABS: tuple[str, ...] = (TAB_PROFILES, TAB_SETTINGS)

# The tab strip's Spanish labels, in display order. They match the tray menu's
# own entries exactly: the menu is how the user asks for one of these, so the
# thing that opens had better be called what they clicked.
_TAB_LABELS: tuple[tuple[str, str], ...] = (
    (TAB_PROFILES, profiles_window.TAB_TITLE),
    (TAB_SETTINGS, settings_window.TAB_TITLE),
)

OnTabChanged = Callable[[str], None]

# The inset between the window edge and the content, on all four sides. Named
# because the window's size is computed from the content's own request rather
# than read back off the window, so this has to be added by hand.
_PADDING = 14

# Breathing room under the tab strip and above the "Cerrar" footer. Named for
# the same reason: they are `pady` on a `pack` call, which no `winfo_reqheight`
# reports back, so the height arithmetic has to add them itself.
_STRIP_GAP = 12
_FOOTER_GAP = 10


def _build_tab_strip(
    parent: tk.Widget,
    theme: Theme,
    *,
    active: str,
    on_select: Callable[[str], None],
) -> None:
    palette = theme.palette
    for name, label in _TAB_LABELS:
        selected = name == active
        _button(
            parent,
            label,
            lambda n=name: on_select(n),
            theme=theme,
            bg=palette.accent if selected else palette.bg,
            fg=palette.fg if selected else palette.muted,
            font_role="body_bold" if selected else "body",
            padx=16,
            pady=7,
            highlightthickness=0 if selected else 1,
            highlightbackground=None if selected else palette.border,
        ).pack(side="left", padx=(0, 8))


def show_main_window(
    root: tk.Misc,
    profiles: list[ClaudeProfile],
    settings: Settings,
    on_profiles_changed: OnProfilesChanged,
    on_settings_changed: OnSettingsChanged,
    *,
    tab: str = TAB_PROFILES,
    theme: Theme = DEFAULT_THEME,
    existing: tk.Toplevel | None = None,
    window_icon: Any | None = None,
    window_size: str | None = None,
    on_window_size_changed: OnWindowSizeChanged | None = None,
    on_tab_changed: OnTabChanged | None = None,
    color_baseline: Mapping[str, str] | None = None,
) -> tk.Toplevel:
    """Build and show the managed window, focused on `tab`.

    `on_profiles_changed(new_profiles)` and `on_settings_changed(new_settings)`
    are each called once, synchronously, on every committed change; the caller
    persists them. A profile change is rerendered here, because the new list is
    the same object handed back. A *settings* change is not: the caller's reply
    to it is a different `Theme`, which only the caller can construct, so it
    re-shows this window (passing the old one as `existing`) and the settings
    being edited are the ones they are drawn in.

    `on_tab_changed(tab)` fires when the user switches tabs, so the caller can
    re-show on the same tab after such a rebuild rather than snapping back.

    `color_baseline` is `settings.colors` as of the moment this window was
    *opened* -- what the settings tab's per-colour "volver" returns to. The
    caller owns it because it must survive the rebuilds a colour change causes;
    `None` means "capture it now", which is right for a fresh open.

    `window_size` is a persisted `"<width>x<height>"`, or `None` to auto-fit
    `tab`'s content; `on_window_size_changed(size)` fires when the window
    closes having been genuinely resized by the user.
    """
    if existing is not None and existing.winfo_exists():
        existing.destroy()

    palette = theme.palette
    window = tk.Toplevel(root)
    window.title(WINDOW_TITLE)
    window.configure(bg=palette.bg)
    # Both tabs share one window, so they share one answer here. The profiles
    # window was resizable and the settings window was not, because size was a
    # profiles-window concept; with one window it is the window's concept, and
    # resizable is the answer that keeps a persisted size meaningful. The
    # settings tab gains the ability to be widened, which at font 16 it wanted.
    window.resizable(True, True)
    window.attributes("-topmost", True)
    apply_window_icon(window, window_icon)

    baseline: Mapping[str, str] = dict(settings.colors) if color_baseline is None else color_baseline

    # Normalize once, up front, so `stored_size` has exactly one meaning below:
    # a usable size, or nothing. An unparseable value (or a merely
    # differently-spelled one, e.g. "800x0700") would otherwise fall back to
    # the auto-fit for layout while still comparing unequal to it at teardown
    # -- and so persist a size the user never chose.
    stored = parse_window_size(window_size)
    stored_size = format_window_size(*stored) if stored is not None else None

    # The live size, tracked off <Configure> rather than read at teardown:
    # querying geometry from inside <Destroy> races the widget's own
    # destruction. `natural` is filled in once the tree is built, and is what
    # tells a user's deliberate resize apart from the auto-fit.
    size: dict[str, str | None] = {"current": stored_size, "natural": None}

    # The render state a tab switch or a profile edit rebuilds against. It
    # lives here rather than in the tab modules because it has to outlive the
    # tree that displays it, and that tree is destroyed on every change.
    #
    # Three named locals rebound through `nonlocal`, rather than the one
    # `dict[str, Any]` this obviously wants to be: a dict of Any is invisible
    # to the type checker exactly where it would help, and this window's whole
    # job is handing `profiles` to one builder and a tab name to a lookup. A
    # `Palette` reaching a `theme:` parameter is the bug this codebase already
    # shipped once, through 138 green tests, on a seam no headless test could
    # reach -- and `state["profiles"]` is that seam with the lights off.
    active_tab: str = tab if tab in TABS else TAB_PROFILES
    shown_profiles: list[ClaudeProfile] = profiles
    editing_id: str | None = None

    outer = tk.Frame(window, bg=palette.panel, padx=_PADDING, pady=_PADDING)
    outer.pack(fill="both", expand=True)

    # Packed before the view and outside it, so the strip and "Cerrar" hold
    # their edges while only the tab's content scrolls between them.
    strip = tk.Frame(outer, bg=palette.panel)
    strip.pack(fill="x", pady=(0, _STRIP_GAP))

    footer = tk.Frame(outer, bg=palette.panel)
    footer.pack(fill="x", side="bottom", pady=(_FOOTER_GAP, 0))

    _button(
        footer,
        "Cerrar",
        window.destroy,
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
        padx=8,
        pady=6,
    ).pack(side="right")

    # Built once per Toplevel: see the module docstring on the wheel binding.
    view = ScrollableView(outer, theme)

    def build_body() -> None:
        for child in strip.winfo_children():
            child.destroy()
        for child in view.content.winfo_children():
            child.destroy()

        _build_tab_strip(strip, theme, active=active_tab, on_select=select_tab)

        if active_tab == TAB_SETTINGS:
            settings_window.build_settings_content(
                view.content,
                settings,
                on_settings_changed,
                profiles=shown_profiles,
                theme=theme,
                baseline=baseline,
            )
        else:
            profiles_window.build_profiles_content(
                view.content,
                shown_profiles,
                on_profiles_changed,
                theme=theme,
                editing_id=editing_id,
                on_rerender=handle_rerender,
            )

        # The viewport did not change, so nothing will fire `<Configure>` to
        # notice that everything inside it did.
        view.content_rebuilt()

    def handle_rerender(new_profiles: list[ClaudeProfile], editing: str | None) -> None:
        nonlocal shown_profiles, editing_id
        shown_profiles = new_profiles
        editing_id = editing
        build_body()

    def select_tab(name: str) -> None:
        nonlocal active_tab, editing_id
        if name == active_tab or name not in TABS:
            return
        active_tab = name
        # An edit form the user walked away from is not a thing to come back
        # to: the list it was editing is what the other tab may have changed.
        editing_id = None
        if on_tab_changed is not None:
            on_tab_changed(name)
        build_body()

    build_body()

    # Sized from the content's request, not from `winfo_height()`: a canvas
    # item propagates no geometry, so the window has no natural height of its
    # own to read back any more. The strip and the footer are outside the
    # viewport, so they are added by hand -- along with the `pady` gaps, which
    # no `winfo_reqheight` accounts for.
    content_width, content_height = view.natural_size()
    chrome_height = (
        strip.winfo_reqheight() + _STRIP_GAP + footer.winfo_reqheight() + _FOOTER_GAP
    )
    work_area = platform.work_area_bounds()
    screen_height = window.winfo_screenheight()
    available = scroll.available_height(work_area, screen_height)

    # The auto-fit is the *clamped* natural size, not the raw one: it is what
    # this window opens at with nothing persisted, and `on_destroy` below
    # compares against it to decide whether the user chose a size or merely
    # accepted ours.
    auto = scroll.fit_height(content_height + chrome_height + 2 * _PADDING, available)
    natural_width = (
        max(content_width, strip.winfo_reqwidth(), footer.winfo_reqwidth())
        + 2 * _PADDING
        + (scroll.SCROLLBAR_SPAN if auto.scrolls else 0)
    )
    natural_height = auto.height
    size["natural"] = format_window_size(natural_width, natural_height)

    width, height = stored if stored is not None else (natural_width, natural_height)
    # A persisted size is clamped too. It was measured on whatever desktop the
    # user had at the time, and a 2000px-tall window restored onto a laptop is
    # the same unreachable "Cerrar" by a different route.
    height = scroll.fit_height(height, available).height
    size["current"] = format_window_size(width, height)

    x = max(scroll.WORK_AREA_MARGIN, (window.winfo_screenwidth() - width) // 2)
    y = scroll.top_placement(work_area, screen_height, height)
    window.geometry(f"{width}x{height}+{x}+{y}")

    def on_configure(event: tk.Event) -> None:
        # <Configure> also fires for descendants; only the Toplevel's own
        # geometry is the window size.
        if event.widget is window:
            size["current"] = format_window_size(event.width, event.height)

    def on_destroy(event: tk.Event) -> None:
        # <Destroy> propagates up from every descendant, so filter to the
        # window itself or this runs once per widget in the tree.
        if event.widget is not window or on_window_size_changed is None:
            return
        current = size["current"]
        if not current or current == stored_size:
            return
        # With nothing persisted yet, only a size the user actually chose is
        # worth storing: persisting the auto-fit would freeze the window at
        # today's content and clip tomorrow's extra profile.
        if stored_size is None and current == size["natural"]:
            return
        on_window_size_changed(current)

    window.bind("<Configure>", on_configure, add="+")
    window.bind("<Destroy>", on_destroy, add="+")
    window.bind("<Escape>", lambda _event: window.destroy())
    window.focus_force()
    return window


__all__ = [
    "TABS",
    "TAB_PROFILES",
    "TAB_SETTINGS",
    "WINDOW_TITLE",
    "OnTabChanged",
    "show_main_window",
]
