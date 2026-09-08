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

The session-id alias store (`record_alias`, `_resolve_alias`, below) lives
here for the identical reason: both `mcp_server/server.py` (via
`set_usage_budget`) and `hooks/gate.py` need "which session does this
environment id actually mean", and the same L5-peers-cannot-import-each-other
rule that put the ceiling policy here applies to the id it is keyed by.

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
from collections.abc import Sequence
from typing import Any, Literal

from . import jsonstore, paths
from .quotas import QUOTA_WINDOWS

#: How far below the ceiling the warning fires, in percentage points.
DEFAULT_WARN_MARGIN = 5.0

#: Exported by Claude Code into every process it spawns -- the MCP server
#: that writes a budget and the hook that reads it both see the same value,
#: and a subagent inherits its parent's, which is what makes "this agent and
#: the subagents it launched" expressible as a single key. True for a fresh
#: session, verified by reading `/proc/<pid>/environ` of a live child MCP
#: server and by the `session_id` a `PreToolUse` event carries.
#:
#: **A resumed session breaks that agreement.** The conversation keeps its
#: original id -- the one `hooks/gate.py` reads straight from the event
#: payload, and the one the user thinks of as "this session" -- but Claude
#: Code starts a *new* MCP server process for it, with this environment
#: variable set to that process's own, different id. `set_usage_budget` only
#: ever sees the environment id, so a ceiling written from inside a resumed
#: session is filed under an id the gate never looks up: `/usage-override`
#: appears to succeed and silently writes to a file nobody reads, leaving a
#: blocked session with no way to unblock itself. `hooks/gate.py`'s
#: `session_of` is the one place that observes both ids on the same event,
#: so it records the correspondence (`record_alias`, below) whenever they
#: differ, and `resolved_session_id()` -- the implicit path through
#: `_resolve`, below -- follows it back whenever a caller reads the
#: environment id rather than naming a session it already knows. An
#: explicitly named id is never run through this lookup; see `_resolve`'s
#: docstring for why that asymmetry is the whole point of the mechanism.
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

#: Budgets older than this are swept on the next write. A ceiling belongs to
#: a session, and sessions end; without a sweep the directory would grow one
#: dead file per Claude session, forever.
STALE_BUDGET_AGE_SECONDS = 7 * 24 * 60 * 60

Action = Literal["allow", "warn", "block"]


def used_percent_for(
    payload: dict[str, Any] | None,
    windows: Sequence[str] | None = None,
) -> float | None:
    """The binding quota percentage across the windows a caller cares about.

    Defaults to every window, which is the right answer for a report: quota
    is exhausted when *any* window fills, so a reader watching only the
    5-hour figure sees 25% and keeps going while the weekly window dies at
    66%.

    A *budget* is the one caller that legitimately narrows this. The plans
    are not interchangeable: the 5-hour window refills four times a day and
    is meant to be spent, while the weekly one is the money. An owner who
    says "do not eat the week" and gets stopped every afternoon by the
    session window has been given a cap they did not ask for -- and the
    usual repair, raising the number until the afternoons stop hurting,
    also raises the cap on the window they were actually protecting.

    Unknown names are ignored rather than raising, and a selection that
    names no window with a number returns None -- which is not 0, and must
    never be read as "plenty left".
    """
    if not payload:
        return None
    selected = tuple(windows) if windows else QUOTA_WINDOWS
    values: list[float] = []
    for window in selected:
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


def highest_used_percent(payload: dict[str, Any] | None) -> float | None:
    """The binding quota percentage across ALL windows.

    Kept as its own name because that is what a report publishes and what
    `mcp_server/report.py` puts in `highest_used_percent`; the windowed
    variant above is for budgets. Duplicated nowhere: both are one function.
    """
    return used_percent_for(payload, QUOTA_WINDOWS)


