"""The usage gate: a Claude Code hook that refuses turns past a ceiling.

Wired to two events, because they stop different things and neither alone is
enough:

* **`UserPromptSubmit`** fires once per turn, including every iteration a
  scheduled loop injects. This is where the warning belongs -- it is the
  only point at which the agent is between pieces of work and can still
  wrap up cleanly -- and where the hard block does most of its work.
* **`PreToolUse`** fires before every tool call. It exists because a single
  autonomous turn can run for a long time, and `UserPromptSubmit` will not
  fire again until that turn is over. Without it, an agent already past the
  ceiling keeps spending until it decides to stop on its own -- which is the
  exact assumption this package exists to remove.

`PreToolUse` never warns, only blocks. A warning repeated before every tool
call would be noise the agent learns to skim, and the wrap-up instruction it
carries is only actionable between turns anyway.

**Scope.** A ceiling belongs to the Claude session that set it, and to the
subagents that session launches -- Claude Code exports `CLAUDE_CODE_SESSION_ID`
into every process it spawns, and a child session inherits its parent's value,
so "this agent and everything it started" is exactly one key. Other sessions on
the same machine are untouched, which is the point: capping an unattended
overnight run must not also cap the user working in another terminal. The cost
is that three sessions can spend three ceilings' worth between them.

**Escape hatch.** A prompt containing `usage-override` passes untouched. It
has to exist and it has to live in the prompt itself: once the ceiling is
reached, every route to raising it goes through a prompt this hook would
otherwise block, and a lock with the key inside is not a safety feature.

The token carries no `!` prefix on purpose. Claude Code reads a leading `!`
as its shell prefix, so `!usage-override ...` was handed to bash instead of
becoming prompt text -- the one position users naturally reach for was the
one position that could never work. A bare token also makes the token a
substring of `/usage-override`, so the slash command doubles as the escape
hatch and there is one thing to remember instead of two.

**Failure is silence.** Any unreadable input, unset budget, or unknown usage
exits 0 with no output. See `budget.py` for why fail-open is the only
defensible default here.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .. import budget as budget_module
from . import cache

#: Typed into a prompt to bypass the gate for that one turn.
#:
#: Deliberately unprefixed: a leading `!` is Claude Code's shell prefix, so a
#: prompt starting with the token never reached this hook at all. Matched as a
#: plain substring, which also means `/usage-override` triggers it -- the hook
#: sees a slash command's raw text, not its expansion.
OVERRIDE_TOKEN = "usage-override"

#: The tool that lifts the ceiling is never blocked by the ceiling.
#:
#: Matched on the suffix because MCP tools arrive namespaced by their server
#: (`mcp__claude-usage__set_usage_budget`), and that prefix depends on what
#: the user named the server in their config -- which this package does not
#: control and must not assume.
#:
#: Without this, `OVERRIDE_TOKEN` only half works: it lets the *prompt*
#: through, and then `PreToolUse` denies the very call that would lift the
#: ceiling. Two doors, one key. The tool that undoes the gate has to sit
#: outside the gate, or there is no way back in from inside a session.
EXEMPT_TOOL_SUFFIX = "set_usage_budget"

#: Shown to the user (not the model) when a turn is blocked, so the way out
#: is on screen at the moment they need it rather than in a README.
_USER_HINT = (
    "Blocked by the Claude usage budget. Run /usage-override <percent> to raise "
    "it, /usage-override off to clear it, or put usage-override anywhere in a "
    "prompt to bypass the gate for one turn."
)


def session_of(event: dict[str, Any]) -> str | None:
    """Which session's ceiling applies to this event.

    The event's own `session_id` first, the environment second. They agree
    in practice -- verified against a live `PreToolUse` event and the
    `/proc` environment of the MCP server serving the same session -- but
    the event is the more specific claim, so it wins, and the environment
    keeps the gate working on any event shape that omits the field.
    """
    from_event = str(event.get("session_id") or "").strip()
    return from_event or budget_module.current_session_id()


def decide(prompt: str = "", session_id: str | None = None) -> budget_module.Decision:
    """The gate's verdict for the current moment.

    Split from the I/O so the interesting cases -- override present, no
    budget, usage unknown, inside the margin, past the ceiling -- are
    testable without a pipe and without an account.
    """
    active = budget_module.load_budget(session_id)
    if active is None:
        return budget_module.Decision("allow", None, None)
    if OVERRIDE_TOKEN in prompt:
        return budget_module.Decision("allow", None, active)
    return budget_module.evaluate(cache.used_percent(), active)


def user_prompt_submit(event: dict[str, Any]) -> dict[str, Any] | None:
    decision = decide(str(event.get("prompt") or ""), session_of(event))

    if decision.action == "block":
        # `decision: "block"` rather than exit code 2: both stop the turn,
        # but exit 2 also erases what the user typed. Losing a prompt the
        # user spent a minute writing, to a gate they may not remember
        # setting, is a bad trade for an identical outcome.
        return {"decision": "block", "reason": decision.message, "systemMessage": _USER_HINT}

    if decision.action == "warn":
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": decision.message,
            }
        }

    return None


def pre_tool_use(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("tool_name") or "").endswith(EXEMPT_TOOL_SUFFIX):
        return None

    decision = decide("", session_of(event))
    if not decision.blocks:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.message,
        },
        "systemMessage": _USER_HINT,
    }


HANDLERS = {
    "UserPromptSubmit": user_prompt_submit,
    "PreToolUse": pre_tool_use,
}


def run(event: dict[str, Any]) -> dict[str, Any] | None:
    handler = HANDLERS.get(str(event.get("hook_event_name")))
    return handler(event) if handler else None


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            return 0
        output = run(event)
    except Exception:  # noqa: BLE001 -- a crashing gate must not break the session
        return 0
    if output is not None:
        print(json.dumps(output, ensure_ascii=False))
    return 0
