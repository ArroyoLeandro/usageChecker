"""The MCP server itself: one tool, `get_claude_usage`.

**Why one tool and not five.** Every tool costs the calling model context on
every single turn, whether or not it is used, and a menu of
`get_session_usage` / `get_weekly_usage` / `get_fable_usage` forces a caller
that wants the whole picture to make three round trips to assemble what one
call already had in hand. The question being asked is always "where does
this account stand", so the tool answers that, and the caller reads the
field it cares about.

**Why no `mcp` SDK.** The dependency buys a protocol implementation of four
methods -- `initialize`, `notifications/initialized`, `tools/list`,
`tools/call` -- and costs a virtualenv this project does not otherwise need
(the tray ships as a frozen `.exe`, and its `requirements.txt` is the input
to that build). Written against the stdlib, this server starts under a bare
system interpreter with no install step, which is the difference between a
config entry that works on any machine and one that works after four
paragraphs of setup. `requests`, reached through `api.py`, is the only
third-party import in the process.

Reporting-only, on purpose. The server can tell a caller it is at 91% of the
weekly window; it cannot stop that caller from continuing. Nothing in an MCP
result is binding -- the model reads it and decides. Enforcement is a
different mechanism (a hook the harness runs, which can refuse a turn
outright) and deliberately not this package's job.
"""

from __future__ import annotations

import json
from typing import Any

from .. import api
from .. import budget as budget_module
from ..accounts import INHERITED_ENV, OVERRIDE_ENV, profile_for, resolve_config_dir
from . import jsonrpc
from .report import build_report

SERVER_NAME = "claude-usage"
SERVER_VERSION = "1.1.0"

#: Echoed back to the client when it does not name a version it wants.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

TOOL_NAME = "get_claude_usage"
BUDGET_TOOL_NAME = "set_usage_budget"

TOOL_DESCRIPTION = (
    "Report how much of this Claude account's usage quota is already spent. "
    "Returns the 5-hour session window and the 7-day weekly window (plus the "
    "Fable weekly window where the account has one), each as a used percentage "
    "and the time until it resets, along with `highest_used_percent` -- the "
    "binding constraint across all windows. "
    "Call this before starting long or repeated autonomous work (a scheduled "
    "loop, a large batch) to decide whether the remaining quota covers it, and "
    "again between iterations to decide whether to keep going. "
    "By default it reports on the Claude installation that launched this "
    "server; pass `config_dir` to ask about a different account."
)

TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "config_dir": {
            "type": "string",
            "description": (
                "Optional path to a Claude config directory, e.g. "
                "'~/.claude-personal'. Omit to use the account this server "
                f"was launched from (${INHERITED_ENV}, or ${OVERRIDE_ENV} if pinned)."
            ),
        },
        "force_refresh": {
            "type": "boolean",
            "description": (
                "Bypass the short client-side cache and re-query the API. "
                "Leave false when polling in a loop: the cache exists to keep "
                "repeated checks from being rate-limited."
            ),
            "default": False,
        },
    },
    "additionalProperties": False,
}

BUDGET_TOOL_DESCRIPTION = (
    "Set, change or clear the usage ceiling this account may not spend past during "
    "unattended work. Once set, a harness hook enforces it outside the model's "
    "control: turns are refused at the ceiling, and from `warn_margin` points below "
    "it the agent is told to stop taking on new work and save what it has. "
    "Use this when the user asks to cap spending for a long or overnight run "
    "(\"don't go past 70%\"). Pass `clear: true` to remove the cap entirely. "
    "The ceiling applies to the current Claude session and the subagents it "
    "launches. Other sessions on the same machine are unaffected."
)

BUDGET_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ceiling_percent": {
            "type": "number",
            "description": (
                "Quota percentage at which further turns are blocked, 1-100. "
                "Compared against the windows named in `windows` -- by default every window, so it caps whichever is closest to full."
            ),
            "minimum": 1,
            "maximum": 100,
        },
        "warn_margin": {
            "type": "number",
            "description": (
                "How many points below the ceiling to start warning the agent so "
                f"it can save its work. Defaults to {budget_module.DEFAULT_WARN_MARGIN:.0f}."
            ),
            "minimum": 0,
        },
        "note": {
            "type": "string",
            "description": "Optional reminder of why this cap was set.",
        },
        "windows": {
            "type": "array",
            "items": {"type": "string", "enum": ["session", "weekly", "weekly_fable"]},
            "description": (
                "Which quota windows the ceiling watches. Omit for all of them. "
                "Pass [\"weekly\", \"weekly_fable\"] to cap the WEEK only and let the "
                "5-hour window refill and be spent -- the two plans are not "
                "interchangeable, and a weekly cap enforced against the session "
                "window stops the account every afternoon for a quota that was "
                "meant to be used."
            ),
        },
        "clear": {
            "type": "boolean",
            "description": "Remove the ceiling. Ignores every other argument.",
            "default": False,
        },
    },
    "additionalProperties": False,
}


