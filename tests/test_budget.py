"""The ceiling policy: which percentage binds, and what to do about it.

The scenario driving every case here is an agent left working unattended:
warn early enough that it can save its work, block hard at the ceiling, and
never -- under any failure -- block for a reason the user cannot undo.
"""

from __future__ import annotations

import pytest

from claude_usage_tray import budget as budget_module
from claude_usage_tray.budget import (
    DEFAULT_WARN_MARGIN,
    SESSION_ENV,
    Budget,
    clear_budget,
    current_session_id,
    evaluate,
    from_mapping,
    highest_used_percent,
    load_budget,
    save_budget,
    sweep_stale,
)


@pytest.fixture
def session(monkeypatch):
    """A Claude session id in the environment, as Claude Code provides."""
    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    return "session-alpha"


# --- which percentage binds ------------------------------------------------


def test_the_binding_percentage_is_the_fullest_window_not_the_session():
    """The whole reason this function exists. A caller watching only the
    5-hour figure sees 25% and keeps going while the weekly window, the one
    that actually runs out, sits at 66%."""
    payload = {
        "session": {"utilization": 25.0},
        "weekly": {"utilization": 66.0},
        "weekly_fable": {"utilization": 0.0},
    }

    assert highest_used_percent(payload) == 66.0


def test_no_readable_window_is_unknown_not_zero():
    """`None` and `0.0` lead a gate to opposite decisions: 'do not block on
    a guess' versus 'the account is untouched, spend freely'."""
    assert highest_used_percent(None) is None
    assert highest_used_percent({}) is None
    assert highest_used_percent({"session": None, "weekly": None, "weekly_fable": None}) is None
    assert highest_used_percent({"session": {"utilization": None}}) is None


def test_a_malformed_utilization_is_skipped_rather_than_fatal():
    payload = {"session": {"utilization": "banana"}, "weekly": {"utilization": 40.0}}

    assert highest_used_percent(payload) == 40.0


def test_windows_outside_the_known_set_do_not_count():
    """Per-model cuts live in `raw` and are reported separately. Letting one
    of them raise the binding figure would block on a limit that is not the
    account's actual quota."""
    payload = {"session": {"utilization": 10.0}, "seven_day_opus": {"utilization": 99.0}}

    assert highest_used_percent(payload) == 10.0


# --- the policy ------------------------------------------------------------


def test_below_the_margin_nothing_happens():
    decision = evaluate(50.0, Budget(ceiling_percent=70, warn_margin=5))

    assert decision.action == "allow"
    assert decision.message == ""


def test_inside_the_margin_warns_and_says_what_to_do():
    """The point of the margin: the agent still has quota left to commit and
    write down where it stopped. A warning that only announces the problem
    would waste that headroom."""
    decision = evaluate(66.0, Budget(ceiling_percent=70, warn_margin=5))

    assert decision.action == "warn"
    assert "66%" in decision.message and "70%" in decision.message
    assert "save" in decision.message.lower()
    assert "stop starting new work" in decision.message.lower()


def test_the_warning_starts_exactly_at_the_margin_boundary():
    active = Budget(ceiling_percent=70, warn_margin=5)

    assert evaluate(64.9, active).action == "allow"
    assert evaluate(65.0, active).action == "warn"


def test_at_and_above_the_ceiling_blocks():
    active = Budget(ceiling_percent=70, warn_margin=5)

    assert evaluate(70.0, active).blocks is True
    assert evaluate(99.9, active).blocks is True


def test_no_budget_never_blocks():
    """Zero friction when the feature is unused: someone who never sets a
    ceiling must not notice this code exists."""
    assert evaluate(99.0, None).action == "allow"


def test_unknown_usage_never_blocks():
    """Fail open. A network blip must not lock the user out of the session
    they would need in order to lift the ceiling."""
    assert evaluate(None, Budget(ceiling_percent=70)).action == "allow"


def test_a_margin_wider_than_the_ceiling_warns_from_the_start():
    """`warn_at` floors at 0 rather than going negative: someone asking for
    a 3% ceiling wants to be told early, not never."""
    active = Budget(ceiling_percent=3, warn_margin=5)

    assert active.warn_at == 0.0
    assert evaluate(0.0, active).action == "warn"
    assert evaluate(3.0, active).blocks is True


def test_the_default_margin_is_applied_when_unspecified():
    assert Budget(ceiling_percent=70).warn_at == 70 - DEFAULT_WARN_MARGIN


# --- the store -------------------------------------------------------------


def test_a_saved_budget_reads_back(app_data_dir, session):
    save_budget(Budget(ceiling_percent=70, warn_margin=5, note="overnight run"))

    restored = load_budget()

    assert restored == Budget(ceiling_percent=70.0, warn_margin=5.0, note="overnight run")


def test_no_file_is_no_budget(app_data_dir, session):
    assert load_budget() is None


def test_a_corrupt_budget_file_is_no_budget_rather_than_a_crash(app_data_dir, session):
    """Fail open again, at the store layer: a half-written file must not
    become a gate that blocks every prompt with a traceback."""
    target = budget_module.paths.budget_file(session)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ not json", encoding="utf-8")

    assert load_budget() is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"ceiling_percent": "seventy"},
        {"ceiling_percent": 0},
        {"ceiling_percent": -5},
        "not a mapping",
    ],
)
def test_unusable_stored_values_parse_to_no_budget(raw):
    assert from_mapping(raw) is None


