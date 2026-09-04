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
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

from .. import formatting
from ..platform import Rect, WorkArea
from ..theme import DEFAULT_THEME, Theme

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


def show_hover_popup(
    root: tk.Misc,
    data: dict[str, Any],
    rect: Rect,
    *,
    theme: Theme = DEFAULT_THEME,
    existing: tk.Toplevel | None = None,
    work_area: WorkArea | None = None,
) -> tk.Toplevel:
    """Build and show the hover popup anchored to the tray icon at `rect`.

    Destroy-and-rebuild on every show, like every other window here, which is
    what makes a theme change -- including a custom hex palette -- apply with
    no live-mutation machinery at all.
    """
    hide_hover_popup(existing)

    palette = theme.palette
    popup = tk.Toplevel(root)
    popup.withdraw()  # place it before it is ever seen, so it cannot flash
    popup.overrideredirect(True)
    popup.configure(bg=palette.bg)
    popup.attributes("-topmost", True)

    shell = tk.Frame(popup, bg=palette.border, padx=1, pady=1)
    shell.pack(fill="both", expand=True)

    frame = tk.Frame(shell, bg=palette.panel, padx=10, pady=8)
    frame.pack(fill="both", expand=True)

    for index, section in enumerate(formatting.hover_sections(data)):
        _section(frame, section, theme, top_pad=0 if index == 0 else 8)

    _place(popup, rect, work_area)
    popup.deiconify()
    return popup


def hide_hover_popup(popup: tk.Toplevel | None) -> None:
    """Destroy `popup` if it still exists. Idempotent, and `None`-tolerant."""
    if popup is not None and popup.winfo_exists():
        popup.destroy()
