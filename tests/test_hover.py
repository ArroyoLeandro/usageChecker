"""Unit tests for claude_usage_tray.ui.hover -- the tray-hover state machine.

The spike proved the *mechanism* works on Windows (rect obtainable, 30ms
poll affordable, enter/leave strictly alternating, DPI-consistent). It could
not prove the *logic* on top of it, because it had no logic -- it was a
measurement harness. This is that logic, and it is decidable here: the
tracker takes its OS access as injected callables, so a fake rect and a fake
cursor exercise every edge rule with no tray, no cursor and no display.

What these tests do NOT cover, and cannot on Linux: that
`platform.tray_icon_rect` returns a true rect, that `cursor_position` shares
its coordinate space, or that the popup they drive renders. Those are the
Windows-only claims the spike measured and the app's own smoke test must
confirm.
"""

from __future__ import annotations

import pytest

from claude_usage_tray.platform import Rect
from claude_usage_tray.ui.hover import DEFAULT_FAILURE_LIMIT, POLL_INTERVAL_S, HoverTracker

ICON = Rect(left=100, top=200, right=124, bottom=230)

INSIDE = (110, 215)
OUTSIDE = (10, 10)


class _Recorder:
    """Collects the tracker's callbacks in the order they fire."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.enter_rects: list[Rect] = []

    def on_enter(self, rect: Rect) -> None:
        self.events.append("enter")
        self.enter_rects.append(rect)

    def on_leave(self) -> None:
        self.events.append("leave")

    def on_unavailable(self) -> None:
        self.events.append("unavailable")


def _tracker(recorder: _Recorder, rects, cursors, **kwargs) -> HoverTracker:
    """A tracker driven by scripted rect/cursor sequences.

    The last value of each sequence repeats forever, so a test only has to
    write the part it cares about.
    """
    rect_iter = list(rects)
    cursor_iter = list(cursors)

    def next_rect():
        return rect_iter.pop(0) if len(rect_iter) > 1 else rect_iter[0]

    def next_cursor():
        return cursor_iter.pop(0) if len(cursor_iter) > 1 else cursor_iter[0]

    return HoverTracker(
        rect_of=next_rect,
        cursor_of=next_cursor,
        on_enter=recorder.on_enter,
        on_leave=recorder.on_leave,
        on_unavailable=recorder.on_unavailable,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Carried forward from the spike (do not drift without re-measuring)
# ---------------------------------------------------------------------------


def test_poll_interval_is_the_one_the_spike_measured():
    # 30ms: lag bound median 15ms / p95 28ms at 0.00% of one core.
    assert POLL_INTERVAL_S == 0.030


# ---------------------------------------------------------------------------
# Enter / leave edges
# ---------------------------------------------------------------------------


def test_entering_the_rect_fires_enter_once():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [OUTSIDE, INSIDE])

    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == ["enter"]
    assert tracker.inside is True


def test_enter_carries_the_rect_it_matched_against():
    # The popup anchors to this rect; handing it a stale one would place the
    # window somewhere the icon no longer is.
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [INSIDE])

    tracker.poll_once()

    assert recorder.enter_rects == [ICON]


def test_staying_inside_does_not_re_fire_enter():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [INSIDE])

    for _ in range(10):
        tracker.poll_once()

    assert recorder.events == ["enter"]


def test_leaving_the_rect_fires_leave_once():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [INSIDE, OUTSIDE])

    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == ["enter", "leave"]
    assert tracker.inside is False


def test_staying_outside_fires_nothing_at_all():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [OUTSIDE])

    for _ in range(10):
        tracker.poll_once()

    assert recorder.events == []
    assert tracker.inside is False


def test_repeated_cycles_strictly_alternate():
    # The spike's CHECK 4 as an executable rule: 6 clean cycles, no stuck
    # state. There it was measured against a real cursor; here it is the
    # logic's own guarantee.
    recorder = _Recorder()
    cursors = []
    for _ in range(6):
        cursors += [OUTSIDE, INSIDE, INSIDE, OUTSIDE]
    tracker = _tracker(recorder, [ICON], cursors)

    for _ in range(len(cursors)):
        tracker.poll_once()

    assert recorder.events == ["enter", "leave"] * 6
    for first, second in zip(recorder.events, recorder.events[1:]):
        assert first != second, "edge sequence must strictly alternate"


def test_hit_testing_is_half_open_on_every_boundary():
    # Matches the spike's point_in_rect exactly: a cursor on the right or
    # bottom edge belongs to the next pixel, not to us.
    assert ICON.contains(ICON.left, ICON.top) is True
    assert ICON.contains(ICON.right - 1, ICON.bottom - 1) is True
    assert ICON.contains(ICON.right, ICON.top) is False
    assert ICON.contains(ICON.left, ICON.bottom) is False
    assert ICON.contains(ICON.left - 1, ICON.top) is False


def test_a_moved_rect_is_hit_tested_against_its_new_position():
    # The taskbar can be moved and icons reorder; the rect is re-read every
    # poll rather than cached at startup.
    moved = Rect(left=500, top=600, right=524, bottom=630)
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON, moved], [INSIDE, INSIDE])

    tracker.poll_once()  # inside the original rect
    tracker.poll_once()  # same cursor, rect has moved away

    assert recorder.events == ["enter", "leave"]


# ---------------------------------------------------------------------------
# Rule 3: the cursor cannot be read
# ---------------------------------------------------------------------------


def test_an_unknown_cursor_leaves_the_state_untouched():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [INSIDE, None, None])

    tracker.poll_once()
    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == ["enter"], "an unreadable cursor must not fake a leave"
    assert tracker.inside is True


def test_an_unknown_cursor_never_retires_the_tracker():
    # The rect is fine; only the hit test is unavailable. That is not a
    # reason to give up on hover for the session.
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [None], failure_limit=3)

    for _ in range(10):
        tracker.poll_once()

    assert recorder.events == []
    assert tracker.retired is False


# ---------------------------------------------------------------------------
# Rule 1: the rect cannot be read
# ---------------------------------------------------------------------------


def test_a_vanishing_rect_leaves_rather_than_stranding_the_popup_open():
    # The spike's stuck-state failure mode, prevented by construction: if the
    # rect we were hit-testing against disappears, the popup must close.
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON, None], [INSIDE], failure_limit=99)

    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == ["enter", "leave"]
    assert tracker.inside is False


def test_a_transient_rect_failure_does_not_retire_the_tracker():
    recorder = _Recorder()
    tracker = _tracker(recorder, [None, None, ICON], [OUTSIDE], failure_limit=5)

    tracker.poll_once()
    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == []
    assert tracker.retired is False


def test_the_failure_count_resets_once_the_rect_comes_back():
    # Otherwise a flaky-but-usable shell would accumulate failures across
    # minutes and eventually retire a tracker that is working fine.
    recorder = _Recorder()
    tracker = _tracker(recorder, [None, None, ICON, None, None], [OUTSIDE], failure_limit=3)

    for _ in range(5):
        tracker.poll_once()

    assert tracker.retired is False, "non-consecutive failures must not accumulate"


def test_sustained_rect_failure_retires_and_reports_once():
    recorder = _Recorder()
    tracker = _tracker(recorder, [None], [OUTSIDE], failure_limit=3)

    for _ in range(10):
        tracker.poll_once()

    assert recorder.events == ["unavailable"], "on_unavailable must fire exactly once"
    assert tracker.retired is True


def test_a_retired_tracker_stops_polling_entirely():
    recorder = _Recorder()
    calls = {"rect": 0}

    def rect_of():
        calls["rect"] += 1
        return None

    tracker = HoverTracker(
        rect_of=rect_of,
        cursor_of=lambda: OUTSIDE,
        on_enter=recorder.on_enter,
        on_leave=recorder.on_leave,
        on_unavailable=recorder.on_unavailable,
        failure_limit=2,
    )
    for _ in range(10):
        tracker.poll_once()

    assert tracker.retired is True
    assert calls["rect"] == 2, "a retired tracker must not keep asking the shell"


def test_retirement_closes_an_open_popup_before_reporting():
    # Order matters: fall back to the native tooltip with our own window
    # already gone, not stranded on screen underneath it.
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON, None], [INSIDE], failure_limit=1)

    tracker.poll_once()
    tracker.poll_once()

    assert recorder.events == ["enter", "leave", "unavailable"]


def test_retirement_is_one_way_even_if_the_rect_returns():
    # Flapping between a custom and a native tooltip would be worse than
    # either; the fallback can always show something, so we go there once.
    recorder = _Recorder()
    tracker = _tracker(recorder, [None, None, ICON, ICON], [INSIDE], failure_limit=2)

    for _ in range(6):
        tracker.poll_once()

    assert recorder.events == ["unavailable"]
    assert tracker.retired is True


def test_the_default_failure_limit_is_about_a_second_of_polling():
    # Long enough that a shell hiccup does not cost the custom popup, short
    # enough that an overflow-parked icon reaches the native tooltip fast.
    assert DEFAULT_FAILURE_LIMIT * POLL_INTERVAL_S == pytest.approx(1.0, abs=0.05)


# ---------------------------------------------------------------------------
# run(): a raising callback must not silently end hover detection
# ---------------------------------------------------------------------------


def test_a_raising_probe_is_counted_rather_than_escaping_the_loop():
    recorder = _Recorder()

    def boom():
        raise OSError("the shell went away")

    tracker = HoverTracker(
        rect_of=boom,
        cursor_of=lambda: OUTSIDE,
        on_enter=recorder.on_enter,
        on_leave=recorder.on_leave,
        on_unavailable=recorder.on_unavailable,
        interval=0.0,
        failure_limit=3,
    )
    tracker.run()

    # run() exits by retiring, not by propagating -- and it reports why.
    assert recorder.events == ["unavailable"]
    assert tracker.retired is True


def test_run_stops_when_asked():
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [OUTSIDE], interval=0.0)
    tracker.stop()

    tracker.run()  # must return immediately rather than hang

    assert recorder.events == []


def test_stop_is_idempotent():
    tracker = _tracker(_Recorder(), [ICON], [OUTSIDE])
    tracker.stop()
    tracker.stop()


# ---------------------------------------------------------------------------
# Host-loop driving: the same state machine, ticked by somebody else's loop
#
# macOS reads the tray rect out of live AppKit state, and AppKit is main-thread
# only. The tracker's own daemon thread is therefore the wrong driver there,
# and `app.py` hands it `root.after` instead. These tests use a scheduler that
# records instead of waiting, so the arrangement is decidable with no Tk.
# ---------------------------------------------------------------------------


class _FakeLoop:
    """A scheduler that queues callbacks instead of waiting for a clock."""

    def __init__(self) -> None:
        self.pending: list[tuple[float, object]] = []

    def schedule(self, delay, fn) -> None:
        self.pending.append((delay, fn))

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            if not self.pending:
                return
            _, fn = self.pending.pop(0)
            fn()


def test_a_scheduled_tracker_starts_no_thread():
    # The whole point on macOS: a 30ms poll touching AppKit from a daemon
    # thread is the undefined behaviour this avoids.
    import threading

    loop = _FakeLoop()
    before = threading.active_count()
    tracker = _tracker(_Recorder(), [ICON], [OUTSIDE], scheduler=loop.schedule)

    tracker.start()

    assert threading.active_count() == before
    assert len(loop.pending) == 1


def test_a_scheduled_tracker_reports_the_same_edges():
    loop = _FakeLoop()
    recorder = _Recorder()
    tracker = _tracker(recorder, [ICON], [OUTSIDE, INSIDE, INSIDE, OUTSIDE], scheduler=loop.schedule)

    tracker.start()
    loop.run(4)

    assert recorder.events == ["enter", "leave"]


def test_each_scheduled_tick_re_arms_exactly_one_more():
    # Re-armed at the end of the tick, not on a repeating timer: a slow poll
    # must not queue up behind itself on a loop that also has to draw a UI.
    loop = _FakeLoop()
    tracker = _tracker(_Recorder(), [ICON], [OUTSIDE], scheduler=loop.schedule)

    tracker.start()
    for _ in range(5):
        assert len(loop.pending) == 1
        loop.run(1)

    assert len(loop.pending) == 1
    assert tracker.inside is False


def test_a_scheduled_tracker_stops_re_arming_once_asked():
    loop = _FakeLoop()
    tracker = _tracker(_Recorder(), [ICON], [OUTSIDE], scheduler=loop.schedule)

    tracker.start()
    tracker.stop()
    loop.run(1)

    assert loop.pending == []


def test_a_scheduled_tracker_stops_re_arming_once_retired():
    # Retirement has to end the ticks as firmly as it ends the thread's loop,
    # or a retired tracker would keep the host loop busy forever.
    loop = _FakeLoop()
    recorder = _Recorder()
    tracker = _tracker(recorder, [None], [OUTSIDE], scheduler=loop.schedule, failure_limit=3)

    tracker.start()
    loop.run(10)

    assert recorder.events == ["unavailable"]
    assert loop.pending == []


def test_starting_a_scheduled_tracker_twice_arms_it_once():
    loop = _FakeLoop()
    tracker = _tracker(_Recorder(), [ICON], [OUTSIDE], scheduler=loop.schedule)

    tracker.start()
    tracker.start()

    assert len(loop.pending) == 1


def test_a_raising_probe_retires_a_scheduled_tracker_too():
    loop = _FakeLoop()
    recorder = _Recorder()

    def boom():
        raise OSError("AppKit went away")

    tracker = HoverTracker(
        rect_of=boom,
        cursor_of=lambda: OUTSIDE,
        on_enter=recorder.on_enter,
        on_leave=recorder.on_leave,
        on_unavailable=recorder.on_unavailable,
        failure_limit=3,
        scheduler=loop.schedule,
    )
    tracker.start()
    loop.run(10)

    assert recorder.events == ["unavailable"]
    assert loop.pending == []
