"""The enforcing hook: what the harness is told, event by event.

These assert the exact JSON contract Claude Code reads. A field name that
looks right but is not (`permission_decision` for `permissionDecision`, a
`reason` where `permissionDecisionReason` belongs) produces a hook that runs,
exits 0, prints something plausible -- and silently enforces nothing. That
failure is invisible until the night it matters, which is why it is pinned
here rather than left to a manual check.
"""

from __future__ import annotations

import io
import json

import pytest

from claude_usage_tray import budget as budget_module
from claude_usage_tray.budget import Budget
from claude_usage_tray.hooks import cache, gate


@pytest.fixture(autouse=True)
def session(monkeypatch):
    """Every gate test runs inside a Claude session, as the harness does."""
    monkeypatch.setenv(budget_module.SESSION_ENV, "session-alpha")
    return "session-alpha"


@pytest.fixture
def at_percent(monkeypatch):
    """Pin the account's binding usage without touching the network."""

    def _set(value):
        monkeypatch.setattr(cache, "used_percent", lambda **_kwargs: value)

    return _set


def _prompt_event(prompt: str = "keep going", session_id: str = "session-alpha") -> dict:
    return {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session_id}


def _tool_event(tool: str = "Bash", session_id: str = "session-alpha") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": "ls"},
        "session_id": session_id,
    }


# --- UserPromptSubmit ------------------------------------------------------


def test_with_no_budget_the_gate_stays_completely_silent(app_data_dir, at_percent):
    at_percent(99.0)

    assert gate.run(_prompt_event()) is None


def test_below_the_margin_the_gate_stays_silent(app_data_dir, at_percent):
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5))
    at_percent(50.0)

    assert gate.run(_prompt_event()) is None


def test_inside_the_margin_injects_context_without_blocking(app_data_dir, at_percent):
    """The wrap-up window. The turn must still run -- that is the whole
    point: the agent needs a turn in which to save its work."""
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5))
    at_percent(66.0)

    output = gate.run(_prompt_event())

    assert "decision" not in output
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "USAGE BUDGET WARNING" in output["hookSpecificOutput"]["additionalContext"]


def test_at_the_ceiling_the_turn_is_blocked(app_data_dir, at_percent):
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5))
    at_percent(71.0)

    output = gate.run(_prompt_event())

    assert output["decision"] == "block"
    assert "USAGE BUDGET REACHED" in output["reason"]
    # The way out has to be on screen at the moment it is needed.
    assert "override" in output["systemMessage"].lower()


def test_blocking_does_not_use_exit_code_2(app_data_dir, at_percent, capsys):
    """Exit 2 also erases the prompt. Same outcome, worse trade: the user
    loses what they typed to a gate they may not remember setting."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(90.0)

    code = _drive_main(_prompt_event())

    assert code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_the_override_token_passes_a_blocked_prompt_through(app_data_dir, at_percent):
    """Without this, every route to raising the ceiling runs through a
    prompt the gate itself blocks -- a lock with the key inside."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(95.0)

    assert gate.run(_prompt_event(f"raise the cap {gate.OVERRIDE_TOKEN}")) is None


