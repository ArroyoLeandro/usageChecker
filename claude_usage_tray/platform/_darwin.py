"""macOS backend for the platform seam.

Selected by `platform/__init__.py` only when running on macOS.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from ._types import FileManagerError, PlatformUnsupportedError, Rect, TrayHandle, WorkArea

log = logging.getLogger(__name__)


def set_app_user_model_id(app_id: str) -> None:
    # No AppUserModelID concept on macOS; explicit, documented no-op.
    return None


def subprocess_flags() -> int:
    return 0


def work_area_bounds() -> WorkArea | None:
    """The main screen's usable rectangle, in Tk's coordinate space.

    `NSScreen.visibleFrame()` is exactly the desktop minus the menu bar and
    the Dock -- the macOS equivalent of `SPI_GETWORKAREA` -- but it is
    expressed in Cocoa coordinates, whose origin is the *bottom* left of the
    main screen and whose y grows upward. Tk's origin is the top left with y
    growing downward, so the two y values have to be flipped against the full
    frame's height; x needs no adjustment. Getting this wrong does not throw,
    it just puts windows off-screen, which is why the flip is spelled out
    here once instead of at each call site.

    AppKit is imported inside the function on purpose: `paths.py` (L1) pulls
    this seam in, and a module-scope pyobjc import would drag a GUI framework
    into every headless import of the package, tests included. Any failure --
    pyobjc absent, no attached screen -- degrades to the documented `None`
    rather than raising, because a caller that can handle "I don't know" for
    Windows can handle it here.
    """
    try:
        from AppKit import NSScreen
    except Exception:
        return None

    screen = NSScreen.mainScreen()
    if screen is None:
        return None

    visible = screen.visibleFrame()
    screen_height = _cocoa_global_height()
    if screen_height is None:
        return None

    left = int(visible.origin.x)
    right = int(visible.origin.x + visible.size.width)
    # Flip: Cocoa's visible.origin.y measures up from the bottom, so the
    # rect's *top* in Tk terms is whatever is left above it.
    top = int(screen_height - (visible.origin.y + visible.size.height))
    bottom = int(screen_height - visible.origin.y)
    return WorkArea(left=left, top=top, right=right, bottom=bottom)


def _cocoa_global_height() -> float | None:
    """The height every Cocoa y in this process must be flipped against.

    Cocoa has a single global coordinate space whose origin is the bottom
    left of the *primary* display -- `NSScreen.screens()[0]`, the one with
    the menu bar -- and every screen's frame is expressed inside it. Tk's
    origin is the top left of that same primary display with y growing down,
    so converting between them is `y_tk = primary_height - y_cocoa` no
    matter which monitor the point is on.

    Deliberately not `NSScreen.mainScreen()`, which is the screen holding the
    key window and therefore moves as the user does. Flipping a secondary
    monitor's coordinates against its own height silently misplaces windows
    by the difference between the two monitors' heights, which is exactly
    zero while you are testing on one screen.
    """
    try:
        from AppKit import NSScreen
    except Exception:
        return None

    screens = NSScreen.screens()
    if not screens:
        return None
    return screens[0].frame().size.height


def work_area_for_rect(rect: Rect) -> WorkArea | None:
    """`visibleFrame` of the NSScreen containing `rect`, in Tk coordinates.

    `rect` arrives in Tk's space (top-left origin, y down) because that is
    what `tray_icon_rect()` promises, so it is flipped back into Cocoa's
    space to be tested against the screen frames, and the answer is flipped
    forward again. Both flips go against the primary display's height -- see
    `_cocoa_global_height()`; using the containing screen's own height here
    is the subtle way to get this wrong on monitors of different sizes.

    The rect's centre is the test point, not a corner: a menu-bar icon sits
    flush against the top edge of its screen, and a corner lands on the
    boundary where a half-open containment test can fall through to the
    neighbouring display.
    """
    try:
        from AppKit import NSScreen
    except Exception:
        return None

    flip = _cocoa_global_height()
    if flip is None:
        return None

    centre_x = rect.left + rect.width / 2
    centre_y = flip - (rect.top + rect.height / 2)

    for screen in NSScreen.screens():
        frame = screen.frame()
        if not (frame.origin.x <= centre_x < frame.origin.x + frame.size.width):
            continue
        if not (frame.origin.y <= centre_y < frame.origin.y + frame.size.height):
            continue
        visible = screen.visibleFrame()
        return WorkArea(
            left=int(visible.origin.x),
            top=int(flip - (visible.origin.y + visible.size.height)),
            right=int(visible.origin.x + visible.size.width),
            bottom=int(flip - visible.origin.y),
        )

    # No screen owns the point -- a monitor unplugged since the rect was
    # read, most likely. The primary display's work area is wrong but on
    # screen, which beats placing the window nowhere.
    return work_area_bounds()


def open_in_file_manager(path: Path) -> None:
    try:
        subprocess.Popen(["open", str(path)])
    except OSError as exc:
        raise FileManagerError(f"Could not open {path} in Finder") from exc


#: The LaunchAgent's job label, and the basename of its plist. Reverse-DNS by
#: convention, and stable: `launchctl` keys the loaded job off this string, so
#: changing it would strand the previously registered job under the old name.
LAUNCH_AGENT_LABEL = "com.anthropic.claudeusage"


def _launch_agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def startup_supported() -> bool:
    return True


def is_startup_enabled() -> bool:
    """Whether our LaunchAgent plist is installed.

    The file's presence is the answer, rather than parsing `launchctl print`:
    a plist in `~/Library/LaunchAgents` with `RunAtLoad` is by definition what
    starts the app at the next login, which is the question being asked. A job
    booted out for the current session but still on disk will run again next
    login, so reporting it as enabled is the truthful answer.
    """
    return _launch_agent_plist().is_file()


def set_startup_enabled(enabled: bool, command: str) -> None:
    """Install or remove the "open at login" LaunchAgent.

    `command` arrives as a shell-style line (it has to be one on Windows,
    where the registry stores exactly that), but `launchctl` wants an argv
    array -- so it is split with `shlex`, which understands the quoting
    `paths.startup_command` puts in. Quoting matters here: the frozen app's
    path runs through `/Users/<name>/Applications`, and an unsplit or
    naively-split path breaks on the first space.

    Writing the plist is what makes this take effect at the *next* login;
    the `launchctl bootstrap` call is what makes it true for the session
    already running. The preceding `bootout` clears any job left from an
    earlier install -- `bootstrap` fails outright on a label already loaded --
    and its own failure is expected and ignored, since "nothing was loaded"
    is the normal case.
    """
    plist_path = _launch_agent_plist()
    domain = f"gui/{os.getuid()}"

    if not enabled:
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True,
        )
        plist_path.unlink(missing_ok=True)
        return

    argv = shlex.split(command)
    if not argv:
        raise PlatformUnsupportedError(f"Cannot register an empty launch command: {command!r}")

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": LAUNCH_AGENT_LABEL,
                "ProgramArguments": argv,
                "RunAtLoad": True,
                # Not a daemon: if the user quits from the menu, it stays
                # quit until the next login. `KeepAlive` would resurrect it
                # immediately and make "Salir" look broken.
                "KeepAlive": False,
                "ProcessType": "Interactive",
            },
            handle,
        )

    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # The plist is on disk, so the next login is already covered; only
        # loading it *now* failed. Say so rather than pretending either full
        # success or total failure -- a silent no-op is the one outcome the
        # seam's contract forbids.
        raise PlatformUnsupportedError(
            "The login item was installed but could not be started for this "
            f"session: {result.stderr.strip() or result.returncode}"
        )


def app_data_root() -> Path:
    return Path.home() / "Library" / "Application Support"


def tray_hover_supported() -> bool:
    # There is no `Shell_NotifyIconGetRect` here, but there does not need to
    # be: every status item owns an `NSStatusBarWindow`, and a window knows
    # its own frame. See `tray_icon_rect` for the measurement.
    return True


def tray_handle_attribute() -> str | None:
    # pystray's macOS backend builds the `NSStatusItem` in `Icon.__init__`
    # and keeps it on `_status_item`. Private, hence discovered rather than
    # declared -- the same bargain `_hwnd` strikes on Windows.
    return "_status_item"


def _cocoa_flip_height() -> float | None:
    """El alto contra el que se invierte la `y` de Cocoa, o `None`.

    Cocoa mide la `y` hacia arriba desde el borde INFERIOR de la pantalla
    principal; Tk la mide hacia abajo desde el borde SUPERIOR de esa misma
    pantalla. Las dos son coordenadas globales, así que la conversión es
    `y_tk = alto_principal - y_cocoa` para cualquier punto, esté en la
    pantalla que esté.

    Multi-monitor: la tentación es invertir contra la pantalla que contiene
    el icono, y es un error. En un escritorio de varias pantallas hay UN solo
    espacio global de coordenadas y su ancla es la pantalla principal; las
    demás viven en `y` positiva o negativa respecto de ella. Invertir contra
    el alto de un monitor secundario daría un desplazamiento igual a la
    diferencia de alturas, que es cero en el caso de dos monitores iguales
    (por eso el error sobrevive a una prueba casual) y decenas de píxeles en
    cuanto no lo son.

    La pantalla principal se busca por su definición -- la que está en el
    origen -- en vez de confiar en `screens()[0]` a ciegas. Ambas cosas
    coinciden en la práctica, pero la primera es verificable acá y la segunda
    es una promesa de la documentación de AppKit; si algún día no coinciden,
    el origen es el que manda para esta cuenta. `mainScreen()` no sirve: es
    la pantalla de la ventana activa, no la del origen.
    """
    try:
        from AppKit import NSScreen

        screens = NSScreen.screens()
    except Exception:
        return None
    if not screens:
        return None
    for screen in screens:
        frame = screen.frame()
        if frame.origin.x == 0.0 and frame.origin.y == 0.0:
            return float(frame.size.height)
    return float(screens[0].frame().size.height)


def _is_on_a_screen(origin_x: float, origin_y: float, width: float, height: float) -> bool:
    """Si ese rectángulo de Cocoa cae sobre alguna pantalla conectada.

    Un `NSStatusItem` recién creado tiene botón y window desde el principio,
    pero hasta que AppKit lo acomoda en la barra su frame es un placeholder
    fuera de pantalla (medido: `origin.y = -33`, o sea justo debajo del borde
    inferior). Ese frame se ve perfectamente sano -- ancho y alto positivos,
    `isVisible` en `True` -- y sin este control el arranque podría anclar el
    tooltip a un lugar donde no hay ningún icono, que es peor que no tener
    tooltip propio: el usuario ve una ventana aparecer sola en un rincón.

    Se prueba contra todas las pantallas, no contra la principal, porque la
    barra de menú puede estar en un monitor secundario.
    """
    try:
        from AppKit import NSScreen

        screens = NSScreen.screens()
    except Exception:
        return False
    right = origin_x + width
    top = origin_y + height
    for screen in screens:
        frame = screen.frame()
        s_left = float(frame.origin.x)
        s_bottom = float(frame.origin.y)
        s_right = s_left + float(frame.size.width)
        s_top = s_bottom + float(frame.size.height)
        if origin_x < s_right and right > s_left and origin_y < s_top and top > s_bottom:
            return True
    return False


def _window_titled(window_title: str) -> Any | None:
    """The application's NSWindow whose title is `window_title`, or `None`.

    A window title is the only handle the seam has on one window among the
    application's own -- see `present_window_without_activating`. AppKit is
    imported inside the function for the reason `work_area_bounds` spells
    out: this seam is pulled in by headless code paths and must not drag a
    GUI framework into every import of the package.
    """
    try:
        from AppKit import NSApp
    except Exception:
        return None

    app = NSApp()
    if app is None:
        return None

    for window in app.windows():
        if window.title() == window_title:
            return window
    return None


def preserve_frontmost_application(action: Callable[[], None]) -> None:
    """Run `action`, then hand the front back to whichever app had it.

    Creating a Tk `Toplevel` on Aqua activates the application -- measured on
    Tk 8.6.18 / macOS 26.6, reading `NSWorkspace.frontmostApplication()`
    before and after: the front moves to us the moment the window exists,
    before any content is built and before it is ever shown. That is the
    activation the user feels as being thrown onto another Space, so a window
    that must never steal the front has to be built inside this.

    `NSApp.deactivate()` does *not* undo it (measured: the front stayed on us
    for the full second afterwards). Re-activating the previous application
    explicitly does, and is what this uses.

    The front is restored, not prevented: there is a brief moment where we
    hold it. Build the window once, at a moment the user is not mid-gesture,
    rather than on every show.
    """
    try:
        from AppKit import NSWorkspace

        previous = NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        previous = None

    try:
        action()
    finally:
        if previous is not None:
            try:
                if not previous.isActive():
                    previous.activateWithOptions_(0)
            except Exception:
                log.warning("could not hand the front back to %s", previous, exc_info=True)


def prepare_overlay_window(window_title: str) -> bool:
    """Make the window titled `window_title` a click-through overlay that
    follows the user across every Space. Returns whether it was found.

    Three separate facts, all of them about a window that exists to be looked
    at and never touched:

    - **`ignoresMouseEvents`.** This window stays alive and mapped for the
      whole session (that is what keeps its content drawn), so while hidden
      it is still a window sitting at the top level. Without this it would
      swallow clicks aimed at whatever is underneath.
    - **`hidesOnDeactivate`.** Floating windows default to hiding for as long
      as the app is inactive -- that is, always, for a tray app that never
      takes the front.
    - **`collectionBehavior`.** `CanJoinAllSpaces` carries the window between
      *desktop* Spaces; `FullScreenAuxiliary` is what additionally lets it
      draw over the Space of an app running full screen. Read-modify-write,
      never assignment: Tk sets bits of its own here and overwriting them is
      how a window quietly loses its level. `MoveToActiveSpace` is cleared in
      the same breath because AppKit raises
      `NSInternalInconsistencyException` if it is set alongside
      `CanJoinAllSpaces` -- they are mutually exclusive answers to the same
      question.
    """
    window = _window_titled(window_title)
    if window is None:
        return False

    try:
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorMoveToActiveSpace,
        )
    except Exception:
        return False

    window.setIgnoresMouseEvents_(True)
    window.setHidesOnDeactivate_(False)

    behavior = int(window.collectionBehavior())
    behavior &= ~int(NSWindowCollectionBehaviorMoveToActiveSpace)
    behavior |= int(NSWindowCollectionBehaviorCanJoinAllSpaces)
    behavior |= int(NSWindowCollectionBehaviorFullScreenAuxiliary)
    window.setCollectionBehavior_(behavior)
    return True


def hide_window_without_unmapping(window_title: str) -> bool:
    """Take the window titled `window_title` off screen at the Cocoa level,
    leaving the toolkit's own map state alone. Returns whether it was found.

    The counterpart to `present_window_without_activating`, and it exists for
    the same reason that one does. Tk's own way to hide a window (`withdraw`)
    unmaps it, and an unmapped window's children are not laid out or drawn --
    so a window hidden that way and later shown through AppKit comes back as
    an empty rectangle of the right size. `orderOut:` removes the window from
    the screen without telling Tk anything, so the content it already drew
    survives to the next show.

    Measured on Tk 8.6.18 / macOS 26.6, reading the compositor's own
    on-screen list from a separate process: after `orderOut:` the window is
    absent from that list entirely, while `winfo_ismapped()` stays true for
    the window and all 21 of its descendants.
    """
    window = _window_titled(window_title)
    if window is None:
        return False
    window.orderOut_(None)
    return True


def present_window_without_activating(window_title: str) -> bool:
    """`orderFrontRegardless` the NSWindow carrying `window_title`.

    Measured on Tk 8.6.18 / macOS 26.6, from a fresh process each time,
    reading the compositor's own on-screen window list from a *separate*
    process (`CGWindowListCopyWindowInfo`, which needs no Screen Recording
    permission for bounds and owner pid) and NSWorkspace's frontmost
    application before and after:

      toolkit's own show path   visible, but activated the app 10 of 10
      orderFrontRegardless      visible and activated 0 of 5

    This shows a window; it does not *render* one. AppKit will happily order
    in a window the toolkit has never mapped, and the result is a correctly
    sized, correctly placed, entirely empty rectangle -- measured:
    `winfo_ismapped()` false for the window and for 0 of its 21 descendants,
    while the compositor reported it on screen at exactly the requested
    geometry. That empty box is what reached a user. The caller must
    therefore keep the window mapped, which is what
    `hide_window_without_unmapping` is for.

    `isVisible` is NOT a usable check here and was what misled an earlier
    attempt: it answers `True` for a window the window server is not
    compositing at all. Only the on-screen list, read from outside, tells
    the truth -- and, as above, not the whole of it.
    """
    window = _window_titled(window_title)
    if window is None:
        return False
    # Floating/panel-level windows default to hidesOnDeactivate, which would
    # hide this one for exactly as long as the app stays inactive -- that is,
    # always, which is the whole point. Clearing it costs nothing on a window
    # that never had it set.
    window.setHidesOnDeactivate_(False)
    window.orderFrontRegardless()
    return True


def tray_icon_rect(handle: TrayHandle) -> Rect | None:
    """El rectángulo en pantalla del icono de la barra de menú, o `None`.

    Cada `NSStatusItem` vive en su propio `NSStatusBarWindow`, y ese window
    ES la ranura del item: medido contra una barra real, su frame da el ancho
    exacto del botón y el alto completo de la barra de menú. Por eso se usa
    el frame del window y no el del botón -- el botón ocupa sólo la franja
    central, y hit-testear contra él dejaría afuera la fila de píxeles de más
    arriba, que es justo donde macOS deposita el cursor cuando uno empuja el
    mouse contra el borde de la pantalla.

    El frame llega en coordenadas de Cocoa (origen abajo a la izquierda) y
    sale en las de Tk (origen arriba a la izquierda), invertido contra el
    alto que documenta `_cocoa_flip_height`. Olvidarse de esa inversión no
    rompe nada de forma visible: simplemente pone el tooltip abajo de todo.

    El resultado no se puede cachear. El item se corre horizontalmente cada
    vez que aparece o desaparece otro icono en la barra, así que un rect
    leído una vez está mal a los pocos segundos.

    Toda falla -- pyobjc ausente, handle que no es un status item, pystray
    que cambió de atributo, item ya retirado de la barra -- termina en `None`,
    que el llamador lee como "no se puede saber" y responde volviendo al
    tooltip nativo. AppKit sólo se puede tocar desde el main thread, y el
    llamador es responsable de eso (ver `tray_requires_host_event_loop`).
    """
    status_item = handle.native
    if status_item is None:
        return None
    try:
        button = status_item.button()
        if button is None:
            return None
        window = button.window()
        if window is None:
            # El item existe pero todavía no lo montaron en la barra. Sin
            # window no hay geometría que leer.
            return None
        # Un item retirado de la barra conserva su botón, su window y un
        # frame de aspecto perfectamente normal -- medido, sigue devolviendo
        # el rectángulo que ocupaba, o uno viejo fuera de pantalla. Lo único
        # que cambia es `isVisible`. Sin este control el llamador anclaría el
        # tooltip a un icono que ya no existe en vez de volver al nativo.
        if not window.isVisible() or button.isHidden():
            return None
        frame = window.frame()
        width = float(frame.size.width)
        height = float(frame.size.height)
        origin_x = float(frame.origin.x)
        origin_y = float(frame.origin.y)
    except Exception:
        log.debug("could not read the status item's frame", exc_info=True)
        return None

    if width <= 0 or height <= 0:
        return None
    if not _is_on_a_screen(origin_x, origin_y, width, height):
        return None

    flip_height = _cocoa_flip_height()
    if flip_height is None:
        return None

    left = int(round(origin_x))
    # El borde superior en Tk es lo que queda por encima del borde superior
    # del rect en Cocoa (`origin_y + height`, porque Cocoa mide desde abajo).
    top = int(round(flip_height - (origin_y + height)))
    return Rect(
        left=left,
        top=top,
        right=left + int(round(width)),
        bottom=top + int(round(height)),
    )


def cursor_position() -> tuple[int, int] | None:
    """La posición del cursor en el mismo espacio que `tray_icon_rect`.

    `NSEvent.mouseLocation()` es una consulta al servidor de ventanas, no una
    lectura de la jerarquía de vistas, así que a diferencia del frame del
    status item no depende del main thread. Devuelve coordenadas de Cocoa y
    se invierte con el mismo alto, que es lo único que garantiza que el punto
    y el rect se puedan comparar.
    """
    try:
        from AppKit import NSEvent

        point = NSEvent.mouseLocation()
        x = float(point.x)
        y = float(point.y)
    except Exception:
        log.debug("could not read the cursor position", exc_info=True)
        return None

    flip_height = _cocoa_flip_height()
    if flip_height is None:
        return None
    return int(round(x)), int(round(flip_height - y))


#: How long to let `security` run before giving up. It is a local Keychain
#: read, so it is either instant or it is stuck behind an unlock prompt that
#: nobody is going to answer -- and the tray must not freeze waiting.
_SECURITY_TIMEOUT_SECONDS = 10


def read_secret(service: str) -> str | None:
    """Read a generic password out of the login Keychain via `/usr/bin/security`.

    Shelling out rather than linking the Security framework is deliberate.
    A Keychain item carries an ACL naming the apps allowed to read it without
    prompting, and Claude Code's item names Apple-signed `security` among
    them; a fresh unsigned binary of our own would trip the "wants to access
    your keychain" panel on every poll. This also keeps the seam free of a
    pyobjc import at module scope.

    `-w` prints the bare password (here, Claude Code's credentials JSON) and
    nothing else. Every failure -- no such item, locked keychain, denied
    access, `security` missing -- collapses to `None`, because the caller's
    response is the same in each case: treat the user as not logged in.

    The empty name is rejected before it gets there: `security` treats an
    absent `-s` value as "no service constraint" and happily returns the
    first generic password in the keychain, which would be some unrelated
    application's secret handed back as if it were ours. No caller passes an
    empty service today, so this guards a mistake rather than a scenario --
    but the failure mode is silently reading someone else's credentials, and
    that is not a thing to leave to call-site discipline.
    """
    if not service.strip():
        return None
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=_SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def tray_requires_host_event_loop() -> bool:
    # An NSStatusItem belongs to the process's one NSApplication, and under
    # Aqua Tk that run loop is the one `Tk.mainloop()` drives. See the seam's
    # docstring for what the caller has to do about it.
    return True


def tray_anchor_edge() -> str:
    return "top"


#: Cached ObjC delegate class. Declaring an `NSObject` subclass registers the
#: name with the Objective-C runtime, and doing that twice under one name is
#: an error -- so the class is built once, lazily, and reused.
_CLICK_TARGET_CLASS = None


def _click_target_class():
    global _CLICK_TARGET_CLASS
    if _CLICK_TARGET_CLASS is not None:
        return _CLICK_TARGET_CLASS

    import objc
    from AppKit import NSApplication, NSEventModifierFlagControl, NSEventTypeRightMouseUp
    from Foundation import NSObject

    class _ClaudeUsageClickTarget(NSObject):
        """Target for the status item button's action.

        Holds the two Python callables and picks between them by inspecting
        the event that is being dispatched: AppKit gives one action for both
        buttons, and `NSApp.currentEvent()` is the documented way to ask which
        one it was. Control-click counts as secondary because macOS treats it
        that way everywhere else.
        """

        def initWithPrimary_secondary_(self, primary, secondary):
            self = objc.super(_ClaudeUsageClickTarget, self).init()
            if self is None:
                return None
            self._primary = primary
            self._secondary = secondary
            return self

        def onClick_(self, sender):
            event = NSApplication.sharedApplication().currentEvent()
            secondary = False
            if event is not None:
                secondary = event.type() == NSEventTypeRightMouseUp or bool(
                    event.modifierFlags() & NSEventModifierFlagControl
                )
            handler = self._secondary if secondary else self._primary
            handler()

    _CLICK_TARGET_CLASS = _ClaudeUsageClickTarget
    return _CLICK_TARGET_CLASS


def bind_tray_click(status_item: Any, on_primary: Callable[[], None]) -> Any | None:
    """Route a primary click to `on_primary`, leaving the menu on secondary.

    Why this exists: pystray's macOS backend sets `HAS_DEFAULT_ACTION = False`,
    and for a real reason -- once an `NSMenu` is attached to a status item,
    AppKit opens the menu itself and the button's action never fires. So the
    `default=True` that gives Windows its one-click-shows-usage behaviour is
    silently inert on a Mac, and clicking the icon can only ever open a menu.

    The fix is to take the menu back off the status item and dispatch clicks
    ourselves, re-presenting that same menu for secondary clicks. Nothing is
    rebuilt: the `NSMenu` pystray already assembled from the app's menu
    definition is reused as-is, so the two paths can never drift.

    Returns a token the caller must keep a reference to -- it is the ObjC
    target object, and letting it be collected would leave the button firing
    at nothing. Returns `None` if the wiring could not be completed, in which
    case *nothing has been changed*: the menu is still attached and the icon
    behaves exactly as before. The order below matters for that guarantee --
    the menu is detached only once the new target is installed.
    """
    try:
        from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp
        import objc
    except Exception:
        return None

    menu = status_item.menu()
    if menu is None:
        # pystray has not built the menu yet, or there is none. Rewiring now
        # would strand the icon with no menu at all.
        return None

    button = status_item.button()
    if button is None:
        return None

    def open_menu() -> None:
        status_item.popUpStatusItemMenu_(menu)

    target = _click_target_class().alloc().initWithPrimary_secondary_(on_primary, open_menu)
    if target is None:
        return None

    button.setTarget_(target)
    button.setAction_(objc.selector(None, selector=b"onClick:"))
    button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
    # Last: until this line the menu still owns the click, so an exception
    # anywhere above leaves the original behaviour intact.
    status_item.setMenu_(None)
    return target


def _backing_scale_factor() -> float:
    """The main screen's pixels-per-point, or `1.0` when it cannot be read.

    2.0 on every Retina Mac, 1.0 on an external non-Retina display, and
    occasionally 3.0. Degrades to 1.0 rather than raising: rendering at 1x is
    the old, merely-blurry behaviour, and a missing screen is not worth
    costing the user a tray icon.
    """
    try:
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is None:
            return 1.0
        scale = float(screen.backingScaleFactor())
    except Exception:
        return 1.0
    return scale if scale >= 1.0 else 1.0


def bind_tray_hidpi_image(icon: Any) -> bool:
    """Make the menu-bar icon render at the display's real pixel density.

    THIS REPLACES A PRIVATE METHOD OF A DEPENDENCY. Specifically, it swaps
    the *instance* attribute `_assert_image` on a `pystray.Icon` built from
    pystray's macOS backend (`pystray/_darwin.py`, verified against pystray
    0.19.5). The class is left untouched: only this one object is affected,
    and only for as long as it lives.

    Why: pystray's own `_assert_image` resizes the PIL image to
    `(thickness, thickness)` -- 22x22 actual pixels -- serialises that to PNG
    and builds an `NSImage` from the data. An `NSImage` created that way
    reports a size of 22x22 *points* at 1x, so on a Retina display AppKit has
    22 pixels to fill 44, and stretches them. The icon is blurry, and no
    amount of care in the drawing code can fix it, because the detail is gone
    before AppKit ever sees it.

    The fix is one line of intent: rasterise at `thickness * backingScaleFactor`
    pixels and then declare the image's size in POINTS with `setSize_`, which
    is how a @2x/@3x asset tells AppKit "these pixels cover this much space".

    If a future pystray moves, renames or restructures `_assert_image`, the
    symptom is a blurry-but-working icon (this returns `False` and logs at
    warning, having changed nothing), or -- if the method exists but its
    surroundings changed -- a blurry-but-working icon plus an exception
    logged per repaint, because every failure inside the replacement falls
    back to pystray's original bound method. The icon is never lost.

    Returns `True` if the replacement was installed. Callers must repaint
    afterwards (assigning `icon.icon` back to itself is enough) for the
    sharper image to reach the screen.
    """
    try:
        import io

        import AppKit
        import Foundation
        from PIL import Image
    except Exception:
        log.warning("no pyobjc/PIL; the tray icon keeps pystray's 1x rendering", exc_info=True)
        return False

    status_bar = getattr(icon, "_status_bar", None)
    status_item = getattr(icon, "_status_item", None)
    original = getattr(icon, "_assert_image", None)
    if status_bar is None or status_item is None or not callable(original):
        log.warning(
            "pystray's macOS backend does not look as expected "
            "(_status_bar/_status_item/_assert_image); keeping its 1x rendering"
        )
        return False

    def _assert_image_hidpi() -> None:
        try:
            thickness = int(status_bar.thickness())
            if thickness <= 0:
                raise ValueError(f"non-positive status bar thickness: {thickness}")

            # Same cache contract as pystray's: `_update_icon` sets
            # `_icon_image` to None before calling this, so an unchanged
            # NSImage of the right POINT size means there is nothing to do.
            cached = getattr(icon, "_icon_image", None)
            if cached is not None:
                current = cached.size()
                if int(current.width) == thickness and int(current.height) == thickness:
                    return

            source = icon._icon
            if source is None:
                raise ValueError("no PIL image set on the icon")

            pixels = max(1, int(round(thickness * _backing_scale_factor())))
            rendered = source.convert("RGBA")
            if rendered.size != (pixels, pixels):
                rendered = rendered.resize((pixels, pixels), Image.LANCZOS)

            buffer = io.BytesIO()
            rendered.save(buffer, "png")
            image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(buffer.getvalue()))
            if image is None:
                raise ValueError("NSImage could not be built from the rendered PNG")

            # The whole point: `pixels` px of bitmap declared to occupy
            # `thickness` points, so AppKit treats it as a @2x/@3x asset and
            # draws it 1:1 on the backing store instead of upscaling.
            image.setSize_(AppKit.NSMakeSize(float(thickness), float(thickness)))

            icon._icon_image = image
            button = status_item.button()
            if button is None:
                raise ValueError("the status item has no button to set the image on")
            button.setImage_(image)
        except Exception:
            # Loud, but never fatal: the user gets pystray's blurry icon back
            # rather than a tray app that dies on a repaint.
            log.exception("high-DPI tray rendering failed; falling back to pystray's")
            original()

    icon._assert_image = _assert_image_hidpi
    return True