def set_budget(
    ceiling_percent: float | None = None,
    *,
    warn_margin: float | None = None,
    note: str | None = None,
    clear: bool = False,
    windows: list[str] | None = None,
) -> dict[str, Any]:
    """Write, replace or remove the budget the hook reads.

    Validated here rather than trusted from the schema: an MCP client is not
    obliged to enforce `minimum`/`maximum`, and a ceiling of 0 or 500 would
    produce a gate that blocks everything or nothing while looking correctly
    configured from the outside.

    **Never passes its session id explicitly, deliberately.** This process
    only ever sees `CLAUDE_CODE_SESSION_ID` -- a resumed session's
    environment id, not the conversation id `hooks/gate.py` reads budgets
    under (see `budget.py`'s `SESSION_ENV` docstring). `budget._resolve`
    runs the alias lookup only on its *implicit* path (no id given),
    precisely so that an id a caller already names -- like the conversation
    id `session_of` hands `load_budget` explicitly on the read side -- is
    never redirected by a map built to translate a different kind of id.
    So `save_budget`/`clear_budget` below are called with no `session_id`
    argument at all, letting them resolve the environment id through
    `budget.resolved_session_id()` themselves. Reading `current_session_id()`
    here and threading *that* through explicitly, as this function used to,
    would fall on the wrong side of that split and silently reintroduce the
    original bug: the explicit path trusts what it is given, so a raw
    environment id handed to it explicitly stops right there instead of
    being followed to the id the gate reads. `current_session_id()` is
    still read once below, but only to answer "is there a session at all"
    and to report which id the ceiling ended up filed under -- never to
    hand a caller-named id to the store functions.
    """
    env_id = budget_module.current_session_id()
    if env_id is None:
        # The server was not spawned by Claude Code, so there is no session
        # to attach a ceiling to. Saying so is far better than writing a
        # budget nothing will ever read.
        return {
            "ok": False,
            "error": (
                f"No Claude session detected (${budget_module.SESSION_ENV} is unset), so there is "
                "nothing to scope a usage budget to. This tool only works from a server started "
                "by Claude Code."
            ),
        }
    # For the response only -- what the ceiling actually ends up filed
    # under. `save_budget`/`clear_budget` below resolve this exact id
    # themselves, on their implicit path; this is not threaded through to
    # them; see the docstring above for why not.
    session_id = budget_module.resolved_session_id()

    if clear:
        removed = budget_module.clear_budget()
        return {"ok": True, "budget": None, "cleared": removed, "session_id": session_id}

    if ceiling_percent is None:
        return {"ok": False, "error": "ceiling_percent is required unless clear is true."}
    try:
        ceiling = float(ceiling_percent)
    except (TypeError, ValueError):
        return {"ok": False, "error": "ceiling_percent must be a number."}
    if not 0 < ceiling <= 100:
        return {"ok": False, "error": "ceiling_percent must be greater than 0 and at most 100."}

    margin = budget_module.DEFAULT_WARN_MARGIN if warn_margin is None else float(warn_margin)
    active = budget_module.Budget(
        ceiling_percent=ceiling,
        warn_margin=max(0.0, margin),
        note=note,
        windows=budget_module.parse_windows(windows),
    )
    budget_module.save_budget(active)
    return {
        "ok": True,
        "budget": _budget_view(active),
        "session_id": session_id,
        "note": (
            f"Enforced from now on for this session and the subagents it launches. Work is "
            f"blocked at {active.ceiling_percent:.0f}% and a wrap-up warning starts at "
            f"{active.warn_at:.0f}%, measured against {', '.join(active.windows)}. "
            f"Other Claude sessions on this machine are unaffected."
        ),
    }


def _budget_view(active: budget_module.Budget | None) -> dict[str, Any] | None:
    if active is None:
        return None
    return {
        "ceiling_percent": active.ceiling_percent,
        "warn_at_percent": active.warn_at,
        "warn_margin": active.warn_margin,
        "note": active.note,
        "windows": list(active.windows),
    }


