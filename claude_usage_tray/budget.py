"""claude_usage_tray.budget -- the ceiling an agent must not spend past.

The use case this exists for: leaving an agent working unattended overnight
with instructions not to eat the whole week's quota. That needs two numbers,
not one. A single hard cut at 70% stops the agent mid-edit with the work
unsaved -- which is a worse outcome than the overspend it prevented. So a
budget is a *ceiling* plus a *warn margin*, and crossing into the margin is
a deliberate, early "wrap up and save now" while the agent still has quota
left to do it with.

**Why this is L2 and not inside the hook.** Three callers need the same
answer to "which percentage binds": `mcp_server/report.py` reports it,
`hooks/gate.py` enforces it, and the tests check it. `mcp_server` and
`hooks` are both L5 entry points, so the layering gate forbids an edge
between them -- exactly the situation `quotas.py` documents. The shared fact
has to fall to a layer both can reach, or it becomes two copies that agree
only by luck.

**Fail open, always.** Every unknown here resolves to `allow`: no budget
set, usage unreadable, the API down, the file corrupt. A gate that fails
closed on a network blip locks the user out of their own Claude until a
quota window resets hours later, and there is no override to reach for once
the prompt that would set it is itself blocked. Overspending is recoverable;
being unable to type is not.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import jsonstore, paths
from .quotas import QUOTA_WINDOWS

#: How far below the ceiling the warning fires, in percentage points.
DEFAULT_WARN_MARGIN = 5.0

#: Exported by Claude Code into every process it spawns -- the MCP server
#: that writes a budget and the hook that reads it both see the same value,
#: and a subagent inherits its parent's, which is what makes "this agent and
#: the subagents it launched" expressible as a single key. Verified by
#: reading `/proc/<pid>/environ` of a live child MCP server and by the
#: `session_id` a `PreToolUse` event carries: all three agree.
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

#: Budgets older than this are swept on the next write. A ceiling belongs to
#: a session, and sessions end; without a sweep the directory would grow one
#: dead file per Claude session, forever.
STALE_BUDGET_AGE_SECONDS = 7 * 24 * 60 * 60

Action = Literal["allow", "warn", "block"]


def highest_used_percent(payload: dict[str, Any] | None) -> float | None:
    """The binding quota percentage in an `api.fetch_usage` payload.

    The maximum across windows, not the session window: quota is exhausted
    when *any* window fills, so a caller watching only the 5-hour figure
    reads 25% and keeps going while the weekly window dies at 66%. Returns
    None when no window reported a number at all -- which is not 0, and must
    never be read as "plenty left".

    Duplicated nowhere. `mcp_server/report.py` publishes this value and
    `hooks/gate.py` enforces against it; if they ever computed it
    separately, the number the agent was shown and the number that stopped
    it could disagree.
    """
    if not payload:
        return None
    values: list[float] = []
    for window in QUOTA_WINDOWS:
        entry = payload.get(window)
        if not isinstance(entry, dict):
            continue
        raw = entry.get("utilization")
        if raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


@dataclass(frozen=True)
class Budget:
    """A ceiling, and how early to warn before reaching it."""

    ceiling_percent: float
    warn_margin: float = DEFAULT_WARN_MARGIN
    note: str | None = None

    @property
    def warn_at(self) -> float:
        """The percentage at which the wrap-up warning starts.

        Floored at 0 so a margin wider than the ceiling (`--ceiling 3
        --margin 5`) warns from the very first prompt rather than computing
        a negative threshold that can never be crossed -- the user asking
        for a 3% ceiling clearly wants to be told early, not never.
        """
        return max(0.0, self.ceiling_percent - self.warn_margin)


@dataclass(frozen=True)
class Decision:
    """What the gate should do, and what to tell the agent."""

    action: Action
    used_percent: float | None
    budget: Budget | None
    message: str = ""

    @property
    def blocks(self) -> bool:
        return self.action == "block"


# Addressed to the model, not to the user, and therefore written in English
# like every other machine-facing artifact in this repo -- unlike the tray's
# Spanish, which a person reads. Phrased as instructions because a warning
# the agent cannot act on is just noise: it says what to do with the
# remaining quota, not merely that it is running out.
_WARN_TEMPLATE = (
    "USAGE BUDGET WARNING: this account is at {used:.0f}% of its Claude quota, "
    "and the ceiling set for unattended work is {ceiling:.0f}%. You have roughly "
    "{headroom:.0f} percentage points left before further turns are blocked "
    "automatically. Stop starting new work now. Finish or safely abandon the "
    "current step, save and commit anything unsaved, and write down where you "
    "left off so the next session can resume. Then report that you stopped "
    "because of the usage budget."
)

_BLOCK_TEMPLATE = (
    "USAGE BUDGET REACHED: this account is at {used:.0f}% of its Claude quota, "
    "at or past the {ceiling:.0f}% ceiling set for unattended work. This turn was "
    "blocked automatically and no further work will run until the quota resets or "
    "the ceiling is raised. "
    # The tool that lifts the ceiling is exempt from the gate -- it has to be,
    # or a blocked session has no way back in (see `hooks/gate.py`'s
    # EXEMPT_TOOL_SUFFIX). That exemption is also the one route by which an
    # agent could undo its own cap, so the instruction not to is stated here,
    # in the message it is guaranteed to read at exactly that moment.
    "Do NOT raise or clear the ceiling yourself, and do not suggest working "
    "around it: it was set deliberately to cap unattended spending, and only "
    "the user may change it. Report that you stopped and why."
)


def evaluate(used_percent: float | None, budget: Budget | None) -> Decision:
    """Pure policy: percentage plus ceiling in, action out.

    No clock, no file, no network -- so "already past the ceiling", "inside
    the margin" and "usage unknown" are all testable without an account and
    without waiting for a quota window to move.
    """
    if budget is None or used_percent is None:
        return Decision("allow", used_percent, budget)

    if used_percent >= budget.ceiling_percent:
        return Decision(
            "block",
            used_percent,
            budget,
            _BLOCK_TEMPLATE.format(used=used_percent, ceiling=budget.ceiling_percent),
        )

    if used_percent >= budget.warn_at:
        return Decision(
            "warn",
            used_percent,
            budget,
            _WARN_TEMPLATE.format(
                used=used_percent,
                ceiling=budget.ceiling_percent,
                headroom=max(0.0, budget.ceiling_percent - used_percent),
            ),
        )

    return Decision("allow", used_percent, budget)


def to_mapping(budget: Budget) -> dict[str, Any]:
    return {
        "ceiling_percent": budget.ceiling_percent,
        "warn_margin": budget.warn_margin,
        "note": budget.note,
    }


def from_mapping(raw: Any) -> Budget | None:
    """Parse a stored budget, or None if it is absent or unusable.

    Returns None rather than raising on anything malformed, for the
    fail-open reason in the module docstring: a hand-edited file with a
    string where a number belongs must not become a gate that blocks every
    prompt with a traceback.
    """
    if not isinstance(raw, dict):
        return None
    try:
        ceiling = float(raw["ceiling_percent"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        margin = float(raw.get("warn_margin", DEFAULT_WARN_MARGIN))
    except (TypeError, ValueError):
        margin = DEFAULT_WARN_MARGIN
    if ceiling <= 0:
        return None
    note = raw.get("note")
    return Budget(
        ceiling_percent=ceiling,
        warn_margin=max(0.0, margin),
        note=note if isinstance(note, str) else None,
    )


def current_session_id() -> str | None:
    """The Claude session this process belongs to, or None outside one.

    None is meaningful, not an error: the tray, a test runner and a shell
    invocation all legitimately have no session. Callers turn it into "no
    budget applies" rather than guessing at one.
    """
    value = os.environ.get(SESSION_ENV, "").strip()
    return value or None


def _resolve(session_id: str | None) -> str | None:
    return session_id if session_id else current_session_id()


def load_budget(session_id: str | None = None) -> Budget | None:
    """The ceiling for this session, or None if there is none.

    A budget set in one session is invisible to another, by design: it caps
    the agent the user pointed it at, not every Claude on the machine. The
    cost is that it caps only that agent -- three sessions can still spend
    three ceilings' worth between them, which is the trade the user makes
    when they scope it this way.
    """
    resolved = _resolve(session_id)
    if resolved is None:
        return None
    try:
        return from_mapping(jsonstore.read_json(paths.budget_file(resolved)))
    except (jsonstore.CorruptFile, OSError):
        return None


def save_budget(budget: Budget, session_id: str | None = None) -> bool:
    """Persist `budget` for this session. False if there is no session."""
    resolved = _resolve(session_id)
    if resolved is None:
        return False
    target = paths.budget_file(resolved)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(to_mapping(budget))
    # Stored for diagnostics only. Nothing reads it back to decide anything
    # -- the filename is the key -- but a directory of anonymous uuids is
    # unreadable when someone is trying to work out which session is
    # blocked.
    payload["session_id"] = resolved
    jsonstore.write_json_atomic(target, payload)
    sweep_stale()
    return True


def clear_budget(session_id: str | None = None) -> bool:
    """Remove this session's ceiling. Returns whether one was there."""
    resolved = _resolve(session_id)
    if resolved is None:
        return False
    try:
        paths.budget_file(resolved).unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def sweep_stale(*, max_age_seconds: int = STALE_BUDGET_AGE_SECONDS, now: float | None = None) -> int:
    """Delete budgets left behind by sessions that have long since ended.

    Best effort throughout: a directory that cannot be listed, or a file
    that cannot be removed, is not a reason to fail the write that triggered
    the sweep.
    """
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(paths.budget_dir().glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if now - entry.stat().st_mtime > max_age_seconds:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed
