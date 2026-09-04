"""claude_usage_tray.ui.hover_popup -- the custom tray hover popup (slice e).

What the native tooltip cannot be: themed, multi-line by structure rather
than by the OS's wrapping whim, and free of the 127-character budget that
`NOTIFYICONDATA.szTip` imposes. Shown when the mouse enters our tray icon's
rectangle and hidden when it leaves, driven by `hover.HoverTracker`.

Structure mirrors `popup.py` deliberately -- same borderless `Toplevel`, same
`palette.border` shell around a `palette.panel` frame, same `theme.font`
roles, same Spanish copy voice. It is not the same window and must not become
one: this is a *hover* surface, so it renders compact text rows rather than
`create_progress_row`'s 252px bars, and it never takes focus.

Two things it deliberately does NOT do, both of which the usage popup does:

- **No `focus_force`.** Stealing focus from whatever the user is working in,
  merely because the mouse passed over a tray icon, would be hostile. It also
  breaks the hover model outright: taking focus can move the cursor's
  effective target and make the leave edge arrive while the popup is still
  under the pointer.
- **No `DismissManager`.** Dismissal is the tracker's leave edge, not a
  click. Registering here would mean a click anywhere destroys a window the
  tracker still believes is open, and the next enter edge would be a no-op
  against a dead handle.

One thing it does that no other window here does: it keeps its `Toplevel`
for the whole session instead of destroying it on every hide. That is not a
performance choice, it is the only arrangement in which the popup is both
visible *and* silent -- see `create_hover_popup` for the two measurements
that force it. The contents are still rebuilt from scratch on every show, so
theming behaves exactly as it does everywhere else.
"""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

from .. import formatting
from ..platform import (
    Rect,
    WorkArea,
    hide_window_without_unmapping,
    prepare_overlay_window,
    present_window_without_activating,
    preserve_frontmost_application,
)
from ..theme import DEFAULT_THEME, Theme

log = logging.getLogger(__name__)

# Spanish labels for the quota keys `formatting.hover_sections` reports.
# Owned here, not in `formatting`, exactly as `popup.py` owns its own row
# labels -- the shorter forms are this surface's call: a hover popup is read
# in a glance, beside the taskbar, not studied.
QUOTA_LABELS: dict[str, str] = {
    "session": "5 horas",
    "weekly": "7 dias",
    "weekly_fable": "Fable 7 dias",
}

# Gap between the popup and the tray icon's rectangle, and from the work
# area's edges. Small: the popup is *about* the icon, so it should read as
# attached to it.
_ICON_GAP = 8
_EDGE_MARGIN = 4

#: Identifies this window to `present_window_without_activating()`, which has
#: no other way to pick one window out of the application's. Never seen: the
#: popup is borderless, so no title is drawn anywhere. Unique on purpose --
#: `popup.py` titles its own window differently.
_WINDOW_TITLE = "claude-usage-tray-hover-popup"


def _quota_row(parent: tk.Widget, label: str, value: float, theme: Theme) -> None:
    palette = theme.palette
    row = tk.Frame(parent, bg=palette.panel)
    row.pack(fill="x", pady=(3, 0))

    tk.Label(
        row,
        text=label,
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("small"),
        anchor="w",
    ).pack(side="left")

    tk.Label(
        row,
        text=f"{value:.0f}%",
        bg=palette.panel,
        # The one place the popup earns its colors: the same thresholds the
        # progress bars use, so a quota that reads "danger" in the usage
        # window reads "danger" here too -- in the user's own palette.
        fg=formatting.bar_color(value, palette),
        font=theme.font("small_bold"),
        anchor="e",
    ).pack(side="right", padx=(12, 0))