def get_usage(config_dir: str | None = None, *, force_refresh: bool = False) -> dict[str, Any]:
    """Resolve the account, fetch through `api.py`, shape the result.

    `try_refresh=False` is not the default `fetch_usage` uses, and the
    difference is the point: refreshing an expired token shells out to
    `claude update`, which is a reasonable thing for a tray the user is
    looking at to do and an unreasonable thing for a background server
    spawned by another Claude to do behind their back. An expired session is
    reported as an error the caller can act on instead.
    """
    resolution = resolve_config_dir(config_dir)
    if not resolution.exists:
        return {
            "ok": False,
            "account": {"config_dir": str(resolution.config_dir), "source": resolution.source},
            "error": f"No Claude config directory at {resolution.config_dir}.",
            "auth_error": True,
        }

    payload = api.fetch_usage(profile_for(resolution), try_refresh=False, force=force_refresh)
    report = build_report(
        payload,
        config_dir=str(resolution.config_dir),
        source=resolution.source,
    )
    # The active ceiling travels with every usage answer. An agent that can
    # see 66% but not the 70% cap has no way to know it is three points from
    # being cut off, which is precisely the moment it should be finishing up
    # rather than starting something new.
    #
    # Called with no explicit id, deliberately: this is the implicit path,
    # so `budget._resolve` already resolves the environment id through any
    # recorded alias on its own. Nothing to thread through by hand here, the
    # way `set_budget` above must for its explicit calls.
    active = budget_module.load_budget()
    report["budget"] = _budget_view(active)
    if active is not None:
        # The same windows the gate enforces against, so the number the agent
        # is shown and the number that stops it cannot disagree.
        windowed = budget_module.used_percent_for(payload, active.windows)
        decision = budget_module.evaluate(
            windowed if windowed is not None else report.get("highest_used_percent"),
            active,
        )
        report["budget_status"] = decision.action
        if decision.message:
            report["budget_message"] = decision.message
    return report


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    # Echo the client's protocol version when it names one: this server has
    # no version-specific behaviour, so refusing a version it would in fact
    # have spoken correctly would be a self-inflicted incompatibility.
    requested = params.get("protocolVersion")
    version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _handle_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "inputSchema": TOOL_INPUT_SCHEMA,
            },
            {
                "name": BUDGET_TOOL_NAME,
                "description": BUDGET_TOOL_DESCRIPTION,
                "inputSchema": BUDGET_TOOL_INPUT_SCHEMA,
            },
        ]
    }


def _call_usage(arguments: dict[str, Any]) -> dict[str, Any]:
    return get_usage(
        arguments.get("config_dir"),
        force_refresh=bool(arguments.get("force_refresh")),
    )


def _call_set_budget(arguments: dict[str, Any]) -> dict[str, Any]:
    return set_budget(
        arguments.get("ceiling_percent"),
        warn_margin=arguments.get("warn_margin"),
        windows=arguments.get("windows"),
        note=arguments.get("note"),
        clear=bool(arguments.get("clear")),
    )


TOOL_CALLS: dict[str, Any] = {
    TOOL_NAME: _call_usage,
    BUDGET_TOOL_NAME: _call_set_budget,
}


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    call = TOOL_CALLS.get(str(params.get("name")))
    if call is None:
        # An unknown *tool* is a tool-level failure, not a protocol-level
        # one: the client asked a well-formed question and deserves an
        # answer it can show the model, rather than a JSON-RPC error frame.
        return _tool_result({"ok": False, "error": f"unknown tool: {params.get('name')}"}, is_error=True)

    result = call(params.get("arguments") or {})
    return _tool_result(result, is_error=not result.get("ok", False))


def _tool_result(report: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    # Both shapes on purpose: `structuredContent` for clients that parse it,
    # and the same object as pretty JSON text for those that only read
    # `content`. One source object, so they cannot drift apart.
    return {
        "content": [{"type": "text", "text": json.dumps(report, indent=2, ensure_ascii=False)}],
        "structuredContent": report,
        "isError": is_error,
    }


HANDLERS: dict[str, jsonrpc.Handler] = {
    "initialize": _handle_initialize,
    "notifications/initialized": lambda _params: None,
    "ping": lambda _params: {},
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


def main() -> None:
    jsonrpc.log(f"{SERVER_NAME} {SERVER_VERSION} ready on stdio")
    jsonrpc.serve(HANDLERS)
