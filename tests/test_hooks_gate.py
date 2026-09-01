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
