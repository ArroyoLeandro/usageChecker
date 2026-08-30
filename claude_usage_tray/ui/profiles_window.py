"""claude_usage_tray.ui.profiles_window -- the "Perfiles" tab's content.

Move-only extraction of `app.py`'s `_show_profiles_window` and its
supporting helpers (`_prompt_add_profile`, `_delete_profile`,
`_profile_status_text`, `_suggested_claude_dir`, `_open_in_explorer`,
`_same_profile_dir`), with feature (a) (editable `config_dir`) and feature
(b) (reordering) added at this surface -- see the profile-management spec.
Every `id == "principal"` guard from the old code is gone: no profile is
structurally protected from edit, delete, or reorder (config.py's Phase 6
rewrite already dropped the "principal" concept entirely; this window is the
first UI surface to rely on that).

CRUD is delegated straight to `config.py`'s functional surface
(`add_profile`/`edit_profile`/`delete_profile`/`reorder_profiles`) -- this
module owns validation-error presentation (Spanish messageboxes matching the
app's existing voice), the caller owns persistence and any usage refresh via
the single `on_profiles_changed` callback. This call pattern is a deliberate
choice, flagged for review: `design.md`'s config.py "Public surface" table
does not list this CRUD quartet verbatim (batch 3's apply-progress already
flagged the gap when it filled it); this window is the first consumer to
lock in a shape against those exact signatures. If a reviewer wants a
different shape (e.g. a single dispatcher, or methods on `AppConfig`), that
is a legitimate design conversation -- not one this batch can resolve alone.

**This module no longer owns a window.** It builds its content into a frame
`main_window.py` hands it, which is the `ScrollableView` content frame of the
one Toplevel the app's two tabs now share. Everything geometric -- clamping to
the work area, scrolling, sizing, the persisted `window_size`, placement --
lives there now. What used to be documented here about *why* the height is
clamped (resizable is not scrollable: dragging the window shorter only clips
the content, because `pack` has nowhere to put what no longer fits, so a long
profile list put "Agregar"/"Cerrar" below the desktop either way) still holds,
and moved with the code that enforces it. `settings_window.py` was split the
same way at the same time.

Rerendering is likewise the caller's: an edit-in-progress is a property of
this tab, but the tree it lives in is rebuilt by the window that owns it, so
`editing_id` is passed *in* and `on_rerender` asks for a new one rather than
this module rebuilding a window it no longer has.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

from .. import paths, platform
from ..config import (
    ClaudeProfile,
    DuplicateConfigDirError,
    ProfileNotFoundError,
    add_profile,
    default_claude_dir,
    delete_profile,
    edit_profile,
    reorder_profiles,
)
from ..theme import Theme
from .widgets import button as _button

TAB_TITLE = "Perfiles"

OnProfilesChanged = Callable[[list[ClaudeProfile]], None]
OnWindowSizeChanged = Callable[[str], None]

# Ask the owning window for a fresh tree: `(profiles, editing_id)`.
# The `str | None` is quoted, as in `hover.py`: a type alias is an ordinary
# assignment evaluated at import time, so `from __future__ import annotations`
# does not defer it and the PEP 604 union is a hard error before 3.10.
OnRerender = Callable[[list[ClaudeProfile], "str | None"], None]


def _profile_status_text(profile: ClaudeProfile) -> str:
    if profile.credentials_path.exists():
        return "Listo para consultar el uso"
    return "Hace falta iniciar sesion en esta carpeta"


def _open_in_explorer(target: Path, parent: tk.Misc) -> None:
    """Open `target` (or its nearest existing ancestor) in the platform file
    manager. Move-only extraction of `app.py`'s `_open_in_explorer`, now
    delegating the "path is gone, fall back to its parent" logic to
    `paths.nearest_existing_dir()` (Phase 3) instead of re-inlining it.
    """
    destination = paths.nearest_existing_dir(target)
    try:
        platform.open_in_file_manager(destination)
    except platform.FileManagerError:
        messagebox.showerror(
            "Perfiles",
            f"No se pudo abrir la carpeta:\n{destination}",
            parent=parent,
        )


def _build_view_row(
    row: tk.Frame,
    profile: ClaudeProfile,
    theme: Theme,
    *,
    is_first: bool,
    is_last: bool,
    on_delete: Callable[[], None],
    on_edit: Callable[[], None],
    on_move_up: Callable[[], None],
    on_move_down: Callable[[], None],
) -> None:
    palette = theme.palette

    title_row = tk.Frame(row, bg=palette.bg)
    title_row.pack(fill="x")

    tk.Label(
        title_row,
        text=profile.name,
        bg=palette.bg,
        fg=palette.fg,
        font=theme.font("body_bold"),
        anchor="w",
    ).pack(side="left")

    _button(title_row, "Eliminar", on_delete, theme=theme, bg=palette.bg, fg=palette.muted).pack(side="right")
    _button(
        title_row, "Editar", on_edit, theme=theme, bg=palette.bg, fg=palette.muted
    ).pack(side="right", padx=(0, 6))
    if not is_last:
        _button(
            title_row, "▼", on_move_down, theme=theme, bg=palette.bg, fg=palette.muted, font_role="small"
        ).pack(side="right", padx=(0, 6))
    if not is_first:
        _button(
            title_row, "▲", on_move_up, theme=theme, bg=palette.bg, fg=palette.muted, font_role="small"
        ).pack(side="right", padx=(0, 6))

    tk.Label(
        row,
        text=_profile_status_text(profile),
        bg=palette.bg,
        fg=palette.accent,
        font=theme.font("small_bold"),
        anchor="w",
    ).pack(fill="x", pady=(4, 0))

    tk.Label(
        row,
        text=str(profile.config_dir),
        bg=palette.bg,
        fg=palette.muted,
        font=theme.font("small"),
        wraplength=420,
        justify="left",
        anchor="w",
    ).pack(fill="x", pady=(4, 0))


def _build_edit_row(
    row: tk.Frame,
    profile: ClaudeProfile,
    theme: Theme,
    window: tk.Misc,
    *,
    on_save: Callable[[str, str], None],
    on_cancel: Callable[[], None],
) -> None:
    palette = theme.palette

    tk.Label(row, text="Nombre", bg=palette.bg, fg=palette.muted, font=theme.font("small"), anchor="w").pack(
        fill="x"
    )
    name_var = tk.StringVar(value=profile.name)
    tk.Entry(
        row,
        textvariable=name_var,
        bg=palette.panel,
        fg=palette.fg,
        insertbackground=palette.fg,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
    ).pack(fill="x", ipady=4, pady=(2, 6))

    tk.Label(
        row, text="Carpeta de Claude", bg=palette.bg, fg=palette.muted, font=theme.font("small"), anchor="w"
    ).pack(fill="x")
    path_var = tk.StringVar(value=str(profile.config_dir))
    path_row = tk.Frame(row, bg=palette.bg)
    path_row.pack(fill="x", pady=(2, 0))
    tk.Entry(
        path_row,
        textvariable=path_var,
        bg=palette.panel,
        fg=palette.fg,
        insertbackground=palette.fg,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
    ).pack(side="left", fill="x", expand=True, ipady=4)
    _button(
        path_row,
        "Examinar",
        lambda: path_var.set(
            filedialog.askdirectory(parent=window, initialdir=path_var.get() or str(Path.home())) or path_var.get()
        ),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left", padx=(8, 0))

    actions = tk.Frame(row, bg=palette.bg)
    actions.pack(fill="x", pady=(8, 0))
    _button(
        actions,
        "Guardar",
        lambda: on_save(name_var.get(), path_var.get()),
        theme=theme,
        bg=palette.accent,
        fg=palette.fg,
        font_role="body_bold",
        padx=12,
        pady=6,
    ).pack(side="left")
    _button(
        actions,
        "Cancelar",
        on_cancel,
        theme=theme,
        bg=palette.bg,
        fg=palette.muted,
        padx=8,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left", padx=(8, 0))



def build_profiles_content(
    parent: tk.Widget,
    profiles: list[ClaudeProfile],
    on_profiles_changed: OnProfilesChanged,
    *,
    theme: Theme,
    editing_id: str | None,
    on_rerender: OnRerender,
) -> None:
    """Build the Perfiles tab's widgets into `parent`.

    `on_profiles_changed(new_profiles)` is called once, synchronously, after
    every successful add/edit/delete/reorder -- the caller is expected to
    persist it (`save_config`) and kick off any usage refresh it wants; this
    module has no opinion about I/O beyond the widgets. `on_rerender` then
    asks the owning window for a fresh tree against the new list, which is
    `app.py`'s original destroy-and-rebuild-on-every-mutation pattern with the
    rebuild moved to whoever owns the Toplevel.

    `editing_id` names the profile whose row is currently an edit form, or
    `None`. It is passed in rather than held here because this tree does not
    survive its own rerender -- state that has to outlive a rebuild has to
    live above it.
    """
    palette = theme.palette
    window = parent.winfo_toplevel()

    def apply_change(new_profiles: list[ClaudeProfile]) -> None:
        on_profiles_changed(new_profiles)
        on_rerender(new_profiles, None)

    def handle_delete(profile: ClaudeProfile) -> None:
        confirmed = messagebox.askyesno(
            "Eliminar perfil",
            f'Se quitara "{profile.name}" de esta lista.\n\nLa carpeta original no se borra.',
            parent=window,
        )
        if not confirmed:
            return
        apply_change(delete_profile(profiles, profile.id))

    def handle_move(profile: ClaudeProfile, direction: int) -> None:
        ids = [item.id for item in profiles]
        index = ids.index(profile.id)
        target = index + direction
        if target < 0 or target >= len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        apply_change(reorder_profiles(profiles, ids))

    def handle_edit_submit(profile: ClaudeProfile, name: str, raw_path: str) -> None:
        raw_path = raw_path.strip()
        config_dir = Path(raw_path) if raw_path else None
        try:
            updated = edit_profile(profiles, profile.id, name=name, config_dir=config_dir)
        except DuplicateConfigDirError:
            messagebox.showinfo("Perfil", "Esa carpeta ya esta agregada.", parent=window)
            return
        except ProfileNotFoundError:
            messagebox.showerror("Perfil", "Este perfil ya no existe.", parent=window)
            return
        apply_change(updated)

    tk.Label(
        parent,
        text="Agrega otros perfiles de Claude para ver su uso en un solo lugar.",
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("body"),
        anchor="w",
    ).pack(fill="x")

    list_frame = tk.Frame(parent, bg=palette.panel)
    list_frame.pack(fill="x", pady=(10, 0))

    for index, profile in enumerate(profiles):
        row = tk.Frame(list_frame, bg=palette.bg, padx=10, pady=7)
        row.pack(fill="x", pady=(0, 6))

        if editing_id == profile.id:
            _build_edit_row(
                row,
                profile,
                theme,
                window,
                on_save=lambda name, path, p=profile: handle_edit_submit(p, name, path),
                on_cancel=lambda: on_rerender(profiles, None),
            )
        else:
            _build_view_row(
                row,
                profile,
                theme,
                is_first=index == 0,
                is_last=index == len(profiles) - 1,
                on_delete=lambda p=profile: handle_delete(p),
                on_edit=lambda p=profile: on_rerender(profiles, p.id),
                on_move_up=lambda p=profile: handle_move(p, -1),
                on_move_down=lambda p=profile: handle_move(p, 1),
            )

    form = tk.Frame(parent, bg=palette.panel)
    form.pack(fill="x", pady=(6, 0))

    tk.Label(
        form,
        text="Agregar otro perfil",
        bg=palette.panel,
        fg=palette.fg,
        font=theme.font("heading"),
        anchor="w",
    ).pack(fill="x")

    name_var = tk.StringVar()
    path_var = tk.StringVar()

    tk.Label(form, text="Nombre", bg=palette.panel, fg=palette.muted, font=theme.font("small"), anchor="w").pack(
        fill="x", pady=(10, 0)
    )
    tk.Entry(
        form,
        textvariable=name_var,
        bg=palette.bg,
        fg=palette.fg,
        insertbackground=palette.fg,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
    ).pack(fill="x", ipady=5)

    tk.Label(
        form, text="Carpeta de Claude", bg=palette.panel, fg=palette.muted, font=theme.font("small"), anchor="w"
    ).pack(fill="x", pady=(10, 0))

    tk.Label(
        form,
        text=(
            "Elegí la carpeta donde Claude guarda tu sesion.\n"
            "Suele verse como: C:/Users/TU_USUARIO/.claude  o  \\\\wsl.localhost\\Ubuntu\\home\\TU_USUARIO\\.claude"
        ),
        bg=palette.panel,
        fg=palette.muted,
        font=theme.font("small"),
        wraplength=420,
        justify="left",
        anchor="w",
    ).pack(fill="x", pady=(4, 0))

    path_row = tk.Frame(form, bg=palette.panel)
    path_row.pack(fill="x", pady=(8, 0))

    tk.Entry(
        path_row,
        textvariable=path_var,
        bg=palette.bg,
        fg=palette.fg,
        insertbackground=palette.fg,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
    ).pack(side="left", fill="x", expand=True, ipady=5)

    _button(
        path_row,
        "Examinar",
        lambda: path_var.set(
            filedialog.askdirectory(parent=window, initialdir=str(Path.home())) or path_var.get()
        ),
        theme=theme,
        bg=palette.bg,
        fg=palette.fg,
        padx=10,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left", padx=(8, 0))

    helpers = tk.Frame(form, bg=palette.panel)
    helpers.pack(fill="x", pady=(8, 0))

    _button(
        helpers,
        "Usar el perfil actual",
        lambda: path_var.set(str(default_claude_dir())),
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
        font_role="small",
        padx=8,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left")

    _button(
        helpers,
        "Abrir carpeta",
        lambda: _open_in_explorer(default_claude_dir(), window),
        theme=theme,
        bg=palette.panel,
        fg=palette.muted,
        font_role="small",
        padx=8,
        pady=4,
        highlightthickness=1,
        highlightbackground=palette.border,
    ).pack(side="left", padx=(8, 0))

    actions = tk.Frame(form, bg=palette.panel)
    actions.pack(fill="x", pady=(12, 0))

    def handle_add_submit() -> None:
        name = name_var.get().strip()
        if not name:
            messagebox.showerror("Perfil", "Ingresá un nombre visible para el perfil.", parent=window)
            return
        raw_path = path_var.get().strip()
        if not raw_path:
            messagebox.showerror("Perfil", "Elegí una carpeta valida de Claude.", parent=window)
            return
        try:
            updated = add_profile(profiles, name=name, config_dir=Path(raw_path))
        except DuplicateConfigDirError:
            messagebox.showinfo("Perfil", "Esa carpeta ya esta agregada.", parent=window)
            return
        apply_change(updated)

    _button(
        actions,
        "Agregar",
        handle_add_submit,
        theme=theme,
        bg=palette.accent,
        fg=palette.fg,
        font_role="body_bold",
        padx=12,
        pady=6,
    ).pack(side="left")


__all__ = ["TAB_TITLE", "OnProfilesChanged", "OnRerender", "OnWindowSizeChanged", "build_profiles_content"]