def _section(parent: tk.Widget, section: formatting.HoverSection, theme: Theme, *, top_pad: int) -> None:
    palette = theme.palette
    block = tk.Frame(parent, bg=palette.panel)
    block.pack(fill="x", pady=(top_pad, 0))

    tk.Label(
        block,
        text=section.title,
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("body_bold"),
        anchor="w",
    ).pack(fill="x")

    if section.warning:
        tk.Label(
            block,
            text=section.warning,
            bg=palette.panel,
            fg=palette.warn,
            font=theme.font("small"),
            wraplength=220,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

    if section.error:
        tk.Label(
            block,
            text=section.error,
            bg=palette.panel,
            fg=palette.muted,
            font=theme.font("small"),
            wraplength=220,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        return

    for key, value in section.quotas:
        _quota_row(block, QUOTA_LABELS.get(key, key), value, theme)


def _show_without_activating(popup: tk.Toplevel) -> None:
    """Put `popup` on screen without pulling the application to the front.

    `deiconify()` is the ordinary way and it is wrong here on exactly one
    platform. On Aqua it activates the application, and macOS answers an
    activation by switching the user to the Space where that application's
    windows live -- so hovering a menu-bar icon threw the user onto another
    Space, reported as "me mueve a la pantalla principal". The seam routes
    around it where that is a real problem and declines everywhere else,
    which is why this is a fallback and not a branch.

    The two paths are alternatives, never both: the seam's path works
    precisely by not going through the activating one, so calling
    `deiconify()` as well would hand the activation straight back.

    Defensive on purpose. Reaching a native window is the fragile kind of
    thing that a toolkit upgrade can quietly move, and this popup is not
    worth an exception on the UI thread: any failure falls back to the plain
    `deiconify()`, which is what shipped before and is merely the old
    behaviour, not a broken one.
    """
    popup.attributes("-alpha", 1.0)
    try:
        if present_window_without_activating(_WINDOW_TITLE):
            return
    except Exception:
        log.warning(
            "could not show the hover popup without activating the app; "
            "falling back to deiconify",
            exc_info=True,
        )
    popup.deiconify()


def _place(popup: tk.Toplevel, rect: Rect, work_area: WorkArea | None) -> None:
    """Put the popup beside the tray icon, clamped inside the usable desktop.

    Anchored to the icon's rectangle rather than to a screen corner, because
    the taskbar is not always at the bottom and the tray is not always at its
    right. `work_area` may be `None` -- the seam's honest "cannot know" -- in
    which case the full screen is the only bound available.
    """
    popup.update_idletasks()
    # `winfo_reqwidth/reqheight`, not `winfo_width/height`. The popup is
    # deliberately still withdrawn here (it is placed before it is ever seen,
    # so it cannot flash), and an unmapped window has no *actual* size yet:
    # under Aqua Tk both accessors answer 1, so the geometry written below
    # would pin the popup to a 1x1 window that is on screen, correctly
    # positioned, and completely invisible. The requested size is what the
    # geometry managers computed during `update_idletasks`, which is exactly
    # the size the window is about to take.
    width, height = popup.winfo_reqwidth(), popup.winfo_reqheight()

    if work_area is None:
        left, top = 0, 0
        right, bottom = popup.winfo_screenwidth(), popup.winfo_screenheight()
    else:
        left, top, right, bottom = work_area.left, work_area.top, work_area.right, work_area.bottom

    # Above the icon by default (the taskbar is at the bottom in the common
    # case); below it if there is no room above.
    y = rect.top - height - _ICON_GAP
    if y < top + _EDGE_MARGIN:
        y = rect.bottom + _ICON_GAP

    # Centred on the icon horizontally, then clamped so it never hangs off.
    x = rect.left + (rect.width - width) // 2
    x = max(left + _EDGE_MARGIN, min(x, right - width - _EDGE_MARGIN))
    y = max(top + _EDGE_MARGIN, min(y, bottom - height - _EDGE_MARGIN))

    popup.geometry(f"{width}x{height}+{x}+{y}")


def create_hover_popup(root: tk.Misc, *, theme: Theme = DEFAULT_THEME) -> tk.Toplevel:
    """Build the popup's window once, mapped and invisible, ready to be shown.

    This window is created once per session and then kept, which is a
    deliberate break from the destroy-and-rebuild every other window here
    uses. Two measured facts force it, and neither is negotiable:

    - **A window the toolkit has not mapped draws nothing.** Showing it
      through the seam's non-activating path put a correctly sized,
      correctly placed, completely empty rectangle on screen -- `winfo_
      ismapped()` false for the popup and for 0 of its 21 descendants. That
      grey box is what reached a user. Only a mapped window lays out and
      paints its children, so the window has to *stay* mapped, and hiding it
      becomes `hide_window_without_unmapping()` rather than `withdraw()`.
    - **Creating the window is what steals the front, not showing it.**
      Measured: the front moves to us at `tk.Toplevel(...)`, before any
      content exists. So creation happens once, inside
      `preserve_frontmost_application()`, at a moment of our choosing --
      rather than on every hover, which is exactly the gesture that must
      never move the user.

    Born at `-alpha 0.0` so the one map it does undergo is invisible; it is
    ordered off screen immediately afterwards and only alpha and window
    order distinguish shown from hidden from then on.

    Content is *not* built here. It is rebuilt on every show, which is what
    keeps a theme change -- including a custom hex palette -- applying with
    no live-mutation machinery, exactly as destroy-and-rebuild did.
    """
    popup: dict[str, tk.Toplevel] = {}

    def build() -> None:
        window = tk.Toplevel(root)
        # Invisible from birth. There is no `withdraw()` here on purpose:
        # withdrawing is what leaves the window unmapped and empty.
        window.attributes("-alpha", 0.0)
        window.title(_WINDOW_TITLE)  # never drawn; it is how the seam finds it
        window.overrideredirect(True)
        window.configure(bg=theme.palette.bg)
        window.attributes("-topmost", True)
        # Forces the map now, while nothing can be seen, so every later show
        # is a window that has already laid itself out.
        window.update_idletasks()
        popup["window"] = window

    preserve_frontmost_application(build)
    window = popup["window"]

    prepare_overlay_window(_WINDOW_TITLE)
    _hide_without_unmapping(window)
    return window


def _hide_without_unmapping(popup: tk.Toplevel) -> None:
    """Take `popup` off screen, leaving the toolkit's map state alone."""
    popup.attributes("-alpha", 0.0)
    try:
        if hide_window_without_unmapping(_WINDOW_TITLE):
            return
    except Exception:
        log.warning(
            "could not hide the hover popup without unmapping it; "
            "falling back to withdraw",
            exc_info=True,
        )
    popup.withdraw()


def show_hover_popup(
    root: tk.Misc,
    data: dict[str, Any],
    rect: Rect,
    *,
    theme: Theme = DEFAULT_THEME,
    existing: tk.Toplevel | None = None,
    work_area: WorkArea | None = None,
) -> tk.Toplevel:
    """Show the hover popup, anchored to the tray icon at `rect`.

    Reuses `existing` when it is still alive and creates the window
    otherwise -- see `create_hover_popup` for why the window outlives the
    show. The *contents* are still destroyed and rebuilt every time, which is
    what makes a theme change apply with no live-mutation machinery.
    """
    popup = existing if existing is not None and existing.winfo_exists() else None
    if popup is None:
        popup = create_hover_popup(root, theme=theme)

    palette = theme.palette
    for child in popup.winfo_children():
        child.destroy()
    popup.configure(bg=palette.bg)

    shell = tk.Frame(popup, bg=palette.border, padx=1, pady=1)
    shell.pack(fill="both", expand=True)

    frame = tk.Frame(shell, bg=palette.panel, padx=10, pady=8)
    frame.pack(fill="both", expand=True)

    for index, section in enumerate(formatting.hover_sections(data)):
        _section(frame, section, theme, top_pad=0 if index == 0 else 8)

    # Placed while still invisible and still off screen, so the move from
    # the previous show's position is never seen.
    _place(popup, rect, work_area)
    _show_without_activating(popup)
    return popup


def hide_hover_popup(popup: tk.Toplevel | None) -> None:
    """Take `popup` off screen if it still exists. Idempotent, `None`-tolerant.

    Deliberately does not destroy it. The window is the session's, not this
    show's: destroying it would mean building a new one on the next hover,
    and building one both steals the front and starts out unmapped -- the two
    things this surface exists to avoid. See `create_hover_popup`.
    """
    if popup is not None and popup.winfo_exists():
        _hide_without_unmapping(popup)
