"""claude_usage_tray.quotas -- what the three quota windows are, and are called.

One module, at L1, because four modules across three layers need the same
answer and two of them are forbidden from asking each other:

* `alerts.py` (L2) evaluates one entry per (profile, quota window).
* `settings.py` (L2) now stores tiers per quota window -- and `alerts` and
  `settings` are **both L2**, so neither may import the other
  (`tests/test_layering.py`). Before per-window thresholds, `alerts` could own
  `QUOTA_WINDOWS` privately because nothing else at its layer needed it. The
  moment the config had to name the same three windows, that stopped being
  possible, and the constant had to fall to a layer they can both reach.
* `ui/settings_window.py` (L4) labels a field per quota window.
* `app.py` (L5) labels a toast per quota window -- and `ui/*` cannot import
  `app.py`, since that edge points *up*. So the labels could not live there
  either once the settings window needed them.

**The labels live here, with the ids, on purpose.** The usage-alerts spec
requires that "a toast, the popup and this window must call the same quota the
same thing", and two dicts in two files are not a mechanism for that -- they
are a promise to remember. One dict is the mechanism.

This does put user-facing Spanish at L1, which `theme.py` deliberately avoids
(it holds `Palette` *field names* and lets the UI spell them). The two are not
in tension: `theme.py` has one consumer for its copy, so the copy belongs with
that consumer; these three names have three consumers who must agree, so the
name is the shared fact and the agreement is the point. `formatting.py` is the
precedent -- it is L1 and writes the tooltip's own visible text.

No tkinter, no PIL, no I/O, nothing. Stdlib typing only.
"""

from __future__ import annotations

from typing import Literal

QuotaWindow = Literal["session", "weekly", "weekly_fable"]

# The three quota windows a poll can report, in the order everything renders
# and evaluates them. Matches `api.fetch_usage`'s payload keys exactly -- these
# strings are the API's, not ours, which is why they are English while every
# label below is Spanish.
QUOTA_WINDOWS: tuple[QuotaWindow, ...] = ("session", "weekly", "weekly_fable")

# The Spanish name of each quota window, as the user reads it -- in the tray
# notification, in the usage popup, and in the alerts control. Lifted verbatim
# from `app.ALERT_WINDOW_LABELS`, which was the first place to need them and
# is now one of this module's callers rather than the owner.
QUOTA_WINDOW_LABELS: dict[QuotaWindow, str] = {
    "session": "Ultimas 5 horas",
    "weekly": "Ultimos 7 dias",
    "weekly_fable": "Fable en los ultimos 7 dias",
}


def quota_label(window: str) -> str:
    """The Spanish name of `window`, or `window` itself if it is not one.

    Falls back rather than raising, and deliberately: the only callers are a
    toast and a label, and neither has any business failing because a payload
    grew a fourth quota window before this module heard about it.

    Takes a plain `str` rather than a `QuotaWindow` for that same reason -- the
    fallback only means anything if a value outside the Literal can reach it,
    so narrowing the parameter would delete the case this function exists for.

    Written as a scan rather than `QUOTA_WINDOW_LABELS.get(window, window)`
    because that does not type-check and should not: `Mapping`'s key type is
    invariant, so a `dict[QuotaWindow, str]` is not a `dict[str, str]`, and
    indexing one with an arbitrary `str` is exactly the mistake the checker
    ought to catch everywhere else. Three comparisons is not a cost worth
    buying an `Any` or a silenced error with.
    """
    for known in QUOTA_WINDOWS:
        if window == known:
            return QUOTA_WINDOW_LABELS[known]
    return window