@dataclass(frozen=True)
class Budget:
    """A ceiling, and how early to warn before reaching it."""

    ceiling_percent: float
    warn_margin: float = DEFAULT_WARN_MARGIN
    note: str | None = None
    #: Which quota windows this ceiling watches. All of them by default, so
    #: a budget written before this field existed keeps its old meaning. A
    #: caller that names only `("weekly", "weekly_fable")` is saying "cap the
    #: week, let the 5-hour window refill and be spent" -- see
    #: `used_percent_for` for why that is a real distinction and not a knob.
    windows: tuple[str, ...] = QUOTA_WINDOWS

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
        "windows": list(budget.windows),
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
        windows=parse_windows(raw.get("windows")),
    )


def parse_windows(raw: Any) -> tuple[str, ...]:
    """The windows a stored budget names, falling back to all of them.

    Absent, malformed, or naming nothing this build knows about all resolve
    to every window -- the pre-existing meaning. Narrowing is the deliberate
    act; a corrupt field must not silently widen the account's exposure by
    turning a weekly cap into no cap at all.
    """
    if not isinstance(raw, (list, tuple)):
        return QUOTA_WINDOWS
    named = tuple(w for w in QUOTA_WINDOWS if w in {str(item) for item in raw})
    return named or QUOTA_WINDOWS


def current_session_id() -> str | None:
    """The Claude session this process belongs to, or None outside one.

    None is meaningful, not an error: the tray, a test runner and a shell
    invocation all legitimately have no session. Callers turn it into "no
    budget applies" rather than guessing at one.
    """
    value = os.environ.get(SESSION_ENV, "").strip()
    return value or None


#: Field names for a stored alias. Kept short and separate from
#: `to_mapping`'s budget keys because an alias file and a budget file are
#: different shapes living in different directories -- nothing ever reads
#: both from the same payload.
_ALIAS_ENV_KEY = "env_session_id"
_ALIAS_EVENT_KEY = "event_session_id"

#: How many alias hops `_resolve_alias` will follow before giving up and
#: failing open with wherever it has gotten to.
#:
#: A real chain is at most one hop per time a session has been resumed in a
#: row -- `E2 -> E1 -> C` after two resumes, one more link per resume after
#: that -- and nobody resumes the same conversation eight times before its
#: first prompt. Unbounded following, by contrast, turns a corrupted map or
#: a two-file cycle (`A -> B`, `B -> A`, both perfectly writable individually)
#: into either an infinite loop or a `RecursionError`, either of which is a
#: gate that no longer fails open -- exactly the failure this module's
#: fail-open promise forbids. Eight is generous headroom over the plausible
#: case while still bounding the cost of a hostile or corrupted directory to
#: a handful of file reads.
_MAX_ALIAS_HOPS = 8


