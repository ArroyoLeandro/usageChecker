"""Shared types for the platform seam.

Kept separate from `__init__.py` so the OS-specific backends
(`_windows.py`, `_darwin.py`, `_posix.py`) can import these types without a
circular import against the facade module that selects them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PlatformUnsupportedError(RuntimeError):
    """Raised when a requested action has no meaning on the current platform."""


class FileManagerError(OSError):
    """Raised when launching the platform's file manager fails."""


@dataclass(frozen=True)
class WorkArea:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class TrayHandle:
    """Lo que un sistema operativo necesita para identificar nuestro icono.

    Existe porque la pregunta "¿dónde está mi icono en pantalla?" se contesta
    con datos distintos en cada plataforma: Windows la contesta con el handle
    de ventana (`HWND`) más el id de notify-icon, y macOS con el propio objeto
    `NSStatusItem`. Un único parámetro con forma de Windows obligaría al
    backend de macOS a recibir un `hwnd` inventado que después descarta, y esa
    mentira es justo el tipo de cosa que el seam existe para evitar.

    `native` es opaco y es propiedad del backend: nadie fuera de
    `platform/` debe interpretarlo. `uid` sólo tiene sentido en Windows y es
    inerte en el resto; se declara acá, y no en la firma de cada función, para
    que agregar un dato propio de una plataforma no cambie la firma común.

    Quién obtiene `native` sigue siendo del llamador: es un atributo privado
    de pystray, y hurgar en una librería de bandeja no es un hecho del sistema
    operativo. El seam sólo dice *qué atributo* mirar
    (`tray_handle_attribute()`) y qué hacer con el valor.
    """

    native: Any
    uid: int = 0


@dataclass(frozen=True)
class Rect:
    """A screen rectangle in the OS's own coordinate space.

    Kept distinct from `WorkArea` even though the fields match: a work area
    is a desktop fact used to place windows, while a `Rect` here is a tray
    icon's hit-testing box. Collapsing them would invite passing one where
    the other is meant, and they are only coincidentally the same shape.
    """

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        """Half-open hit test: left/top inclusive, right/bottom exclusive.

        The same convention the hover spike measured DPI correctness against
        (`point_in_rect`), so a cursor sample that counted as "inside" there
        counts as "inside" here.
        """
        return self.left <= x < self.right and self.top <= y < self.bottom
