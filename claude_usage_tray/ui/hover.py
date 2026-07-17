"""claude_usage_tray.ui.hover -- tray-icon hover detection, as pure logic.

pystray publishes no hover event, so the only route that does not fork the
library is the one the design-phase spike measured: ask the shell for our
icon's rectangle and poll the cursor against it. This module is the polling
half, and it imports neither tkinter nor `platform`.

Everything OS-shaped arrives as an injected callable (`rect_of`, `cursor_of`)
and everything UI-shaped leaves as an injected callback (`on_enter`,
`on_leave`, `on_unavailable`). That is not ceremony: `poll_once` is then a
pure state transition over two inputs, so the edge rules below -- which are
the entire reason the spike had to exist -- are decidable on Linux, with no
tray, no cursor, and no display server. The thread in `run()` is a loop
around it and holds no logic of its own.

Two states -- OUTSIDE and INSIDE -- and one retirement:

  1. rect is None            -> count a failure; leave if we were inside, so
                                a popup can never strand itself open. At
                                `failure_limit` consecutive failures, retire
                                and report `on_unavailable` exactly once.
  2. rect readable           -> reset the failure count.
  3. cursor unknown          -> do nothing this tick; the rect is still fine.
  4. inside and was outside  -> on_enter(rect)
  5. outside and was inside  -> on_leave()

Rule 1's failure budget exists because a rect can vanish for entirely
ordinary reasons -- an icon dragged into the taskbar overflow has no rect at
all. One missed read is noise; a sustained run of them means hover tracking
is not available in this session, and the caller must fall back to the native
tooltip rather than show nothing.

Retirement is deliberately one-way. A tracker that flapped between custom and
native tooltips as the shell's answer came and went would be worse than
either mode: the fallback is always able to show *something*, so the safe
direction is toward it, once.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..platform import Rect

# Carried forward from the spike, which swept 250/125/60/30 ms and measured
# 30 ms at 0.00% of one core with a detection-lag bound of median 15 ms /
# p95 28 ms. The cost being immeasurable is what makes the tightest interval
# the right one: there is nothing to buy by polling slower.
POLL_INTERVAL_S = 0.030

# ~1 second of consecutive unreadable rects before giving up on hover for the
# session. Long enough that a transient shell hiccup does not retire us,
# short enough that a user whose icon lives in the overflow reaches the
# native tooltip almost immediately.
DEFAULT_FAILURE_LIMIT = 33

RectOf = Callable[[], "Rect | None"]
CursorOf = Callable[[], "tuple[int, int] | None"]
OnEnter = Callable[["Rect"], None]
OnLeave = Callable[[], None]
OnUnavailable = Callable[[], None]


class HoverTracker:
    """Polls a rectangle against the cursor and reports enter/leave edges.

    Callbacks fire on the polling thread, never the Tk main thread -- the
    caller is responsible for hopping them across (`app.py` puts them on its
    existing UI queue). This class deliberately does not know that a UI
    exists.
    """

    def __init__(
        self,
        *,
        rect_of: RectOf,
        cursor_of: CursorOf,
        on_enter: OnEnter,
        on_leave: OnLeave,
        on_unavailable: OnUnavailable,
        interval: float = POLL_INTERVAL_S,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
    ) -> None:
        self._rect_of = rect_of
        self._cursor_of = cursor_of
        self._on_enter = on_enter
        self._on_leave = on_leave
        self._on_unavailable = on_unavailable
        self._interval = interval
        self._failure_limit = failure_limit
        self._inside = False
        self._failures = 0
        self._retired = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def inside(self) -> bool:
        """Whether the cursor was over the icon at the last readable poll."""
        return self._inside

    @property
    def retired(self) -> bool:
        """Whether this tracker has given up and reported `on_unavailable`."""
        return self._retired

    def poll_once(self) -> None:
        """One tick of the state machine. Pure with respect to the clock."""
        if self._retired:
            return

        rect = self._rect_of()
        if rect is None:
            self._failures += 1
            if self._inside:
                # Rule 1: never leave the popup stranded because the rect we
                # were hit-testing against disappeared underneath it.
                self._inside = False
                self._on_leave()
            if self._failures >= self._failure_limit:
                self._retired = True
                self._on_unavailable()
            return

        self._failures = 0
        point = self._cursor_of()
        if point is None:
            return  # rule 3: the rect is fine; we simply cannot hit-test now

        now_inside = rect.contains(point[0], point[1])
        if now_inside and not self._inside:
            self._on_enter(rect)
        elif not now_inside and self._inside:
            self._on_leave()
        self._inside = now_inside

    def run(self) -> None:
        """Poll until `stop()` or retirement. Blocks; `start()` threads it.

        Each tick is individually guarded for the same reason `app.py`'s UI
        pump is: a single raising poll must not end hover detection for the
        life of the process, silently, in a way that looks nothing like the
        original failure.
        """
        while not self._stop.is_set() and not self._retired:
            started = time.perf_counter()
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - see docstring
                self._failures += 1
                if self._failures >= self._failure_limit and not self._retired:
                    self._retired = True
                    self._on_unavailable()
            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.0, self._interval - elapsed))

    def start(self) -> None:
        """Run `run()` on a daemon thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="hover-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the polling thread to finish. Idempotent."""
        self._stop.set()
