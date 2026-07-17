"""claude_usage_tray.ui -- widget trees for the tray app's windows.

The only package besides `app.py` permitted to import `tkinter`. Re-exports
the public surface design.md lists for this layer: `show_popup`,
`show_main_window` (the one managed window, carrying the Perfiles and
Configuracion tabs that were `show_profiles_window` and `show_settings_window`
until they were merged), `show_hover_popup` (slice e), `DismissManager` (the
click-outside dismissal registry the managed windows may use), and
`HoverTracker` (slice e's tray-hover state machine).

The window builders are exposed lazily (module `__getattr__`, PEP 562)
rather than imported at module scope. `popup.py`/`main_window.py`/
`profiles_window.py`/`settings_window.py`/`hover_popup.py` import real
`tkinter` at module level -- eagerly importing them here would make even `from
claude_usage_tray.ui.dismiss import DismissManager` fail on a machine without
tkinter installed, since importing any submodule always executes the parent
package's `__init__.py` first.

`dismiss.py`, `hover.py`, `scroll.py` and `colors.py` are the `ui` modules
designed to be headless-testable (see their own docstrings), so this package
must not force tkinter onto them as a side effect of package initialization.
None of them touches tkinter outside `TYPE_CHECKING`. `dismiss`/`hover` are
imported eagerly and safely below because they export part of this layer's
public surface; `scroll` and `colors` are not re-exported -- they are the
arithmetic and the colour rules the widget halves are built out of, and no
caller outside `ui` has any business with either.
"""

from __future__ import annotations

from typing import Any

from .dismiss import DismissManager
from .hover import HoverTracker

__all__ = [
    "DismissManager",
    "HoverTracker",
    "TAB_PROFILES",
    "TAB_SETTINGS",
    "show_hover_popup",
    "show_main_window",
    "show_popup",
]


def __getattr__(name: str) -> Any:
    if name == "show_popup":
        from .popup import show_popup

        return show_popup
    if name == "show_main_window":
        from .main_window import show_main_window

        return show_main_window
    # Deferred like the builders, and for the same reason -- `main_window`
    # imports tkinter at module scope. Defining the two strings here instead
    # would spare the import and cost the single definition: two spellings of
    # the same tab name, in two files, free to drift into a tab that silently
    # never matches. `app.py` is the only caller and imports tkinter anyway.
    if name in ("TAB_PROFILES", "TAB_SETTINGS"):
        from . import main_window

        return getattr(main_window, name)
    if name == "show_hover_popup":
        from .hover_popup import show_hover_popup

        return show_hover_popup
    if name == "hide_hover_popup":
        from .hover_popup import hide_hover_popup

        return hide_hover_popup
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