def test_the_override_token_does_not_hit_the_network(app_data_dir, monkeypatch):
    """An override must work when the API is exactly what is broken."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    monkeypatch.setattr(cache, "used_percent", lambda **_kwargs: pytest.fail("must not query usage"))

    assert gate.run(_prompt_event(gate.OVERRIDE_TOKEN)) is None


def test_unknown_usage_never_blocks_a_prompt(app_data_dir, at_percent):
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(None)

    assert gate.run(_prompt_event()) is None


# --- PreToolUse ------------------------------------------------------------


def test_a_tool_call_past_the_ceiling_is_denied(app_data_dir, at_percent):
    """`UserPromptSubmit` does not fire again inside a long autonomous turn.
    Without this event, an agent already past the ceiling keeps spending
    until it chooses to stop -- the assumption this package removes."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(88.0)

    output = gate.run(_tool_event())

    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"
    assert "USAGE BUDGET REACHED" in hook_output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "tool_name",
    ["set_usage_budget", "mcp__claude-usage__set_usage_budget", "mcp__whatever-they-named-it__set_usage_budget"],
)
def test_the_tool_that_lifts_the_ceiling_is_never_blocked_by_it(app_data_dir, at_percent, tool_name):
    """Otherwise the override only half works: it lets the prompt through and
    then denies the one call that would lift the ceiling. Matched on the
    suffix because the MCP prefix depends on what the user named the server."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(99.0)

    assert gate.run(_tool_event(tool_name)) is None


def test_the_block_message_tells_the_agent_not_to_lift_the_cap_itself(app_data_dir, at_percent):
    """That exemption is the one route by which an agent could undo its own
    cap, so the instruction lands in the message it reads at that moment."""
    budget_module.save_budget(Budget(ceiling_percent=70))
    at_percent(90.0)

    reason = gate.run(_prompt_event())["reason"]

    assert "do not raise or clear the ceiling yourself" in reason.lower()


def test_tool_calls_are_not_warned_only_blocked(app_data_dir, at_percent):
    """A wrap-up instruction repeated before every tool call is noise, and
    it is not actionable mid-turn anyway."""
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5))
    at_percent(66.0)

    assert gate.run(_tool_event()) is None


# --- the process contract --------------------------------------------------


def _drive_main(event: dict, monkeypatch=None) -> int:
    import sys

    original = sys.stdin
    sys.stdin = io.StringIO(json.dumps(event))
    try:
        return gate.main()
    finally:
        sys.stdin = original


def test_an_unrecognised_event_is_ignored(app_data_dir, at_percent):
    at_percent(99.0)
    budget_module.save_budget(Budget(ceiling_percent=1))

    assert gate.run({"hook_event_name": "SessionStart"}) is None


def test_garbage_on_stdin_exits_zero_and_prints_nothing(capsys):
    import sys

    original = sys.stdin
    sys.stdin = io.StringIO("not json at all")
    try:
        code = gate.main()
    finally:
        sys.stdin = original

    assert code == 0
    assert capsys.readouterr().out == ""


def test_a_crash_inside_the_gate_still_exits_zero(monkeypatch, capsys):
    """The gate failing must never take the session with it."""
    monkeypatch.setattr(gate, "run", lambda _event: 1 / 0)

    code = _drive_main(_prompt_event())

    assert code == 0
    assert capsys.readouterr().out == ""


def test_the_silent_path_never_touches_the_network(app_data_dir, monkeypatch):
    """With no budget set there is nothing to enforce, so asking the API
    would put a round trip in front of every prompt for no reason at all."""
    monkeypatch.setattr(cache, "used_percent", lambda **_kwargs: pytest.fail("must not query usage"))

    assert gate.run(_prompt_event()) is None
    assert gate.run(_tool_event()) is None


# --- resumed sessions: environment id vs. event id --------------------------
# Claude Code keeps a resumed session's original conversation id -- the one
# `hooks/gate.py` reads from the event payload -- but spawns a *new* MCP
# server process for it, with `CLAUDE_CODE_SESSION_ID` set to a different
# value. `set_usage_budget` only ever sees that environment id, so without the
# alias `session_of` now records, a ceiling written from inside a resumed
# session is filed under an id this gate never looks up: `/usage-override`
# appears to succeed while silently writing to a file nobody reads.


def test_a_ceiling_written_under_the_environment_id_is_read_under_the_event_id(
    app_data_dir, at_percent, monkeypatch
):
    """The regression this whole mechanism exists to fix. FAILS without the
    alias mechanism: pre-fix, `save_budget` called with no explicit
    `session_id` (exactly how `set_usage_budget` calls it) writes under the
    environment id, `load_budget` in `decide()` reads under the event id
    `session_of` returns, and the two files never meet -- the gate sees no
    budget and lets the turn through unblocked."""
    monkeypatch.setenv(budget_module.SESSION_ENV, "env-mcp-session")
    at_percent(96.0)

    # A gate evaluation observes both ids and records the correspondence --
    # any real `UserPromptSubmit` does this before a budget is ever set, so
    # the alias is on record ahead of the write below.
    assert gate.run(_prompt_event(session_id="event-conversation-session")) is None

    # The writer, called exactly as `mcp_server/server.py`'s
    # `set_usage_budget` calls it: no explicit `session_id`, only whatever
    # `current_session_id()` reads from the environment.
    budget_module.save_budget(Budget(ceiling_percent=95.0))

    output = gate.run(_prompt_event(session_id="event-conversation-session"))

    assert output is not None
    assert output["decision"] == "block"
    assert "95%" in output["reason"]


def test_raising_the_ceiling_from_a_blocked_resumed_session_unblocks_the_next_prompt(
    app_data_dir, at_percent, monkeypatch
):
    """`/usage-override <percent>` must actually reach the session it is
    typed into, even when that session has been resumed."""
    monkeypatch.setenv(budget_module.SESSION_ENV, "env-mcp-session-2")
    at_percent(80.0)
    # A ceiling set two days ago, back when this was a fresh session and the
    # event id and environment id still agreed -- the scenario the bug
    # report describes.
    budget_module.save_budget(Budget(ceiling_percent=70.0), "event-conversation-session-2")

    blocked = gate.run(_prompt_event(session_id="event-conversation-session-2"))
    assert blocked["decision"] == "block"

    # The override prompt passes on its own token, and records the alias as
    # a side effect of being evaluated.
    passthrough = gate.run(
        _prompt_event(f"/usage-override 95 {gate.OVERRIDE_TOKEN}", "event-conversation-session-2")
    )
    assert passthrough is None
    # The write `/usage-override 95` triggers: no explicit `session_id`,
    # exactly like `set_usage_budget`.
    budget_module.save_budget(Budget(ceiling_percent=95.0))

    unblocked = gate.run(_prompt_event(session_id="event-conversation-session-2"))

    assert unblocked is None


def test_clearing_the_ceiling_from_a_resumed_session_actually_clears_it(app_data_dir, at_percent, monkeypatch):
    monkeypatch.setenv(budget_module.SESSION_ENV, "env-mcp-session-3")
    at_percent(99.0)
    budget_module.save_budget(Budget(ceiling_percent=70.0), "event-conversation-session-3")

    blocked = gate.run(_prompt_event(session_id="event-conversation-session-3"))
    assert blocked["decision"] == "block"

    # `/usage-override off` clears the ceiling: `set_usage_budget(clear=True)`
    # called with no explicit `session_id`.
    budget_module.clear_budget()

    cleared = gate.run(_prompt_event(session_id="event-conversation-session-3"))

    assert cleared is None


def test_the_unresumed_case_records_no_alias_and_behaves_exactly_as_before(app_data_dir, at_percent):
    """When the event id and environment id already agree -- the ordinary,
    non-resumed case every existing test in this file exercises -- nothing
    new happens: no alias file is written, and the gate's behaviour is
    unchanged from before this mechanism existed."""
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5))
    at_percent(90.0)

    output = gate.run(_prompt_event())  # default session_id equals the env id

    assert output["decision"] == "block"
    assert list(budget_module.paths.alias_dir().glob("*.json")) == []


def test_gate_run_survives_a_record_alias_failure_and_returns_its_normal_decision(
    app_data_dir, at_percent, monkeypatch
):
    """`session_of` calls `budget.record_alias` on every single event with
    no guard of its own around the call -- it depends entirely on
    `record_alias` being total (see that function's docstring). This drives
    `gate.run` directly, not through `gate.main()`'s process-level `except
    Exception`, so it is specifically `record_alias`'s own fail-open
    hardening under test here: simulate the write step inside it failing,
    on a resumed-session event where `session_of` actually attempts to
    record an alias (event id and environment id differ), and confirm the
    gate's verdict for that event comes back exactly as if nothing had gone
    wrong.

    The budget lives directly under the id `session_of` returns for this
    event -- as it would for a session that was fresh when the ceiling was
    set, or one whose writer had already resolved through a *previously*
    recorded alias -- precisely so this read never itself depends on the
    alias `session_of` is about to fail to record. That keeps the test
    isolated to one thing: a broken `record_alias` write must not corrupt
    or prevent the decision `gate.run` returns.
    """
    budget_module.save_budget(Budget(ceiling_percent=70, warn_margin=5), "event-resumed-session")
    at_percent(90.0)

    def _explode(*args, **kwargs):
        raise RuntimeError("disk exploded mid-write")

    monkeypatch.setattr(budget_module.jsonstore, "write_json_atomic", _explode)

    # session_id differs from the environment's "session-alpha", so
    # session_of() calls record_alias() for this event -- and record_alias's
    # own write attempt is what hits the monkeypatched failure above.
    output = gate.run(_prompt_event(session_id="event-resumed-session"))

    assert output["decision"] == "block"
    assert "USAGE BUDGET REACHED" in output["reason"]
    # The failure really was hit and swallowed, not sidestepped for some
    # unrelated reason: no alias ended up on record.
    assert list(budget_module.paths.alias_dir().glob("*.json")) == []
