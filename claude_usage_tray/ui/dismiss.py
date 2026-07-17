"""claude_usage_tray.ui.dismiss -- click-outside dismissal registry.

Replaces the global-click half of `app.py`'s old `_bind_popup_dismiss`: a
single `root.bind_all("<Button-1>", ..., add="+")`, bound once and never
unbound, dispatching to a small registry of `(widget, on_dismiss)`
subscribers -- instead of the old `cleanup()` running
`self._root.unbind_all("<Button-1>")` on every popup `<Destroy>`.

`unbind_all(sequence)` clears *every* callback bound to that sequence on the
`all` bindtag, for every widget -- so the old code's cleanup, run when the
*first* floating window closes, silently deleted the dismiss handling of any
*other* still-open floating window (a second popup-like surface, e.g. the
profiles window if it ever grew this behavior). A binding that is never
removed cannot be removed wrongly; the registry, not the binding, is what
changes over time. See design.md's "One permanent `<Button-1>` binding plus a
subscriber registry" decision.

`tkinter` is imported only under `TYPE_CHECKING`, for the type hints only.
This module duck-types the handful of methods it actually calls
(`bind_all`, `winfo_exists`, `winfo_rootx`, `winfo_rooty`, `winfo_width`,
`winfo_height`, `bind`), so the registry and hit-test logic are unit-testable
with a plain fake root/window -- no real tkinter import required for tests,
which matters here since tkinter is not installed in this dev/CI
environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import tkinter as tk


class DismissManager:
    """One permanent `<Button-1>` binding on `root`; dispatches to every
    registered widget whose click landed outside it.

    `register(widget, on_dismiss)` returns an opaque token; `unregister`
    is idempotent (safe on an already-removed or unknown token). Widgets
    also self-unregister on their own `<Destroy>`, filtered on
    `event.widget is widget` -- `<Destroy>` propagates from descendants, so
    without the filter a child widget's teardown would unregister the whole
    window.
    """

    def __init__(self, root: "tk.Misc | Any") -> None:
        self._root = root
        self._subscribers: dict[int, tuple[Any, Callable[[], None]]] = {}
        self._next_token = 0
        self._root.bind_all("<Button-1>", self._on_global_click, add="+")

    def register(self, widget: Any, on_dismiss: Callable[[], None]) -> int:
        token = self._next_token
        self._next_token += 1
        self._subscribers[token] = (widget, on_dismiss)
        widget.bind("<Destroy>", self._destroy_handler(widget, token), add="+")
        return token

    def unregister(self, token: int) -> None:
        self._subscribers.pop(token, None)

    def _destroy_handler(self, widget: Any, token: int) -> Callable[[Any], None]:
        def _on_destroy(event: Any) -> None:
            if getattr(event, "widget", None) is widget:
                self.unregister(token)

        return _on_destroy

    def _on_global_click(self, event: Any) -> None:
        for widget, on_dismiss in list(self._subscribers.values()):
            if not _widget_exists(widget):
                continue
            if not _click_is_inside(widget, event):
                on_dismiss()


def _widget_exists(widget: Any) -> bool:
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def _click_is_inside(widget: Any, event: Any) -> bool:
    x1, y1 = widget.winfo_rootx(), widget.winfo_rooty()
    x2, y2 = x1 + widget.winfo_width(), y1 + widget.winfo_height()
    return x1 <= event.x_root <= x2 and y1 <= event.y_root <= y2
