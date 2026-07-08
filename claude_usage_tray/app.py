"""Monitor de uso de Claude en la bandeja de Windows."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

if sys.platform == "win32":
    import winreg

from PIL import Image, ImageDraw, ImageTk

import pystray

from .api import fetch_usage_bundle
from .config import ClaudeProfile, default_profile, load_profiles, normalize_config_dir, save_profiles

POLL_SECONDS = 300
WINDOW_TITLE = "Uso Claude"
BG = "#262522"
PANEL = "#2f2d29"
TRACK = "#4a4743"
FG = "#f7f3ee"
MUTED = "#b9b1a6"
ACCENT = "#d97757"
WARN = "#f59e0b"
DANGER = "#ef4444"
LINK = "#8ab4ff"
BORDER = "#3a3733"
DAY_NAMES = ["lun.", "mar.", "mie.", "jue.", "vie.", "sab.", "dom."]
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ClaudeUsage"
APP_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "claude_usage_icon.ico"


def _pct(entry: dict[str, Any] | None) -> float | None:
    if not entry:
        return None
    try:
        return float(entry.get("utilization", 0))
    except (TypeError, ValueError):
        return None


def _reset_text(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        reset = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff = reset - now
        total_min = max(0, int(diff.total_seconds() // 60))
        clock = reset.astimezone().strftime("%H:%M")
        if total_min >= 60:
            return f"en {total_min // 60}h {total_min % 60}m ({clock})"
        if total_min > 0:
            return f"en {total_min}m ({clock})"
        return f"pronto ({clock})"
    except Exception:
        return "—"


def _reset_label(iso_str: str | None) -> str:
    if not iso_str:
        return "Se restablece: —"
    try:
        reset = datetime.fromisoformat(iso_str).astimezone()
        now = datetime.now().astimezone()
        if reset.date() == now.date():
            total_sec = max(0, int((reset - now).total_seconds()))
            if total_sec < 60:
                return "Se restablece pronto"
            total_min = total_sec // 60
            if total_min < 60:
                return f"Se restablece en {total_min} min"
            hours = total_min // 60
            mins = total_min % 60
            if mins == 0:
                return f"Se restablece en {hours} h"
            return f"Se restablece en {hours} h {mins} min"
        day = DAY_NAMES[reset.weekday()]
        return f"Se restablece {day} {reset.strftime('%H:%M')} hs"
    except Exception:
        return "Se restablece: —"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return f'"{exe}" "{launcher}"'


def _is_startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
        return True
    except OSError:
        return False


def _set_startup_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


def _bar_color(pct: float) -> str:
    if pct >= 90:
        return DANGER
    if pct >= 70:
        return WARN
    return ACCENT


def _tooltip(bundle: dict[str, Any]) -> str:
    profiles = bundle.get("profiles")
    if isinstance(profiles, list) and profiles:
        lines = ["Claude — uso"]
        for item in profiles[:4]:
            profile = item.get("profile")
            data = item.get("data", {})
            session = _pct(data.get("session"))
            weekly = _pct(data.get("weekly"))
            if session is None and weekly is None:
                error = data.get("error", "Sin datos")
                name = profile.name if isinstance(profile, ClaudeProfile) else "Perfil"
                lines.append(f"{name}: {error}")
                continue
            parts = []
            if session is not None:
                parts.append(f"5h {session:.0f}%")
            if weekly is not None:
                parts.append(f"7d {weekly:.0f}%")
            name = profile.name if isinstance(profile, ClaudeProfile) else "Perfil"
            lines.append(f"{name}: {' · '.join(parts)}")
        return "\n".join(lines)

    data = bundle.get("primary", bundle)
    if data.get("error") and _pct(data.get("session")) is None and _pct(data.get("weekly")) is None:
        return f"Claude uso\n{data['error']}"

    session = _pct(data.get("session"))
    weekly = _pct(data.get("weekly"))
    fable = _pct(data.get("weekly_fable"))
    lines = ["Claude — uso"]
    warning = data.get("meta", {}).get("warning")
    if warning:
        lines.append("Mostrando la ultima informacion disponible")
    if session is not None:
        lines.append(f"5h: {session:.0f}%")
    if weekly is not None:
        lines.append(f"7d: {weekly:.0f}%")
    if fable is not None:
        lines.append(f"Fable: {fable:.0f}%")
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def _availability_pct(data: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in ("session", "weekly", "weekly_fable"):
        used = _pct(data.get(key))
        if used is None:
            continue
        values.append(max(0.0, min(100.0, 100.0 - used)))
    if not values:
        return None
    return min(values)


def _availability_pct_from_bundle(bundle: dict[str, Any]) -> float | None:
    profile_items = bundle.get("profiles")
    values: list[float] = []
    if isinstance(profile_items, list) and profile_items:
        for item in profile_items:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if isinstance(data, dict):
                availability = _availability_pct(data)
                if availability is not None:
                    values.append(availability)
    else:
        data = bundle.get("primary", bundle)
        if isinstance(data, dict):
            availability = _availability_pct(data)
            if availability is not None:
                values.append(availability)
    if not values:
        return None
    return min(values)


def _tray_level(availability: float | None) -> int:
    if availability is None:
        return 0
    if availability >= 80:
        return 4
    if availability >= 50:
        return 3
    if availability >= 20:
        return 2
    return 1


def _work_area_bounds() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the usable desktop area."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, 0, 0


def _updated_text(updated_at: datetime | None) -> str:
    if updated_at is None:
        return "Ultima actualizacion: sin datos"
    now = datetime.now()
    diff_seconds = max(0, int((now - updated_at).total_seconds()))
    if diff_seconds < 60:
        human = "hace menos de un minuto"
    else:
        minutes = diff_seconds // 60
        human = f"hace {minutes} min"
    return f"Ultima actualizacion: {human}"


def _parse_meta_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _make_icon(bundle: dict[str, Any]) -> Image.Image:
    data = bundle.get("primary", bundle)
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    session = _pct(data.get("session"))
    weekly = _pct(data.get("weekly"))

    if data.get("error") and session is None and weekly is None:
        draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=PANEL)
        draw.rounded_rectangle((28, 14, 36, 40), radius=4, fill=DANGER)
        draw.ellipse((28, 46, 36, 54), fill=DANGER)
        return img

    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=PANEL)
    active_bars = _tray_level(_availability_pct_from_bundle(bundle))
    heights = [14, 22, 30, 38]
    left = 13
    bottom = 50
    bar_width = 8
    gap = 4

    for index, height in enumerate(heights, start=1):
        x1 = left + (index - 1) * (bar_width + gap)
        x2 = x1 + bar_width
        y1 = bottom - height
        draw.rounded_rectangle((x1, y1, x2, bottom), radius=3, fill=TRACK)
        if index <= active_bars:
            draw.rounded_rectangle((x1, y1, x2, bottom), radius=3, fill=ACCENT)

    draw.rounded_rectangle((11, 10, 53, 52), radius=10, outline=BORDER, width=2)
    return img


def _window_icon_image(size: int = 32) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=7, fill=PANEL, outline=BORDER, width=1)
    heights = [7, 11, 15, 19]
    left = 6
    bottom = size - 6
    bar_width = 4
    gap = 2
    for index, height in enumerate(heights):
        x1 = left + index * (bar_width + gap)
        x2 = x1 + bar_width
        y1 = bottom - height
        draw.rounded_rectangle((x1, y1, x2, bottom), radius=2, fill=ACCENT)
    return img


class UsageTrayApp:
    def __init__(self) -> None:
        self._profiles = load_profiles()
        self._data: dict[str, Any] = {
            "primary": {"error": "Cargando…"},
            "profiles": [],
        }
        self._updated_at: datetime | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()
        self._popup: tk.Toplevel | None = None
        self._profiles_window: tk.Toplevel | None = None
        self._root = tk.Tk()
        self._root.withdraw()
        self._window_icon = ImageTk.PhotoImage(_window_icon_image())
        self._apply_window_icon(self._root)
        self._root.after(100, self._process_ui_queue)

        menu_items: list[Any] = [
            pystray.MenuItem("Ver uso", self._on_show, default=True),
            pystray.MenuItem("Actualizar", self._on_refresh),
            pystray.MenuItem("Perfiles", self._on_manage_profiles),
        ]
        if sys.platform == "win32":
            menu_items.append(
                pystray.MenuItem(
                    "Iniciar con Windows",
                    self._on_toggle_startup,
                    checked=lambda _item: _is_startup_enabled(),
                )
            )
        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self._on_quit),
            ]
        )
        menu = pystray.Menu(*menu_items)
        self._icon = pystray.Icon("claude-usage", _make_icon(self._data), _tooltip(self._data), menu)

    def _apply_window_icon(self, window: tk.Misc) -> None:
        try:
            window.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass
        if sys.platform == "win32" and APP_ICON_PATH.exists():
            try:
                window.iconbitmap(default=str(APP_ICON_PATH))
            except tk.TclError:
                pass

    def _process_ui_queue(self) -> None:
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            fn()
        if not self._stop.is_set():
            self._root.after(100, self._process_ui_queue)

    def _run_on_ui(self, fn) -> None:
        self._ui_queue.put(fn)

    def _set_data(self, data: dict[str, Any]) -> None:
        primary = data.get("primary", data)
        with self._lock:
            self._data = data
            fetched_at = _parse_meta_datetime(primary.get("meta", {}).get("fetched_at"))
            self._updated_at = fetched_at or datetime.now()
        self._icon.icon = _make_icon(data)
        self._icon.title = _tooltip(data)

    def _create_progress_row(
        self,
        parent: tk.Widget,
        label: str,
        entry: dict[str, Any] | None,
        *,
        top_pad: int = 8,
        emphasis: bool = False,
    ) -> tk.Frame:
        pct = _pct(entry)
        value = f"{pct:.0f}% usado" if pct is not None else "—"

        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(top_pad, 0))

        header = tk.Frame(row, bg=PANEL)
        header.pack(fill="x")

        tk.Label(
            header,
            text=label,
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 9, "bold" if emphasis else "normal"),
            anchor="w",
        ).pack(side="left")

        tk.Label(
            header,
            text=_reset_label(entry.get("resets_at") if entry else None),
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="e",
        ).pack(side="right")

        bar_row = tk.Frame(row, bg=PANEL)
        bar_row.pack(fill="x", pady=(2, 0))

        canvas = tk.Canvas(
            bar_row,
            width=252,
            height=10,
            bg=PANEL,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(side="left", fill="x", expand=True)

        width = 252
        bar_height = 6
        y = 2
        canvas.create_rectangle(0, y, width, y + bar_height, fill=TRACK, outline=TRACK)
        if pct is not None:
            fill_width = max(0, min(width, int(width * pct / 100)))
            canvas.create_rectangle(0, y, fill_width, y + bar_height, fill=_bar_color(pct), outline=_bar_color(pct))

        tk.Label(
            bar_row,
            text=value,
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 8, "bold"),
            anchor="e",
        ).pack(side="right", padx=(8, 0))

        return row

    def _weekly_rows(self, data: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, bool]]:
        rows: list[tuple[str, dict[str, Any] | None, bool]] = []
        if data.get("weekly") is not None:
            rows.append(("Ultimos 7 dias", data.get("weekly"), True))
        if data.get("weekly_fable") is not None:
            rows.append(("Fable en los ultimos 7 dias", data.get("weekly_fable"), False))
        return rows

    def _render_profile_section(self, parent: tk.Widget, title: str, data: dict[str, Any], *, top_pad: int) -> None:
        section = tk.Frame(parent, bg=PANEL)
        section.pack(fill="x", pady=(top_pad, 0))

        tk.Label(
            section,
            text=title,
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")

        warning = data.get("meta", {}).get("warning")
        has_usage_data = _pct(data.get("session")) is not None or _pct(data.get("weekly")) is not None

        if warning:
            tk.Label(
                section,
                text=warning,
                bg=PANEL,
                fg=WARN,
                font=("Segoe UI", 9, "bold"),
                wraplength=340,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

        if data.get("error") and not has_usage_data:
            tk.Label(
                section,
                text=data["error"],
                bg=PANEL,
                fg=FG,
                font=("Segoe UI", 9),
                wraplength=340,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(5, 0))
            return

        self._create_progress_row(section, "Ultimas 5 horas", data.get("session"), top_pad=5, emphasis=True)
        weekly_rows = self._weekly_rows(data)
        for index, (label, entry, emphasis) in enumerate(weekly_rows):
            self._create_progress_row(
                section,
                label,
                entry,
                top_pad=6,
                emphasis=emphasis,
            )

    def _fetch_and_update(self) -> dict[str, Any]:
        data = fetch_usage_bundle(self._profiles)
        self._set_data(data)
        return data

    def _same_profile_dir(self, left: Path, right: Path) -> bool:
        try:
            return left.expanduser().resolve() == right.expanduser().resolve()
        except OSError:
            return str(left.expanduser()).lower() == str(right.expanduser()).lower()

    def _profile_status_text(self, profile: ClaudeProfile) -> str:
        if profile.credentials_path.exists():
            return "Listo para consultar el uso"
        return "Hace falta iniciar sesion en esta carpeta"

    def _suggested_claude_dir(self) -> Path:
        return default_profile().config_dir

    def _open_in_explorer(self, target: Path) -> None:
        destination = target.expanduser()
        if not destination.exists():
            destination = destination.parent if destination.parent != destination else Path.home()
        try:
            if sys.platform == "win32":
                os.startfile(str(destination))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(destination)])
            else:
                subprocess.Popen(["xdg-open", str(destination)])
        except Exception:
            messagebox.showerror(
                "Perfiles",
                f"No se pudo abrir la carpeta:\n{destination}",
                parent=self._profiles_window,
            )

    def _refresh_after_profile_change(self) -> None:
        data = fetch_usage_bundle(self._profiles, force=True)
        self._set_data(data)
        if self._popup and self._popup.winfo_exists():
            self._run_on_ui(lambda: self._show_popup(data))

    def _persist_profiles(self) -> None:
        save_profiles(self._profiles)
        self._profiles = load_profiles()

    def _prompt_add_profile(self, name: str, raw_path: str) -> bool:
        profile_name = name.strip()
        config_dir = normalize_config_dir(raw_path.strip())
        if not profile_name:
            messagebox.showerror("Perfil", "Ingresá un nombre visible para el perfil.", parent=self._profiles_window)
            return False
        if config_dir is None:
            messagebox.showerror("Perfil", "Elegí una carpeta valida de Claude.", parent=self._profiles_window)
            return False
        if any(self._same_profile_dir(profile.config_dir, config_dir) for profile in self._profiles):
            messagebox.showinfo("Perfil", "Esa carpeta ya esta agregada.", parent=self._profiles_window)
            return False

        base = default_profile()
        self._profiles.append(
            ClaudeProfile(
                id=f"perfil-{len(self._profiles) + 1}",
                name=profile_name,
                config_dir=config_dir,
                supports_refresh=self._same_profile_dir(config_dir, base.config_dir),
            )
        )
        self._persist_profiles()
        threading.Thread(target=self._refresh_after_profile_change, daemon=True).start()
        return True

    def _delete_profile(self, profile: ClaudeProfile) -> None:
        if profile.id == "principal":
            return
        confirmed = messagebox.askyesno(
            "Eliminar perfil",
            f'Se quitara "{profile.name}" de esta lista.\n\nLa carpeta original no se borra.',
            parent=self._profiles_window,
        )
        if not confirmed:
            return
        self._profiles = [
            item for item in self._profiles if not self._same_profile_dir(item.config_dir, profile.config_dir)
        ]
        self._persist_profiles()
        self._show_profiles_window()
        threading.Thread(target=self._refresh_after_profile_change, daemon=True).start()

    def _show_profiles_window(self) -> None:
        if self._profiles_window and self._profiles_window.winfo_exists():
            self._profiles_window.destroy()

        window = tk.Toplevel(self._root)
        window.title("Perfiles de Claude")
        window.configure(bg=BG)
        window.resizable(False, False)
        window.attributes("-topmost", True)
        self._apply_window_icon(window)
        self._profiles_window = window

        frame = tk.Frame(window, bg=PANEL, padx=14, pady=14)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="Perfiles agregados",
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            frame,
            text="Agrega otros perfiles de Claude para ver su uso en un solo lugar.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        list_frame = tk.Frame(frame, bg=PANEL)
        list_frame.pack(fill="x", pady=(10, 0))

        for profile in self._profiles:
            row = tk.Frame(list_frame, bg=BG, padx=10, pady=7)
            row.pack(fill="x", pady=(0, 6))

            title_row = tk.Frame(row, bg=BG)
            title_row.pack(fill="x")

            title = profile.name
            if profile.id == "principal":
                title = f"{title} (actual)"

            tk.Label(
                title_row,
                text=title,
                bg=BG,
                fg=FG,
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(side="left")

            if profile.id != "principal":
                tk.Button(
                    title_row,
                    text="Eliminar",
                    command=lambda p=profile: self._delete_profile(p),
                    bg=BG,
                    fg=MUTED,
                    activebackground=BG,
                    activeforeground=FG,
                    bd=0,
                    padx=4,
                    pady=0,
                    font=("Segoe UI", 9),
                    highlightthickness=0,
                    relief="flat",
                    cursor="hand2",
                ).pack(side="right")

            tk.Label(
                row,
                text=self._profile_status_text(profile),
                bg=BG,
                fg=ACCENT,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

            tk.Label(
                row,
                text=str(profile.config_dir),
                bg=BG,
                fg=MUTED,
                font=("Segoe UI", 8),
                wraplength=420,
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

        form = tk.Frame(frame, bg=PANEL)
        form.pack(fill="x", pady=(6, 0))

        tk.Label(
            form,
            text="Agregar otro perfil",
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")

        name_var = tk.StringVar()
        path_var = tk.StringVar()

        tk.Label(form, text="Nombre", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(
            fill="x", pady=(10, 0)
        )
        tk.Entry(
            form,
            textvariable=name_var,
            bg=BG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        ).pack(fill="x", ipady=5)

        tk.Label(form, text="Carpeta de Claude", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(
            fill="x", pady=(10, 0)
        )

        tk.Label(
            form,
            text=(
                "Elegí la carpeta donde Claude guarda tu sesion.\n"
                "Suele verse como: C:/Users/TU_USUARIO/.claude  o  \\\\wsl.localhost\\Ubuntu\\home\\TU_USUARIO\\.claude"
            ),
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=420,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        path_row = tk.Frame(form, bg=PANEL)
        path_row.pack(fill="x", pady=(8, 0))

        tk.Entry(
            path_row,
            textvariable=path_var,
            bg=BG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        ).pack(side="left", fill="x", expand=True, ipady=5)

        tk.Button(
            path_row,
            text="Examinar",
            command=lambda: path_var.set(
                filedialog.askdirectory(parent=window, initialdir=str(Path.home())) or path_var.get()
            ),
            bg=BG,
            fg=FG,
            activebackground=BG,
            activeforeground=FG,
            bd=0,
            padx=10,
            pady=0,
            font=("Segoe UI", 9),
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            cursor="hand2",
        ).pack(side="left", padx=(8, 0))

        helpers = tk.Frame(form, bg=PANEL)
        helpers.pack(fill="x", pady=(8, 0))

        tk.Button(
            helpers,
            text="Usar el perfil actual",
            command=lambda: path_var.set(str(self._suggested_claude_dir())),
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=8,
            pady=4,
            font=("Segoe UI", 8),
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            cursor="hand2",
        ).pack(side="left")

        tk.Button(
            helpers,
            text="Abrir carpeta",
            command=lambda: self._open_in_explorer(self._suggested_claude_dir()),
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=8,
            pady=4,
            font=("Segoe UI", 8),
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            cursor="hand2",
        ).pack(side="left", padx=(8, 0))

        actions = tk.Frame(form, bg=PANEL)
        actions.pack(fill="x", pady=(12, 0))

        def submit() -> None:
            if self._prompt_add_profile(name_var.get(), path_var.get()):
                name_var.set("")
                path_var.set("")
                self._show_profiles_window()

        tk.Button(
            actions,
            text="Agregar",
            command=submit,
            bg=ACCENT,
            fg=FG,
            activebackground=ACCENT,
            activeforeground=FG,
            bd=0,
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
        ).pack(side="left")

        tk.Button(
            actions,
            text="Cerrar",
            command=window.destroy,
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=8,
            pady=6,
            font=("Segoe UI", 9),
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = max(40, (window.winfo_screenwidth() - width) // 2)
        y = max(40, (window.winfo_screenheight() - height) // 3)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.focus_force()

    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            self._fetch_and_update()

    def _on_refresh(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        threading.Thread(
            target=lambda: self._set_data(fetch_usage_bundle(self._profiles, force=True)),
            daemon=True,
        ).start()

    def _on_manage_profiles(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._run_on_ui(self._show_profiles_window)

    def _on_toggle_startup(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        _set_startup_enabled(not _is_startup_enabled())
        icon.update_menu()

    def _on_show(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        def work() -> None:
            data = self._fetch_and_update()
            self._run_on_ui(lambda: self._show_popup(data))

        threading.Thread(target=work, daemon=True).start()

    def _show_popup(self, data: dict[str, Any]) -> None:
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()

        popup = tk.Toplevel(self._root)
        popup.title(WINDOW_TITLE)
        popup.configure(bg=BG)
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.overrideredirect(True)
        self._apply_window_icon(popup)
        self._popup = popup

        shell = tk.Frame(popup, bg=BORDER, padx=1, pady=1)
        shell.pack(fill="both", expand=True)

        frame = tk.Frame(shell, bg=PANEL, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=PANEL)
        header.pack(fill="x")

        tk.Label(
            header,
            text="Uso del plan Max (5x)",
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")

        tk.Button(
            header,
            text="×",
            command=popup.destroy,
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=4,
            pady=0,
            font=("Segoe UI", 11, "bold"),
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        profiles = data.get("profiles")
        if isinstance(profiles, list) and profiles:
            for index, item in enumerate(profiles):
                profile = item.get("profile")
                profile_data = item.get("data", {})
                title = profile.name if isinstance(profile, ClaudeProfile) else f"Perfil {index + 1}"
                self._render_profile_section(frame, title, profile_data, top_pad=10 if index == 0 else 12)
        else:
            self._render_profile_section(frame, default_profile().name, data.get("primary", data), top_pad=10)

        footer = tk.Frame(frame, bg=PANEL)
        footer.pack(fill="x", pady=(12, 0))

        tk.Label(
            footer,
            text=_updated_text(self._updated_at),
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left")

        tk.Button(
            footer,
            text="Perfiles",
            command=self._show_profiles_window,
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=4,
            pady=0,
            font=("Segoe UI", 9),
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=(0, 10))

        tk.Button(
            footer,
            text="↻",
            command=lambda: threading.Thread(target=self._refresh_popup, daemon=True).start(),
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=FG,
            bd=0,
            padx=4,
            pady=0,
            font=("Segoe UI", 11, "bold"),
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
        ).pack(side="right")

        popup.update_idletasks()
        w, h = popup.winfo_width(), popup.winfo_height()
        _, _, work_right, work_bottom = _work_area_bounds()
        if work_right == 0 and work_bottom == 0:
            work_right = popup.winfo_screenwidth()
            work_bottom = popup.winfo_screenheight()
        margin_x, margin_y = 26, 12
        x = work_right - w - margin_x
        y = work_bottom - h - margin_y
        popup.geometry(f"{w}x{h}+{x}+{y}")

        popup.bind("<Escape>", lambda _e: popup.destroy())
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        self._bind_popup_dismiss(popup)
        popup.focus_force()

    def _bind_popup_dismiss(self, popup: tk.Toplevel) -> None:
        def is_within(widget: tk.Misc | None, ancestor: tk.Misc) -> bool:
            while widget is not None:
                if widget == ancestor:
                    return True
                widget = widget.master
            return False

        def dismiss() -> None:
            if popup.winfo_exists():
                popup.destroy()

        def on_focus_out(_event: tk.Event | None = None) -> None:
            popup.after(150, check_focus)

        def check_focus() -> None:
            if not popup.winfo_exists():
                return
            focused = popup.focus_get()
            if focused is None or not is_within(focused, popup):
                dismiss()

        def on_global_click(event: tk.Event) -> None:
            if not popup.winfo_exists():
                return
            x1, y1 = popup.winfo_rootx(), popup.winfo_rooty()
            x2, y2 = x1 + popup.winfo_width(), y1 + popup.winfo_height()
            if not (x1 <= event.x_root <= x2 and y1 <= event.y_root <= y2):
                dismiss()

        def cleanup(_event: tk.Event | None = None) -> None:
            try:
                self._root.unbind_all("<Button-1>")
            except tk.TclError:
                pass

        popup.bind("<FocusOut>", on_focus_out, add="+")
        popup.bind("<Destroy>", cleanup, add="+")
        self._root.bind_all("<Button-1>", on_global_click, add="+")

    def _refresh_popup(self) -> None:
        data = fetch_usage_bundle(self._profiles, force=True)
        self._set_data(data)
        self._run_on_ui(lambda: self._show_popup(data))

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop.set()
        icon.stop()
        self._run_on_ui(self._root.quit)

    def run(self) -> None:
        self._fetch_and_update()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._icon.run, daemon=True).start()
        self._root.mainloop()


def main() -> None:
    UsageTrayApp().run()
