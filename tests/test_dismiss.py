"""Tests for claude_usage_tray.ui.dismiss.DismissManager.

Everything here uses plain duck-typed fakes -- no real tkinter import,
since tkinter is not installed in this dev/CI environment. `DismissManager`
itself only calls `bind_all` / `bind` / `winfo_exists` / `winfo_rootx` /
`winfo_rooty` / `winfo_width` / `winfo_height` on the objects it is given,
so a fake exposing exactly those methods is a faithful stand-in for the
registry and hit-test logic under test.
"""

from __future__ import annotations

from claude_usage_tray.ui.dismiss import DismissManager


class FakeRoot:
    def __init__(self) -> None:
        self.global_click_handler = None

    def bind_all(self, sequence, func, add="+"):
        assert sequence == "<Button-1>"
        self.global_click_handler = func
        return "funcid"


class FakeWidget:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._rootx = x
        self._rooty = y
        self._width = width
        self._height = height
        self._exists = True
        self.destroy_handler = None

    def bind(self, sequence, func, add="+"):
        assert sequence == "<Destroy>"
        self.destroy_handler = func

    def winfo_exists(self) -> bool:
        return self._exists

    def winfo_rootx(self) -> int:
        return self._rootx

    def winfo_rooty(self) -> int:
        return self._rooty

    def winfo_width(self) -> int:
        return self._width

    def winfo_height(self) -> int:
        return self._height


class FakeEvent:
    def __init__(self, x_root: int, y_root: int, widget=None) -> None:
        self.x_root = x_root
        self.y_root = y_root
        self.widget = widget


def _click(root: FakeRoot, x_root: int, y_root: int) -> None:
    assert root.global_click_handler is not None
    root.global_click_handler(FakeEvent(x_root, y_root))


def test_register_returns_distinct_tokens():
    root = FakeRoot()
    manager = DismissManager(root)
    widget_a = FakeWidget(0, 0, 100, 100)
    widget_b = FakeWidget(0, 0, 100, 100)

    token_a = manager.register(widget_a, lambda: None)
    token_b = manager.register(widget_b, lambda: None)

    assert token_a != token_b


def test_click_outside_widget_dismisses():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))

    _click(root, x_root=500, y_root=500)  # far outside

    assert dismissed == [True]


def test_click_inside_widget_does_not_dismiss():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))

    _click(root, x_root=20, y_root=20)  # inside the 10..60 x 10..60 box

    assert dismissed == []


def test_click_on_boundary_counts_as_inside():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=0, y=0, width=100, height=100)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))

    _click(root, x_root=100, y_root=100)  # exactly on the bottom-right corner

    assert dismissed == []


def test_unregister_stops_dispatch():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    dismissed = []
    token = manager.register(widget, lambda: dismissed.append(True))

    manager.unregister(token)
    _click(root, x_root=500, y_root=500)

    assert dismissed == []


def test_unregister_is_idempotent():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    token = manager.register(widget, lambda: None)

    manager.unregister(token)
    manager.unregister(token)  # must not raise
    manager.unregister(9999)  # unknown token, must not raise


def test_destroyed_widget_is_skipped_without_error():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))
    widget._exists = False

    _click(root, x_root=500, y_root=500)  # would be "outside" if alive

    assert dismissed == []


def test_destroy_event_on_registered_widget_unregisters_it():
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))

    assert widget.destroy_handler is not None
    widget.destroy_handler(FakeEvent(0, 0, widget=widget))

    _click(root, x_root=500, y_root=500)
    assert dismissed == []


def test_destroy_event_from_descendant_does_not_unregister_parent():
    """`<Destroy>` propagates from descendants -- a child widget being
    destroyed must not unregister the parent window's own dismiss handling.
    """
    root = FakeRoot()
    manager = DismissManager(root)
    widget = FakeWidget(x=10, y=10, width=50, height=50)
    child = FakeWidget(x=15, y=15, width=10, height=10)
    dismissed = []
    manager.register(widget, lambda: dismissed.append(True))

    assert widget.destroy_handler is not None
    widget.destroy_handler(FakeEvent(0, 0, widget=child))  # filtered out, must not unregister

    _click(root, x_root=500, y_root=500)
    assert dismissed == [True]


def test_second_window_dismiss_survives_first_windows_teardown():
    """The scenario the old `unbind_all("<Button-1>")` pattern broke: a
    second floating window must keep dismissing correctly after the first
    one is destroyed and unregistered.
    """
    root = FakeRoot()
    manager = DismissManager(root)
    first = FakeWidget(x=0, y=0, width=10, height=10)
    second = FakeWidget(x=200, y=200, width=10, height=10)
    first_dismissed = []
    second_dismissed = []
    manager.register(first, lambda: first_dismissed.append(True))
    manager.register(second, lambda: second_dismissed.append(True))

    first.destroy_handler(FakeEvent(0, 0, widget=first))  # first window closes

    _click(root, x_root=205, y_root=205)  # inside `second`, outside `first`

    assert first_dismissed == []  # unregistered, not evaluated at all
    assert second_dismissed == []  # click landed inside it


def test_multiple_subscribers_each_evaluated_independently():
    root = FakeRoot()
    manager = DismissManager(root)
    inside_widget = FakeWidget(x=0, y=0, width=1000, height=1000)
    outside_widget = FakeWidget(x=10, y=10, width=20, height=20)
    inside_dismissed = []
    outside_dismissed = []
    manager.register(inside_widget, lambda: inside_dismissed.append(True))
    manager.register(outside_widget, lambda: outside_dismissed.append(True))

    _click(root, x_root=500, y_root=500)  # inside the big widget, outside the small one

    assert inside_dismissed == []
    assert outside_dismissed == [True]


def test_permanent_binding_is_registered_once_on_construction():
    root = FakeRoot()
    DismissManager(root)

    assert root.global_click_handler is not None