def record_alias(env_session_id: str, event_session_id: str) -> None:
    """Remember that the MCP server's environment id actually means the
    hook's event id, for a resumed session where the two have diverged.

    `hooks/gate.py`'s `session_of` is the only call site: a hook evaluation
    is the one moment in the whole system where both ids are in hand at
    once, because the event payload carries one and `os.environ` carries the
    other. Everywhere else -- `set_usage_budget`, the tray -- only the
    environment id exists to read.

    A no-op, deliberately, whenever there is nothing worth recording: either
    id blank, or the two already equal. The equal case is not just an
    optimisation -- it is what keeps a plain, non-resumed session from ever
    growing an `aliases/` directory at all, since `_resolve_alias` below
    would answer the same environment id back regardless of whether the file
    exists.

    Writes only when the stored mapping is absent or different. This runs
    on the hot path -- `session_of` is called for every prompt *and* every
    tool call, so a resumed session reaches here thousands of times -- and
    re-writing an identical mapping each time would put a temp-file-plus-
    rename and a full `sweep_stale()` directory walk in front of every tool
    call the user makes, to record a fact that has not changed since the
    first one. Re-reading it first is the cheaper half of that trade, and
    the write stays last-write-wins for the case where the mapping genuinely
    does change.

    Every operation that touches disk here -- the skip-if-unchanged read,
    the `mkdir`, the write, the trailing `sweep_stale()` -- sits inside one
    `try` that catches `Exception`, not just `OSError`, so the function is
    total: nothing it does can escape as an exception, whatever the failure
    mode. That matters beyond this module's own I/O, because `session_of`
    (`hooks/gate.py`) calls this on every prompt and every tool call with no
    guard of its own around the call; a raise here would surface there. In
    practice `gate.main()` already wraps its entire dispatch in a
    process-level `except Exception`, so a raise here would not crash the
    harness -- but it *would* discard whatever `run()` had already decided
    for this event, turning a routine allow/warn/block verdict into total
    silence for that one prompt. Catching here keeps that outcome from ever
    depending on how far up the call stack a broad handler happens to sit.
    An unwritable `aliases/` directory, either way, costs the user a
    `/usage-override` that lands on the wrong id -- the bug this mechanism
    exists to fix, no worse than before it existed, and a strictly smaller
    cost than losing the gate's verdict for the event that triggered it.
    """
    env_id = (env_session_id or "").strip()
    event_id = (event_session_id or "").strip()
    if not env_id or not event_id or env_id == event_id:
        return
    try:
        if _resolve_alias(env_id) == event_id:
            return
        target = paths.alias_file(env_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        jsonstore.write_json_atomic(target, {_ALIAS_ENV_KEY: env_id, _ALIAS_EVENT_KEY: event_id})
        sweep_stale()
    except Exception:  # noqa: BLE001 -- see docstring: this function must be total
        return


def _resolve_alias(env_session_id: str) -> str:
    """Follow a recorded alias chain from `env_session_id` to the id it
    ultimately stands for, or return `env_session_id` unchanged if there is
    none recorded for it.

    A single hop is not enough. A session resumed twice in a row writes two
    links -- `E1 -> C` from the first resume, then `E2 -> E1` from the
    second -- and a writer holding only `E2` has to cross both to land on
    `C`, the id `hooks/gate.py` actually reads budgets under. Stopping after
    one hop would resolve `E2` to `E1` and write `budgets/E1.json`, while the
    reader still reads `budgets/C.json`: the exact divergence this whole
    mechanism exists to close, reopened one resume later.

    So this follows the chain: read the alias for the current id, and if one
    exists, move to it and read again, up to `_MAX_ALIAS_HOPS` times. It
    stops and returns the current id as soon as a step has no alias
    recorded -- the terminal, common case -- and also stops and returns the
    current id (not the id that would be visited next) the moment the next
    hop would revisit an id already seen in this call, which is the only way
    a chain can fail to terminate (`A -> B`, `B -> A`, or a longer cycle
    built the same way). Both of those are fail-open outcomes, consistent
    with every other read in this module: a missing file (the overwhelming
    majority of calls -- most sessions are never resumed at all), a corrupt
    one, one hand-edited into a shape without a usable string, a cycle, or a
    chain longer than the bound all resolve to "stop here and use the best
    id found so far" rather than raising or looping. This function must
    never raise and must never block a prompt over a detail as small as a
    diagnostic mapping failing to parse or looping back on itself.
    """
    current = env_session_id
    visited = {current}
    for _ in range(_MAX_ALIAS_HOPS):
        try:
            payload = jsonstore.read_json(paths.alias_file(current))
        except (jsonstore.CorruptFile, OSError):
            return current
        if not isinstance(payload, dict):
            return current
        aliased = payload.get(_ALIAS_EVENT_KEY)
        if not (isinstance(aliased, str) and aliased.strip()):
            return current
        next_id = aliased.strip()
        if next_id in visited:
            # A cycle: following further would repeat ids forever. `current`
            # is the furthest this chain could progress before it would
            # start retracing itself, so that -- not `next_id`, which is
            # what closes the loop -- is the fail-open answer.
            return current
        visited.add(next_id)
        current = next_id
    # The bound was reached without hitting a terminal id or a cycle: an
    # implausibly long chain, or a corrupted map that keeps producing new
    # ids forever. Fail open with the last id actually reached rather than
    # keep reading indefinitely.
    return current


def resolved_session_id() -> str | None:
    """The environment session id, translated through any alias recorded
    for it -- the public counterpart to `current_session_id()` for callers
    that want the id a budget should actually be filed under.

    `current_session_id()` stays a raw, unaliased read on purpose: it is
    what `hooks/gate.py`'s `session_of` needs to record an alias correctly
    in the first place (recording `env -> event` requires the *unresolved*
    environment id, not wherever an existing alias would already redirect
    it), and it is also the leaf primitive `_resolve_alias` above must not
    itself depend on. This function is the other side of that split: read
    the environment, then follow whatever alias chain is on record for it.
    `_resolve`, below, calls this for every implicit (no `session_id`
    argument) store operation -- which is how `set_usage_budget`
    (`mcp_server/server.py`) reaches it: that function calls
    `save_budget`/`clear_budget` with no explicit id at all, so this is what
    actually resolves the environment id it runs in before either function
    touches a file.
    """
    env_id = current_session_id()
    if env_id is None:
        return None
    return _resolve_alias(env_id)


def _resolve(session_id: str | None) -> str | None:
    """The session id every store operation below actually keys on.

    The split that fixes the aliasing bug: an explicit `session_id` is
    returned exactly as given, with no alias lookup at all, while the
    implicit path -- no argument, meaning "whatever session this process is
    in" -- resolves through `resolved_session_id()`, environment plus alias.

    That asymmetry is deliberate, not an oversight. An explicit id is a
    caller naming a *specific* session it already means: a test id, or
    `hooks/gate.py`'s `session_of` handing over the conversation id it read
    straight from the event payload -- the one id in this whole system that
    is authoritative by construction and must never be redirected by a map
    built to translate a *different* kind of id (environment ids). Running
    every explicit id through `_resolve_alias` unconditionally, as an
    earlier version of this function did, could not tell those two apart:
    an alias recorded because some environment id `X` once meant conversation
    `Y` would just as happily fire the day `X` was itself an authoritative
    conversation id passed in explicitly, silently rerouting one session's
    reads and writes onto another's file. `set_usage_budget`
    (`mcp_server/server.py`) is the reason the implicit path exists at all:
    it only ever has an environment id to start from, and it deliberately
    never resolves that id itself or passes it to `save_budget`/
    `clear_budget` explicitly -- it calls them bare, with no `session_id`
    argument, so it is *this* function, via `resolved_session_id()`, that
    turns the environment id into the id the gate reads. Passing a
    caller-resolved id through explicitly instead would put that id on the
    "trust it as given" side of this same split one call too early, for no
    benefit -- there is nothing left to resolve by the time it would arrive
    -- and every future explicit caller added to this module would then have
    to remember not to do the same by accident. Simplest to keep exactly one
    path that ever performs this resolution.
    """
    if session_id:
        return session_id
    return resolved_session_id()


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
    """Delete budgets, and recorded aliases, left behind by sessions that
    have long since ended.

    Both directories share `STALE_BUDGET_AGE_SECONDS` rather than each
    getting its own constant: an alias and the budget it exists to route to
    are retired by the same event -- the session ending -- so a second
    staleness window would be one more number to keep in sync with the
    first for no gained precision. Without this, `aliases/` would grow one
    dead file per *resumed* session, forever, exactly as `budgets/` would
    without the budget half of this sweep.

    Best effort throughout: a directory that cannot be listed, or a file
    that cannot be removed, is not a reason to fail the write that triggered
    the sweep, and a failure sweeping one directory must not skip the other.
    """
    now = time.time() if now is None else now
    removed = 0
    for directory in (paths.budget_dir(), paths.alias_dir()):
        try:
            entries = list(directory.glob("*.json"))
        except OSError:
            continue
        for entry in entries:
            try:
                if now - entry.stat().st_mtime > max_age_seconds:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
