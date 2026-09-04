"""Monitor de uso de Claude en la bandeja de Windows."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from tkinter import messagebox
from typing import Any, Callable, Mapping

from PIL import ImageTk

import pystray

from . import (
    alerts,
    formatting,
    icons,
    logging_setup,
    paths,
    platform,
    quotas,
    settings as settings_model,
    ui,
)
from .api import fetch_usage_bundle
from .config import ClaudeProfile, ConfigCorrupted, load_config, save_config
from .ui.widgets import apply_window_icon

POLL_SECONDS = 300

# The notify-icon id to ask the shell about when resolving our tray icon's
# rectangle on Windows. pystray publishes neither the icon id nor the handle
# it belongs to, so both are discovered rather than declared: `0` is the value
# the design-phase spike measured answering on pystray's win32 backend, and
# the seam names the private attribute the handle itself sits on
# (`tray_handle_attribute()`). Inert on every other platform, where a status
# item identifies itself. This is the fragile part of the custom-tooltip path
# by construction -- a pystray upgrade could move either -- which is exactly
# why failing to resolve them degrades to the native tooltip instead of
# raising (tray-tooltip spec, "Native Tooltip Remains the Fallback").
TRAY_ICON_UID = 0

# How many times to ask the OS for our icon's rect before concluding hover is
# not available this session, and how long to leave between tries.
#
# The icon is not necessarily where the OS can measure it the instant it is
# created. Measured on macOS: a fresh `NSStatusItem` reports an off-screen
# placeholder frame for the first ~200ms and only then lands in the menu bar,
# so a single probe at startup loses a race it wins a fifth of a second later.
# Two seconds of budget clears that with room to spare on a loaded machine.
#
# Bounded, because the other reason a rect never resolves -- a Windows icon
# parked in the taskbar overflow -- is permanent, and the user must reach the
# native tooltip promptly rather than wait on retries that cannot succeed.
HOVER_PROBE_ATTEMPTS = 20
HOVER_PROBE_DELAY_S = 0.1

# Spanish labels for the three quota windows an alert can name. Kept as a name
# here because this module's toast has used it since it existed, but it is
# `quotas.py`'s dict now, not a second copy of it: the alerts control in
# `ui/settings_window.py` labels a field per quota window and cannot import
# this module (`ui` is L4, `app` is L5 -- that edge points up), so the one
# thing that had to be true, that a toast and that window call the same quota
# the same thing, is now structural rather than a promise to remember.
ALERT_WINDOW_LABELS = quotas.QUOTA_WINDOW_LABELS

# The explicit AppUserModelID this process declares to Windows. Dotted
# `CompanyName.ProductName` is Microsoft's documented convention for these
# ids; a bare "ClaudeUsage" also works but the dotted form is the correct,
# collision-resistant spelling. Set once at startup so the shell attributes
# tray toasts to this identity rather than to `python.exe`/`claudeusage.exe`.
APP_USER_MODEL_ID = "Anthropic.ClaudeUsage"

# What the "run at login" toggle is called, in the words each OS uses for it.
# Windows says "startup"; macOS calls the same thing a login item, and there
# is no Windows to start with. Branching on the seam's own exported constant
# keeps this the one place the wording is decided -- the menu item and the log
# line below both read it, so they can never disagree.
STARTUP_MENU_LABEL = (
    "Iniciar con Windows" if platform.IS_WINDOWS else "Abrir al iniciar sesion"
)

log = logging.getLogger(__name__)


class UsageTrayApp:
    def __init__(self) -> None:
        config_load = load_config()
        self._config = config_load.config
        self._profiles = self._config.profiles
        self._read_only = config_load.read_only
        # config.py carries the settings blob raw (it and settings.py are
        # both L2, so neither may import the other). This is the composition
        # root: mapping raw <-> Settings is its job, not the store's.
        settings_load = settings_model.load(self._config.settings)
        self._settings = settings_load.settings
        self._theme = settings_model.build_theme(self._settings)
        # Both stores get to report at load. They are separate notice types on
        # purpose -- one means irreplaceable data was quarantined, the other
        # that a hand-typed colour was dropped -- and merging the lists here
        # is what lets `_show_notices` stay the single place that decides how
        # loudly each is said.
        notices: list[Any] = [*config_load.notices, *settings_load.notices]
        log.info(
            "config loaded: %d profile(s), read_only=%s, %d notice(s), theme=%s, font_size=%d, %d custom color(s)",
            len(self._profiles),
            self._read_only,
            len(notices),
            self._settings.theme,
            self._settings.font_size,
            len(self._settings.colors),
        )
        self._alert_state = alerts.prune(
            alerts.load_state(paths.alert_state_file()),
            {profile.id for profile in self._profiles},
        )
        self._data: dict[str, Any] = {
            "primary": {"error": "Cargando…"},
            "profiles": [],
        }
        self._updated_at: datetime | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()
        self._popup: tk.Toplevel | None = None
        # One window, two tabs -- so one reference, plus the two pieces of its
        # render state that have to outlive its rebuilds. The tray menu still
        # offers both entries; each just opens this window on its own tab.
        self._main_window: tk.Toplevel | None = None
        self._active_tab: str = ui.TAB_PROFILES
        # `settings.colors` as of the moment the window was last opened: what
        # the settings tab's per-colour "volver" goes back to. It lives here
        # because every colour change rebuilds that window, so a baseline
        # captured inside it would reset on the first change and leave revert
        # with nothing to return to.
        self._color_baseline: Mapping[str, str] = {}
        self._hover_popup: tk.Toplevel | None = None
        self._hover_tracker: ui.HoverTracker | None = None
        # Set once hover tracking resolves a tray handle; stays `None` on a
        # platform that never resolves one, which is why every reader treats
        # it as optional rather than assuming the tray is introspectable.
        self._tray_rect_of: Callable[[], platform.Rect | None] | None = None
        # Start on the native tooltip and earn the custom one: the tray
        # handle, the icon id and a readable rect are all discovered at
        # runtime, and anything we cannot confirm must leave the user with
        # the tooltip that has always worked.
        self._native_tooltip = True
        # Strong reference to the ObjC object the platform seam installs as
        # the status item button's target (macOS only; `None` everywhere
        # else). AppKit targets are weak references, so dropping this would
        # leave the button firing at a collected object.
        self._click_target: Any = None
        self._root = tk.Tk()
        self._root.withdraw()
        # Route Tk callback exceptions into the log. Tk's default handler
        # prints them to stderr, which under `--windowed` means nowhere.
        self._root.report_callback_exception = logging_setup.tk_exception_handler()
        self._window_icon = ImageTk.PhotoImage(icons.window_icon(self._theme.palette))
        apply_window_icon(self._root, self._window_icon)
        self._dismiss_manager = ui.DismissManager(self._root)
        self._root.after(100, self._process_ui_queue)

        if notices:
            self._run_on_ui(lambda: self._show_notices(notices, read_only=config_load.read_only))

        menu_items: list[Any] = [
            pystray.MenuItem("Ver uso", self._on_show, default=True),
            pystray.MenuItem("Actualizar", self._on_refresh),
            pystray.MenuItem("Perfiles", self._on_manage_profiles),
            pystray.MenuItem("Configuracion", self._on_settings),
        ]
        if platform.startup_supported():
            menu_items.append(
                pystray.MenuItem(
                    STARTUP_MENU_LABEL,
                    self._on_toggle_startup,
                    checked=lambda _item: platform.is_startup_enabled(),
                )
            )
        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self._on_quit),
            ]
        )
        menu = pystray.Menu(*menu_items)
        self._icon = pystray.Icon(
            "claude-usage",
            icons.tray_icon(self._data, self._theme.palette),
            formatting.tooltip(self._data),
            menu,
        )

    def _show_notices(self, notices: list[Any], *, read_only: bool) -> None:
        for notice in notices:
            if isinstance(notice, ConfigCorrupted):
                if notice.quarantined_to is not None:
                    message = (
                        "No se pudo leer el archivo de configuracion. Se guardo una copia en:\n"
                        f"{notice.quarantined_to}\n\n"
                        "Se creo una configuracion nueva; revisa esa copia si queres recuperar "
                        "perfiles anteriores."
                    )
                else:
                    message = (
                        "No se pudo leer el archivo de configuracion y tampoco se pudo resguardar "
                        "de forma segura."
                    )
                if read_only:
                    message += (
                        "\n\nLa aplicacion va a funcionar en modo solo lectura durante esta sesion: "
                        "los cambios en los perfiles no se van a guardar."
                    )
                messagebox.showwarning("Configuracion", message, parent=self._root)
            elif isinstance(notice, settings_model.InvalidColors):
                # Deliberately quieter than the quarantine notice above, and
                # deliberately not silent: nothing was lost, but a colour the
                # user typed by hand did not take, and the only alternative
                # is a window that inexplicably did not change.
                fields = ", ".join(notice.fields)
                messagebox.showinfo(
                    "Configuracion",
                    "No se pudieron usar estos colores personalizados y se dejo el color del tema:\n"
                    f"{fields}\n\n"
                    "Tienen que ser hexadecimales, por ejemplo #1a2b3c.",
                    parent=self._root,
                )

    def _process_ui_queue(self) -> None:
        """Drain queued UI work, then re-arm.

        The re-arm is in a `finally` and each task is individually guarded on
        purpose. Previously an exception in a task escaped this method, so the
        `after()` re-arm never ran and the pump died permanently -- one failing
        task made every later click silently do nothing, which is far worse
        than the original failure and looks nothing like it.
        """
        try:
            while True:
                try:
                    fn = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    log.exception("queued UI task failed")
        finally:
            if not self._stop.is_set():
                self._root.after(100, self._process_ui_queue)

    def _run_on_ui(self, fn) -> None:
        self._ui_queue.put(fn)

    def _apply_to_tray(self, *, image: Any = None, title: str | None = None) -> None:
        """Push a new image and/or tooltip onto the tray icon, from any thread.

        On Windows pystray owns a message loop of its own and takes these
        writes from whatever thread makes them. On macOS the icon is an
        AppKit `NSStatusItem`, and `setImage_`/`setToolTip_` off the main
        thread is undefined behaviour -- it does not fail loudly, it corrupts
        or crashes eventually, which for a process that repaints every poll
        means a tray app that dies overnight for no visible reason. So there
        the write hops onto the existing UI queue, the same one the hover
        callbacks already use.

        The caller renders the image on its own thread and passes it in:
        `icons.tray_icon` is pure Pillow and has no reason to occupy the main
        thread, and keeping the queued task down to two attribute writes is
        what stops a slow repaint from stuttering the UI.
        """

        def apply() -> None:
            if image is not None:
                self._icon.icon = image
            if title is not None:
                self._icon.title = title

        if (
            not platform.tray_requires_host_event_loop()
            or threading.current_thread() is threading.main_thread()
        ):
            apply()
        else:
            self._run_on_ui(apply)

    def _set_data(self, data: dict[str, Any]) -> None:
        primary = data.get("primary", data)
        with self._lock:
            self._data = data
            fetched_at = formatting.parse_meta_datetime(primary.get("meta", {}).get("fetched_at"))
            self._updated_at = fetched_at or datetime.now()
        # In custom-popup mode `title` is left empty and stays that way: a
        # non-empty `szTip` makes the shell draw its own tooltip, which would
        # sit next to ours saying the same thing. The popup reads
        # `self._data` when it opens, so it needs no push here.
        self._apply_to_tray(
            image=icons.tray_icon(data, self._theme.palette),
            title=formatting.tooltip(data) if self._native_tooltip else None,
        )
        self._process_alerts(data)

    def _process_alerts(self, data: dict[str, Any]) -> None:
        """Run the poll through the alert state machine and deliver events.

        Runs on whichever thread fetched -- the poll loop or a refresh
        thread -- so the state swap is under the same lock the data swap
        uses. Notification delivery stays outside it: `icon.notify` is a
        cross-thread shell call that has no business holding a lock.
        """
        items: list[tuple[str, dict[str, Any]]] = []
        names: dict[str, str] = {}
        for entry in data.get("profiles") or []:
            profile = entry.get("profile")
            if not isinstance(profile, ClaudeProfile):
                continue
            items.append((profile.id, entry.get("data") or {}))
            names[profile.id] = profile.name
        if not items:
            return

        # Resolved here rather than inside `alerts`: the state machine stays
        # ignorant of the settings model (they are both L2 and cannot import
        # each other), and this is the composition root's job either way.
        alert_settings = self._settings.alerts
        # Resolved to a complete (profile x window) map here rather than left
        # to `evaluate`'s own inheritance: this is the composition root, it is
        # the only place that holds an `AlertSettings`, and `thresholds_for`
        # is the one implementation of "override, else global" either side
        # should be running. `evaluate` keeps its own fallback for callers
        # (and tests) that hand it a partial map.
        thresholds_by_profile = {
            profile_id: {
                window: alert_settings.thresholds_for(profile_id, window)
                for window in quotas.QUOTA_WINDOWS
            }
            for profile_id, _ in items
        }

        # Inject the clock here rather than let `alerts` read one: the state
        # machine stays pure and deterministic under test, and the composition
        # root is the only place a wall clock belongs. The cooldown suppresses
        # a repeat of the SAME (profile, window) within this interval.
        now = datetime.now(timezone.utc)
        cooldown = timedelta(minutes=alert_settings.min_interval_minutes)
        with self._lock:
            state = self._alert_state
            new_state, events = alerts.evaluate(
                state,
                items,
                alert_settings.thresholds,
                thresholds_by_profile=thresholds_by_profile,
                now=now,
                cooldown=cooldown,
            )
            changed = new_state != state
            self._alert_state = new_state

        if changed:
            # Write only when something actually changed: in steady state
            # almost no poll changes anything, so this turns ~288 writes/day
            # into the handful that reflect a real crossing or rollover.
            try:
                alerts.save_state(paths.alert_state_file(), new_state)
            except OSError:
                # Losing this file costs at most one duplicate notification.
                # It is never worth taking the app down for.
                log.exception("could not persist alert state; continuing")

        if not self._settings.alerts.enabled:
            # evaluate() still ran, so the state stays warm and re-enabling
            # alerts cannot produce a burst of stale ones.
            return
        for event in events:
            self._notify_alert(event, names.get(event.profile_id, "Claude"))

    def _notify_alert(self, event: alerts.AlertEvent, profile_name: str) -> None:
        label = ALERT_WINDOW_LABELS.get(event.window, event.window)
        try:
            self._icon.notify(
                f"{profile_name}: {label} alcanzo el {event.threshold}% ({event.pct:.0f}% usado)",
                "Uso Claude",
            )
        except Exception:
            # A backend that cannot toast must not kill the poll thread --
            # the tray icon and popup still carry the same information.
            log.exception("could not deliver alert notification")

    def _fetch_and_update(self) -> dict[str, Any]:
        log.info("fetching usage for %d profile(s)", len(self._profiles))
        data = fetch_usage_bundle(self._profiles)
        primary_error = data.get("primary", {}).get("error")
        if primary_error:
            log.warning("primary profile fetch reported: %s", primary_error)
        self._set_data(data)
        return data

    def _refresh_after_profile_change(self) -> None:
        data = fetch_usage_bundle(self._profiles, force=True)
        self._set_data(data)
        if self._popup and self._popup.winfo_exists():
            self._run_on_ui(lambda: self._show_popup(data))

    def _persist_config(self) -> None:
        if self._read_only:
            return
        try:
            save_config(self._config)
        except OSError:
            log.exception("could not persist config")

    def _on_profiles_changed(self, new_profiles: list[ClaudeProfile]) -> None:
        previous = {profile.id: profile.config_dir for profile in self._profiles}
        # A deleted profile's threshold override is the other half of the
        # footprint `alerts.prune` clears below: without this, `config.json`
        # keeps tier lists keyed by uuids nothing can reach again.
        self._settings = settings_model.prune_profile_thresholds(
            self._settings, {profile.id for profile in new_profiles}
        )
        # `replace` rather than a fresh AppConfig: the settings blob rides
        # along, so a profile edit can never silently reset the user's theme
        # (user-settings spec, "Settings survive independently of profile
        # edits"). It is re-serialized rather than passed through because the
        # prune above may have just changed it.
        self._config = replace(
            self._config,
            profiles=new_profiles,
            settings=settings_model.to_raw(self._settings),
        )
        self._profiles = new_profiles
        self._persist_config()

        with self._lock:
            state = alerts.prune(self._alert_state, {profile.id for profile in new_profiles})
            for profile in new_profiles:
                # A repointed path is a different account: drop its arm state
                # rather than trusting the new account's resets_at to differ.
                # Weekly windows can legitimately coincide.
                if profile.id in previous and previous[profile.id] != profile.config_dir:
                    state = alerts.invalidate_profile(state, profile.id)
            changed = state != self._alert_state
            self._alert_state = state
        if changed:
            try:
                alerts.save_state(paths.alert_state_file(), state)
            except OSError:
                log.exception("could not persist alert state after a profile change")

        threading.Thread(target=self._refresh_after_profile_change, daemon=True).start()

    def _on_settings_changed(self, new_settings: settings_model.Settings) -> None:
        log.info("settings changed: theme=%s, font_size=%d", new_settings.theme, new_settings.font_size)
        self._settings = new_settings
        self._theme = settings_model.build_theme(new_settings)
        self._config = replace(self._config, settings=settings_model.to_raw(new_settings))
        self._persist_config()

        # Rebuild the theme-dependent images. Safe here: this runs on the Tk
        # main thread (it is a widget command), and ImageTk.PhotoImage must
        # not be constructed off it.
        self._window_icon = ImageTk.PhotoImage(icons.window_icon(self._theme.palette))
        apply_window_icon(self._root, self._window_icon)
        with self._lock:
            data = self._data
        self._apply_to_tray(image=icons.tray_icon(data, self._theme.palette))

        # Re-show in the new theme, on whatever tab the user is looking at.
        # This is the settings tab's rerender: it cannot do its own, because
        # the reply to a settings change is a different Theme and only this
        # method can build one. The popup picks the theme up on its next show,
        # which rebuilds it from scratch anyway.
        self._show_main_window()

    def _on_main_window_resized(self, window_size: str) -> None:
        self._settings = settings_model.with_window_size(self._settings, window_size)
        self._config = replace(self._config, settings=settings_model.to_raw(self._settings))
        self._persist_config()

    def _on_tab_changed(self, tab: str) -> None:
        # The window rerenders itself on a tab switch; this is only so that a
        # *settings*-driven rebuild afterwards re-shows the same tab instead
        # of snapping back to the one the window was opened on.
        self._active_tab = tab

    def _show_main_window(self, tab: str | None = None) -> None:
        """Show the managed window, on `tab` if the caller named one.

        `tab=None` means "rebuild where we are" -- a theme change re-showing
        the window the user is already looking at. A named tab means the tray
        menu asked for it, which is a fresh visit: that is what re-arms the
        colour baseline, so "volver al anterior color" means "the colour I
        walked in with" rather than "the colour I had eight windows ago".
        """
        if tab is not None:
            self._active_tab = tab
            self._color_baseline = dict(self._settings.colors)
        log.info("opening the main window on the %s tab", self._active_tab)
        self._main_window = ui.show_main_window(
            self._root,
            self._profiles,
            self._settings,
            self._on_profiles_changed,
            self._on_settings_changed,
            tab=self._active_tab,
            theme=self._theme,
            existing=self._main_window,
            window_icon=self._window_icon,
            window_size=self._settings.window_size,
            on_window_size_changed=self._on_main_window_resized,
            on_tab_changed=self._on_tab_changed,
            color_baseline=self._color_baseline,
        )

    # -- tray hover popup ---------------------------------------------------

    def _start_hover_tracking(self, attempt: int = 1) -> None:
        """Promote to the custom hover popup, but only on evidence.

        Three things must hold, and each is checked rather than assumed: the
        platform can report a tray rect at all, pystray still keeps its tray
        handle where the seam says to look, and the OS actually answers for
        our icon *right now*. Any of them missing leaves `_native_tooltip`
        true and the app behaving exactly as it did before this existed.

        Must run after the icon's native object exists. On Windows that means
        after `icon.run()` -- `_hwnd` is assigned inside pystray's `_run()`.
        On macOS the `NSStatusItem` exists from construction, but reading its
        window's frame is an AppKit call, so `_on_icon_ready` queues this onto
        the UI loop there instead of calling it on pystray's setup thread.
        """
        if not platform.tray_hover_supported():
            log.info("tray hover not supported on this platform; keeping the native tooltip")
            return

        attribute = platform.tray_handle_attribute()
        if attribute is None:
            log.info("no tray handle to resolve on this platform; keeping the native tooltip")
            return

        native = getattr(self._icon, attribute, None)
        # Falsy rather than `is None`: a zero `HWND` is as unusable as a
        # missing attribute, and an `NSStatusItem` is always truthy.
        if not native:
            log.warning("could not read pystray's %r; keeping the native tooltip", attribute)
            return

        handle = platform.TrayHandle(native=native, uid=TRAY_ICON_UID)
        rect = platform.tray_icon_rect(handle)
        if rect is None:
            if attempt < HOVER_PROBE_ATTEMPTS:
                # Not necessarily a no: the icon may simply not be laid out
                # yet. The retry is a *timed* one -- putting it straight back
                # on the UI queue would burn the whole budget inside a single
                # pump, which drains everything queued in one pass and would
                # spend twenty attempts in under a millisecond.
                log.debug(
                    "no rect for our tray icon yet (attempt %s/%s)",
                    attempt,
                    HOVER_PROBE_ATTEMPTS,
                )
                self._retry_hover_probe(attempt + 1)
                return
            log.warning(
                "the OS reported no rect for our tray icon after %s attempts (%s uid=%s); "
                "keeping the native tooltip",
                HOVER_PROBE_ATTEMPTS,
                attribute,
                TRAY_ICON_UID,
            )
            return

        self._native_tooltip = False
        self._apply_to_tray(title="")
        # Held on the instance, not just closed over by the tracker: the
        # usage popup needs the same rectangle to know which display to open
        # on, and it is opened by a click, nowhere near the hover path.
        def rect_of() -> platform.Rect | None:
            return platform.tray_icon_rect(handle)

        self._tray_rect_of = rect_of
        self._hover_tracker = ui.HoverTracker(
            # Re-read on every tick, never cached: the icon moves whenever a
            # neighbour appears or disappears in the menu bar / tray.
            rect_of=rect_of,
            cursor_of=platform.cursor_position,
            # Every callback hops to the Tk main thread through the queue that
            # already exists: on Windows these fire on the tracker's polling
            # thread, and widget work off the main thread is exactly the bug
            # this app's `_ui_queue` was built to prevent. On macOS the
            # tracker is already on that thread and the hop is redundant, but
            # it is also harmless -- and one dispatch path is one thing to
            # reason about instead of two.
            on_enter=lambda rect: self._run_on_ui(lambda: self._show_hover_popup(rect)),
            on_leave=lambda: self._run_on_ui(self._hide_hover_popup),
            on_unavailable=lambda: self._run_on_ui(
                lambda: self._fall_back_to_native_tooltip("the tray rect stopped resolving")
            ),
            # Where the tray belongs to the host toolkit's loop, so does
            # reading its geometry: `tray_icon_rect` touches live AppKit
            # state on macOS, and a 30ms poll doing that from a daemon thread
            # is the same undefined behaviour `_apply_to_tray` already routes
            # around. `after()` on the Tk root *is* that loop.
            scheduler=self._schedule_on_ui_loop
            if platform.tray_requires_host_event_loop()
            else None,
        )
        self._hover_tracker.start()
        # The rect goes in the log because it is the one number that explains
        # a popup nobody can make appear: a y far from the tray's own edge
        # means the platform's coordinate flip is wrong, and no amount of
        # staring at the hover logic would show that.
        log.info(
            "custom hover popup active (%s uid=%s rect=%s,%s %sx%s)",
            attribute,
            TRAY_ICON_UID,
            rect.left,
            rect.top,
            rect.width,
            rect.height,
        )

    def _start_hover_tracking_guarded(self, attempt: int = 1) -> None:
        """`_start_hover_tracking`, with the enhancement's failure contract.

        Hover is an enhancement, and losing it must never cost the user the
        tray icon itself. The native tooltip survives any failure here
        because `_native_tooltip` only flips once the promotion has fully
        succeeded.
        """
        try:
            self._start_hover_tracking(attempt)
        except Exception:
            log.exception("could not start hover tracking; keeping the native tooltip")

    def _retry_hover_probe(self, attempt: int) -> None:
        """Ask again for the tray rect, `HOVER_PROBE_DELAY_S` from now.

        Two hops on purpose. `after()` is a Tk call and may only be made from
        the main thread, but the first probe runs wherever `_on_icon_ready`
        put it -- pystray's setup thread on Windows. The queue hop is what
        makes the timer request legal from there; the timer is what makes the
        retry actually wait.
        """
        self._run_on_ui(
            lambda: self._schedule_on_ui_loop(
                HOVER_PROBE_DELAY_S,
                lambda: self._start_hover_tracking_guarded(attempt),
            )
        )

    def _schedule_on_ui_loop(self, delay: float, fn) -> None:
        """Run `fn` on the Tk main loop after `delay` seconds.

        The `HoverTracker`'s alternative to owning a thread. Guarded because
        `after` raises once the interpreter is tearing the root down, and a
        tray app on its way out has nothing to gain from a traceback.
        """
        try:
            self._root.after(max(1, int(delay * 1000)), fn)
        except tk.TclError:
            log.debug("could not schedule the hover poll; the root is gone")

    def _fall_back_to_native_tooltip(self, reason: str) -> None:
        """Give up on the custom popup for the rest of the session.

        One-way on purpose: flapping between two tooltips as the shell's
        answer comes and goes would be worse than either, and the native path
        can always show something. Idempotent, because both the tracker and a
        failed render can call it.
        """
        if self._native_tooltip:
            return
        log.warning("falling back to the native tooltip: %s", reason)
        self._native_tooltip = True
        if self._hover_tracker is not None:
            self._hover_tracker.stop()
            self._hover_tracker = None
        self._hide_hover_popup()
        with self._lock:
            data = self._data
        self._apply_to_tray(title=formatting.tooltip(data))

    def _show_hover_popup(self, rect: platform.Rect) -> None:
        with self._lock:
            data = self._data
        try:
            self._hover_popup = ui.show_hover_popup(
                self._root,
                data,
                rect,
                theme=self._theme,
                existing=self._hover_popup,
                work_area=platform.work_area_for_rect(rect),
            )
        except Exception:
            # A popup that cannot be built must not leave the user hovering
            # over an icon that says nothing at all -- `title` is currently
            # empty precisely because this window was supposed to work.
            log.exception("could not build the hover popup")
            self._fall_back_to_native_tooltip("the hover popup could not be built")

    def _hide_hover_popup(self) -> None:
        popup, self._hover_popup = self._hover_popup, None
        try:
            ui.hide_hover_popup(popup)
        except Exception:
            log.exception("could not hide the hover popup")

    def _tray_icon_rect(self) -> platform.Rect | None:
        """The tray icon's current rectangle, or `None` if it cannot be read.

        Only ever used to choose which display a window opens on, so every
        failure answers `None` and the caller falls back to the primary
        display. Reading it touches live AppKit state on macOS and therefore
        must happen on the UI thread -- which is where `_show_popup` already
        runs, since it is dispatched through `_run_on_ui`.
        """
        rect_of = self._tray_rect_of
        if rect_of is None:
            return None
        try:
            return rect_of()
        except Exception:
            log.debug("could not read the tray icon rect", exc_info=True)
            return None

    def _show_popup(self, data: dict[str, Any]) -> None:
        log.info("opening usage popup")
        self._popup = ui.show_popup(
            self._root,
            data,
            self._updated_at,
            theme=self._theme,
            existing=self._popup,
            window_icon=self._window_icon,
            dismiss_manager=self._dismiss_manager,
            tray_rect=self._tray_icon_rect(),
            on_refresh=lambda: threading.Thread(target=self._refresh_popup, daemon=True).start(),
            on_manage_profiles=lambda: self._show_main_window(ui.TAB_PROFILES),
        )

    def _refresh_popup(self) -> None:
        data = fetch_usage_bundle(self._profiles, force=True)
        self._set_data(data)
        self._run_on_ui(lambda: self._show_popup(data))

    def _poll_loop(self) -> None:
        # A transient fetch failure must not end periodic polling for the rest
        # of the process's life.
        while not self._stop.wait(POLL_SECONDS):
            try:
                self._fetch_and_update()
            except Exception:
                log.exception("scheduled poll failed; continuing")

    def _on_refresh(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: Actualizar")
        threading.Thread(
            target=lambda: self._set_data(fetch_usage_bundle(self._profiles, force=True)),
            daemon=True,
        ).start()

    def _on_manage_profiles(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: Perfiles")
        self._run_on_ui(lambda: self._show_main_window(ui.TAB_PROFILES))

    def _on_settings(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: Configuracion")
        self._run_on_ui(lambda: self._show_main_window(ui.TAB_SETTINGS))

    def _on_toggle_startup(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: %s", STARTUP_MENU_LABEL)
        platform.set_startup_enabled(not platform.is_startup_enabled(), paths.startup_command())
        icon.update_menu()

    def _on_show(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: Ver uso")

        def work() -> None:
            data = self._fetch_and_update()
            self._run_on_ui(lambda: self._show_popup(data))

        threading.Thread(target=work, daemon=True).start()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.info("menu: Salir")
        self._stop.set()
        if self._hover_tracker is not None:
            self._hover_tracker.stop()
        icon.stop()
        self._run_on_ui(self._root.quit)

    def _bind_tray_hidpi(self) -> None:
        """Ask the platform to rasterise the tray icon at the display's real
        pixel density, then force one repaint so the sharper image lands.

        A no-op returning `False` everywhere but macOS. Ordered before
        `_bind_primary_click` deliberately: that method detaches the
        `NSMenu` and rewires the status item button's target/action, while
        this one only ever touches the button's *image*, so the two are
        independent -- but doing the image first means a failure here cannot
        leave the click rewiring half-applied.

        The repaint goes through the public `icon` property rather than
        pystray's `_update_icon()`: the setter invalidates the cached
        `NSImage` and re-asserts it, which is exactly what is needed and is
        the one part of this path that is documented API.
        """
        try:
            installed = platform.bind_tray_hidpi_image(self._icon)
        except Exception:
            log.exception("could not install high-DPI tray rendering; keeping the default")
            return
        if not installed:
            return
        try:
            self._icon.icon = self._icon.icon
        except Exception:
            log.exception("high-DPI tray rendering installed but the repaint failed")
            return
        log.info("tray icon renders at the display's backing scale factor")

    def _bind_primary_click(self) -> None:
        """Ask the platform to send a plain click straight to "Ver uso".

        On Windows this is a no-op -- the menu item's `default=True` already
        does it. On macOS it is the difference between the icon being useful
        and the icon only ever opening a menu, because AppKit gives every
        click to an attached `NSMenu` and never calls the button's action.

        `_status_item` is pystray's private attribute, discovered rather than
        published -- the same bargain `_hwnd` strikes for the hover popup, and
        handled the same way: failure costs the shortcut, never the icon. The
        seam leaves the menu attached if it cannot rewire, so the fallback is
        the perfectly usable "click opens the menu, pick Ver uso".
        """
        status_item = getattr(self._icon, "_status_item", None)
        if status_item is None:
            return
        try:
            self._click_target = platform.bind_tray_click(
                status_item,
                lambda: self._on_show(self._icon, None),
            )
        except Exception:
            log.exception("could not bind the primary click; the menu still opens on click")
            return
        if self._click_target is not None:
            log.info("primary click opens the usage popup; secondary opens the menu")

    def _on_icon_ready(self, icon: pystray.Icon) -> None:
        """Run the first fetch once the tray icon's window actually exists.

        This must not happen before `icon.run()`. pystray's win32 backend
        assigns `_hwnd` inside `_run()`, and `Icon.notify()` sends
        `Shell_NotifyIcon` with that handle -- which is declared `BOOL` with
        no errcheck, so notifying too early fails *silently*. The first poll
        is exactly when a quota is most likely to already sit above a
        threshold, and the alert state machine would record that tier as
        notified while the toast went nowhere: a permanently missed alert,
        not a delayed one. pystray's setup thread blocks on an internal queue
        until `_mark_ready()`, which the backend calls only after the window
        is created, so this callback is race-free by construction.

        `visible` must be set explicitly: pystray only does it for you in its
        own default setup, which passing this callback replaces.
        """
        icon.visible = True
        # AppKit work, so it has to land on the main thread -- this callback
        # runs on pystray's setup thread. Queued rather than called: by the
        # time the queue drains, `icon.run_detached()` has long since built
        # the menu this rewiring needs to find.
        self._run_on_ui(self._bind_tray_hidpi)
        self._run_on_ui(self._bind_primary_click)
        # Same reason as the two above where the tray belongs to the host
        # loop: resolving the icon's rect reads AppKit state, and this
        # callback is on pystray's setup thread. Queued, it also lands after
        # the status item has been laid out, which a call made here would
        # race against.
        if platform.tray_requires_host_event_loop():
            self._run_on_ui(self._start_hover_tracking_guarded)
        else:
            self._start_hover_tracking_guarded()
        try:
            self._fetch_and_update()
        except Exception:
            # A failed first fetch must not stop the tray from appearing --
            # without the menu there is no way to reach Perfiles and fix a
            # bad config dir.
            log.exception("initial fetch failed; continuing")

    def run(self) -> None:
        log.info("starting tray app")
        threading.Thread(target=self._poll_loop, daemon=True).start()
        if platform.tray_requires_host_event_loop():
            # macOS: there is exactly one NSApplication run loop in the
            # process, and `Tk.mainloop()` below is already it -- Aqua Tk is
            # a Cocoa app. `run()` would try to start a second one on a
            # background thread, which is both wrong (AppKit is main-thread
            # only) and pointless. `run_detached()` is pystray's supported
            # answer: it readies the icon and returns, leaving the clicks to
            # be dispatched by whatever loop the host toolkit runs.
            #
            # `visible` is set here rather than left to the setup callback:
            # the callback runs on a worker thread, and showing the status
            # item is an AppKit call. Doing it on this thread, before the
            # loop starts, is both safe and one less thing for `_on_icon_ready`
            # to get wrong. The setter is idempotent, so the callback's own
            # assignment becomes a no-op.
            self._icon.visible = True
            self._icon.run_detached(setup=self._on_icon_ready)
            log.info("tray icon attached to the host event loop (run_detached)")
        else:
            threading.Thread(
                target=lambda: self._icon.run(setup=self._on_icon_ready), daemon=True
            ).start()
        log.info("entering mainloop")
        self._root.mainloop()
        log.info("mainloop exited")


def main() -> None:
    logging_setup.setup()
    # Declare our app identity before the tray icon exists, so Windows can
    # attribute toasts to it. A no-op off Windows; guarded because a cosmetic
    # attribution must never block startup.
    try:
        platform.set_app_user_model_id(APP_USER_MODEL_ID)
    except Exception:
        log.exception("could not set AppUserModelID; continuing")
    try:
        UsageTrayApp().run()
    except Exception:
        # Startup failures happen before any window exists, so there is
        # nothing to show a dialog on -- the log is the only record.
        log.exception("fatal error during startup")
        raise
