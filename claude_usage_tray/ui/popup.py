"""claude_usage_tray.ui.popup -- the usage popup window.

Move-only extraction of `app.py`'s `_show_popup` / `_render_profile_section`
/ `_weekly_rows` / `_bind_popup_dismiss`, parameterized by `Theme` (default
`DEFAULT_THEME`) instead of the module-level color/font literals they used
to close over. The click-outside-to-dismiss half that used to bind/unbind globally
now goes through a `DismissManager` (see `dismiss.py`) instead of directly
calling `root.bind_all`/`unbind_all` -- see design.md's dismiss decision for
why.

Its three buttons -- close, "Perfiles", refresh -- were the last hand-rolled
`tk.Button`s in the app, carrying a copy of the flat-button option block
`widgets.button` already owned. They now go through that factory like every
other button. That was not cosmetic: this is the window the user actually
looks at, and on macOS those three rendered as native grey pills on the dark
panel no matter what colours they were handed.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from typing import Any, Callable

from .. import formatting, platform
from ..config import ClaudeProfile
from ..theme import DEFAULT_THEME, Theme
from .dismiss import DismissManager
from .widgets import apply_window_icon, button, create_progress_row

WINDOW_TITLE = "ClaudeUsage — Uso Claude"

# Shown as the section title when `data["profiles"]` is empty -- i.e. zero
# profiles configured (not merely a fetch error on one). Matches
# `formatting.tooltip()`'s own generic fallback name for the same situation
# (see `formatting.py`'s `_profile_display_name`), so the two surfaces stay
# consistent with each other.
FALLBACK_PROFILE_NAME = "Perfil"


def _weekly_rows(data: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, bool]]:
    rows: list[tuple[str, dict[str, Any] | None, bool]] = []
    if data.get("weekly") is not None:
        rows.append(("Ultimos 7 dias", data.get("weekly"), True))
    if data.get("weekly_fable") is not None:
        rows.append(("Fable en los ultimos 7 dias", data.get("weekly_fable"), False))
    return rows


def _render_profile_section(
    parent: tk.Widget,
    title: str,
    data: dict[str, Any],
    *,
    theme: Theme,
    top_pad: int,
) -> None:
    palette = theme.palette
    section = tk.Frame(parent, bg=palette.panel)
    section.pack(fill="x", pady=(top_pad, 0))

    tk.Label(
        section,
        text=title,
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("heading"),
        anchor="w",
    ).pack(fill="x")

    warning = data.get("meta", {}).get("warning")
    has_usage_data = (
        formatting.pct(data.get("session")) is not None or formatting.pct(data.get("weekly")) is not None
    )

    if warning:
        tk.Label(
            section,
            text=warning,
            bg=palette.panel,
            fg=palette.warn,
            font=theme.font("body_bold"),
            wraplength=340,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

    if data.get("error") and not has_usage_data:
        tk.Label(
            section,
            text=data["error"],
            bg=palette.panel,
            fg=palette.fg,
            font=theme.font("body"),
            wraplength=340,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))
        return

    create_progress_row(section, "Ultimas 5 horas", data.get("session"), theme=theme, top_pad=5, emphasis=True)
    for label, entry, emphasis in _weekly_rows(data):
        create_progress_row(section, label, entry, theme=theme, top_pad=6, emphasis=emphasis)


def _bind_focus_dismiss(popup: tk.Toplevel) -> None:
    """Dismiss once focus has genuinely left the popup.

    Unrelated to `DismissManager`: this binding is scoped to `popup` alone
    (`popup.bind`, never `root.bind_all`), so it carries none of the
    cross-window scope-unsafety that motivated the registry -- it is
    move-only, unchanged from `app.py`'s original `_bind_popup_dismiss`.
    """

    def is_within(widget: tk.Misc | None, ancestor: tk.Misc) -> bool:
        while widget is not None:
            if widget == ancestor:
                return True
            widget = widget.master
        return False

    def check_focus() -> None:
        if not popup.winfo_exists():
            return
        focused = popup.focus_get()
        if focused is None or not is_within(focused, popup):
            popup.destroy()

    def on_focus_out(_event: tk.Event | None = None) -> None:
        popup.after(150, check_focus)

    popup.bind("<FocusOut>", on_focus_out, add="+")


def show_popup(
    root: tk.Misc,
    data: dict[str, Any],
    updated_at: datetime | None,
    *,
    theme: Theme = DEFAULT_THEME,
    existing: tk.Toplevel | None = None,
    window_icon: Any | None = None,
    dismiss_manager: DismissManager | None = None,
    on_refresh: Callable[[], None] | None = None,
    on_manage_profiles: Callable[[], None] | None = None,
) -> tk.Toplevel:
    """Build, position, and show the usage popup as a borderless `Toplevel`.

    The caller owns window lifecycle bookkeeping: pass the previously
    returned window as `existing` so it is destroyed before the new one is
    built (mirrors `app.py`'s original `if self._popup and
    self._popup.winfo_exists(): self._popup.destroy()` guard at the top of
    `_show_popup`). If `dismiss_manager` is given, the popup registers for
    click-outside dismissal there instead of the caller managing a global
    binding itself. `on_refresh`/`on_manage_profiles` are called
    synchronously on the UI thread -- if the caller wants refresh to run in
    a background thread (the original did), that is the caller's callback to
    write, not this module's concern.
    """
    if existing is not None and existing.winfo_exists():
        existing.destroy()

    palette = theme.palette
    popup = tk.Toplevel(root)
    popup.title(WINDOW_TITLE)
    popup.configure(bg=palette.bg)
    popup.resizable(False, False)
    popup.attributes("-topmost", True)
    popup.overrideredirect(True)
    apply_window_icon(popup, window_icon)

    shell = tk.Frame(popup, bg=palette.border, padx=1, pady=1)
    shell.pack(fill="both", expand=True)

    frame = tk.Frame(shell, bg=palette.panel, padx=12, pady=12)
    frame.pack(fill="both", expand=True)

    header = tk.Frame(frame, bg=palette.panel)
    header.pack(fill="x")

    tk.Label(
        header,
        text="Uso del plan Max (5x)",
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("heading"),
        anchor="w",
    ).pack(side="left")

    button(
        header,
        "×",
        popup.destroy,
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
        font_role="title",
    ).pack(side="right")

    profiles = data.get("profiles")
    if isinstance(profiles, list) and profiles:
        for index, item in enumerate(profiles):
            profile = item.get("profile")
            profile_data = item.get("data", {})
            title = profile.name if isinstance(profile, ClaudeProfile) else f"Perfil {index + 1}"
            _render_profile_section(frame, title, profile_data, theme=theme, top_pad=10 if index == 0 else 12)
    else:
        _render_profile_section(frame, FALLBACK_PROFILE_NAME, data.get("primary", data), theme=theme, top_pad=10)

    footer = tk.Frame(frame, bg=palette.panel)
    footer.pack(fill="x", pady=(12, 0))

    tk.Label(
        footer,
        text=formatting.updated_text(updated_at),
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("body"),
        anchor="w",
    ).pack(side="left")

    button(
        footer,
        "Perfiles",
        on_manage_profiles,
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
    ).pack(side="right", padx=(0, 10))

    button(
        footer,
        "↻",
        on_refresh,
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
        font_role="title",
    ).pack(side="right")

    popup.update_idletasks()
    w, h = popup.winfo_width(), popup.winfo_height()
    work_area = platform.work_area_bounds()
    if work_area is None:
        work_top = 0
        work_right = popup.winfo_screenwidth()
        work_bottom = popup.winfo_screenheight()
    else:
        work_top = work_area.top
        work_right, work_bottom = work_area.right, work_area.bottom
    margin_x, margin_y = 26, 12
    x = work_right - w - margin_x
    # Hang the popup off whichever edge the tray icon actually lives on:
    # under the menu bar on macOS, above the taskbar on Windows and Linux.
    # Anchoring at the bottom everywhere would, on a Mac, drop the popup at
    # the far corner from the icon that opened it -- and on top of the Dock.
    if platform.tray_anchor_edge() == platform.TRAY_ANCHOR_TOP:
        y = work_top + margin_y
    else:
        y = work_bottom - h - margin_y
    popup.geometry(f"{w}x{h}+{x}+{y}")

    popup.bind("<Escape>", lambda _e: popup.destroy())
    popup.protocol("WM_DELETE_WINDOW", popup.destroy)
    _bind_focus_dismiss(popup)
    if dismiss_manager is not None:
        dismiss_manager.register(popup, popup.destroy)
    popup.focus_force()
    return popup