def test_a_nonsense_margin_falls_back_to_the_default():
    parsed = from_mapping({"ceiling_percent": 70, "warn_margin": "wide"})

    assert parsed is not None
    assert parsed.warn_margin == DEFAULT_WARN_MARGIN


def test_clearing_reports_whether_anything_was_there(app_data_dir, session):
    save_budget(Budget(ceiling_percent=70))

    assert clear_budget() is True
    assert clear_budget() is False
    assert load_budget() is None


# --- session scoping -------------------------------------------------------


def test_a_ceiling_set_in_one_session_is_invisible_to_another(app_data_dir, monkeypatch):
    """The whole point of scoping: capping an unattended overnight agent must
    not also cap the user working in another terminal."""
    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    save_budget(Budget(ceiling_percent=70))

    monkeypatch.setenv(SESSION_ENV, "session-beta")

    assert load_budget() is None


def test_each_session_keeps_its_own_ceiling(app_data_dir, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    save_budget(Budget(ceiling_percent=70))
    monkeypatch.setenv(SESSION_ENV, "session-beta")
    save_budget(Budget(ceiling_percent=30))

    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    assert load_budget().ceiling_percent == 70
    monkeypatch.setenv(SESSION_ENV, "session-beta")
    assert load_budget().ceiling_percent == 30


def test_clearing_one_session_leaves_the_others_alone(app_data_dir, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    save_budget(Budget(ceiling_percent=70))
    monkeypatch.setenv(SESSION_ENV, "session-beta")
    save_budget(Budget(ceiling_percent=30))

    clear_budget()

    assert load_budget() is None
    monkeypatch.setenv(SESSION_ENV, "session-alpha")
    assert load_budget() is not None


def test_a_subagent_sharing_its_parents_session_id_inherits_the_ceiling(app_data_dir, session):
    """Claude Code exports the same `CLAUDE_CODE_SESSION_ID` into a child
    session, which is what makes "this agent and the subagents it launched"
    a single key rather than a tree that has to be tracked."""
    save_budget(Budget(ceiling_percent=70))

    assert load_budget(session).ceiling_percent == 70


def test_outside_a_claude_session_there_is_no_budget(app_data_dir, monkeypatch):
    """The tray, a test runner and a plain shell all legitimately have no
    session. None of them should read or write somebody's ceiling."""
    monkeypatch.delenv(SESSION_ENV, raising=False)

    assert current_session_id() is None
    assert save_budget(Budget(ceiling_percent=70)) is False
    assert load_budget() is None
    assert clear_budget() is False


def test_a_blank_session_id_counts_as_none(app_data_dir, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "   ")

    assert current_session_id() is None
    assert save_budget(Budget(ceiling_percent=70)) is False


def test_an_explicit_session_id_beats_the_environment(app_data_dir, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "session-alpha")

    save_budget(Budget(ceiling_percent=70), "session-gamma")

    assert load_budget() is None
    assert load_budget("session-gamma").ceiling_percent == 70


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "..", "....//....//x", "/etc/shadow", "a\\..\\..\\b", "."],
)
def test_a_session_id_cannot_escape_the_budget_directory(app_data_dir, hostile):
    """The id arrives from outside the process -- a hook's stdin, an
    environment variable -- and is concatenated into a path.

    What matters is that the result is a single path component inside the
    budget directory. Literal dots surviving *inside* a name are harmless:
    `_.._.._etc_passwd.json` traverses nothing, because there is no
    separator for a `..` to be a segment of.
    """
    budget_dir = budget_module.paths.budget_dir()

    save_budget(Budget(ceiling_percent=70), hostile)

    written = list(budget_dir.glob("*.json"))
    assert len(written) == 1
    target = written[0]
    assert target.parent.resolve() == budget_dir.resolve()
    assert target.name == target.parts[-1]  # one component, no separators
    assert budget_dir.resolve() in target.resolve().parents


def test_the_stored_file_records_which_session_it_belongs_to(app_data_dir, session):
    """A directory of anonymous uuids is unreadable when someone is trying
    to work out which session is blocked."""
    save_budget(Budget(ceiling_percent=70))

    stored = budget_module.jsonstore.read_json(budget_module.paths.budget_file(session))

    assert stored["session_id"] == session


def test_budgets_from_long_dead_sessions_are_swept(app_data_dir, monkeypatch):
    """Sessions end; without a sweep the directory grows one dead file per
    Claude session, forever."""
    monkeypatch.setenv(SESSION_ENV, "old-session")
    save_budget(Budget(ceiling_percent=70))
    stale = budget_module.paths.budget_file("old-session")
    import os

    os.utime(stale, (0, 0))

    removed = sweep_stale()

    assert removed == 1
    assert not stale.exists()


def test_the_sweep_spares_budgets_still_in_use(app_data_dir, session):
    save_budget(Budget(ceiling_percent=70))

    assert sweep_stale() == 0
    assert load_budget() is not None
