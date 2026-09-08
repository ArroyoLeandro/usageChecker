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
    parse_windows,
    save_budget,
    sweep_stale,
    to_mapping,
    used_percent_for,
)
from claude_usage_tray.quotas import QUOTA_WINDOWS


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

# --- a ceiling that watches only some windows ------------------------------
# The 5-hour window refills four times a day and is meant to be spent; the
# weekly one is the money. An owner who caps "the week" and is stopped every
# afternoon by the session window has been given a cap they did not ask for.


def _payload(session, weekly):
    return {
        "session": {"utilization": session},
        "weekly": {"utilization": weekly},
        "weekly_fable": {"utilization": 3.0},
    }


def test_used_percent_for_narrows_to_the_named_windows():
    payload = _payload(84.0, 17.0)
    assert used_percent_for(payload) == 84.0
    assert used_percent_for(payload, ("weekly", "weekly_fable")) == 17.0
    assert used_percent_for(payload, ("session",)) == 84.0


def test_used_percent_for_ignores_a_window_this_build_does_not_know():
    assert used_percent_for(_payload(84.0, 17.0), ("weekly", "monthly")) == 17.0


def test_a_selection_naming_no_measured_window_is_unknown_not_zero():
    payload = {"session": {"utilization": 84.0}}
    assert used_percent_for(payload, ("weekly",)) is None


def test_a_weekly_ceiling_does_not_block_on_a_full_session_window():
    weekly_only = Budget(ceiling_percent=60.0, windows=("weekly", "weekly_fable"))
    payload = _payload(84.0, 17.0)
    decision = evaluate(used_percent_for(payload, weekly_only.windows), weekly_only)
    assert decision.action == "allow"
    # ...and the same numbers under the default (all windows) DO block, which
    # is what makes the case above a measurement rather than a tautology.
    everything = Budget(ceiling_percent=60.0)
    assert evaluate(used_percent_for(payload, everything.windows), everything).action == "block"


def test_a_weekly_ceiling_still_blocks_when_the_week_fills():
    weekly_only = Budget(ceiling_percent=60.0, windows=("weekly", "weekly_fable"))
    payload = _payload(10.0, 61.0)
    assert evaluate(used_percent_for(payload, weekly_only.windows), weekly_only).action == "block"


def test_a_budget_written_before_windows_existed_watches_all_of_them():
    restored = from_mapping({"ceiling_percent": 60.0})
    assert restored is not None
    assert restored.windows == QUOTA_WINDOWS


def test_a_corrupt_window_list_widens_to_every_window_rather_than_none():
    # Narrowing is the deliberate act. A malformed field must not silently
    # turn a weekly cap into no cap at all.
    for raw in ("weekly", 7, [], ["nonsense"], None):
        assert parse_windows(raw) == QUOTA_WINDOWS


def test_a_stored_selection_round_trips():
    original = Budget(ceiling_percent=60.0, windows=("weekly", "weekly_fable"))
    restored = from_mapping(to_mapping(original))
    assert restored is not None
    assert restored.windows == ("weekly", "weekly_fable")


# --- the resumed-session alias ----------------------------------------------
# A resumed session keeps its original conversation id -- the one
# `hooks/gate.py` reads from its event payload -- but Claude Code spawns a new
# MCP server process for it, with `CLAUDE_CODE_SESSION_ID` set to a different
# value. `set_usage_budget` only ever sees that environment id. Without this
# mechanism, a ceiling written from a resumed session is filed under an id
# the gate never looks up. `record_alias`/`_resolve_alias` are what
# `hooks/gate.py`'s `session_of` calls to close that gap; these tests cover
# them directly, independent of the hook that drives them in practice.


def test_record_alias_is_a_noop_when_the_ids_already_agree(app_data_dir):
    """The common, non-resumed case: nothing to translate, so nothing should
    be written -- an `aliases/` directory must not appear for a session that
    was never resumed."""
    budget_module.record_alias("same-id", "same-id")

    assert list(budget_module.paths.alias_dir().glob("*.json")) == []


@pytest.mark.parametrize("env_id, event_id", [("", "event-id"), ("env-id", ""), ("", "")])
def test_record_alias_is_a_noop_when_either_id_is_blank(app_data_dir, env_id, event_id):
    budget_module.record_alias(env_id, event_id)

    assert list(budget_module.paths.alias_dir().glob("*.json")) == []


def test_resolve_alias_follows_a_recorded_mapping(app_data_dir):
    budget_module.record_alias("env-id", "event-id")

    assert budget_module._resolve_alias("env-id") == "event-id"


def test_resolve_alias_falls_back_to_the_environment_id_when_none_is_recorded(app_data_dir):
    """The overwhelmingly common case: no alias was ever recorded for this
    id, so there is nothing to follow -- today's pre-alias behaviour."""
    assert budget_module._resolve_alias("no-alias-recorded-for-this-id") == "no-alias-recorded-for-this-id"


def test_a_corrupt_alias_file_falls_back_to_the_environment_id_rather_than_raising(app_data_dir):
    """Fail open, same as a corrupt budget file: a hand-mangled diagnostic
    mapping must not become a gate that blocks every prompt with a
    traceback."""
    target = budget_module.paths.alias_file("env-id")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ not json", encoding="utf-8")

    assert budget_module._resolve_alias("env-id") == "env-id"


def test_save_budget_written_under_the_environment_id_lands_where_the_alias_points(app_data_dir, monkeypatch):
    """The writer's half of the fix, independent of the hook that records
    the alias in practice: once a mapping is on record, `save_budget` given
    no explicit `session_id` -- exactly how `mcp_server/server.py`'s
    `set_usage_budget` calls it -- converges on the event id, not the
    environment id it read from `os.environ`."""
    monkeypatch.setenv(SESSION_ENV, "env-id")
    budget_module.record_alias("env-id", "event-id")

    assert save_budget(Budget(ceiling_percent=95.0)) is True

    assert budget_module.paths.budget_file("event-id").exists()
    assert not budget_module.paths.budget_file("env-id").exists()
    assert load_budget("event-id").ceiling_percent == 95.0


def test_clear_budget_written_under_the_environment_id_clears_through_the_alias(app_data_dir, monkeypatch):
    monkeypatch.setenv(SESSION_ENV, "env-id-2")
    budget_module.record_alias("env-id-2", "event-id-2")
    save_budget(Budget(ceiling_percent=70.0))

    assert clear_budget() is True
    assert load_budget("event-id-2") is None


def test_an_id_with_no_alias_recorded_is_used_exactly_as_before(app_data_dir, session):
    """The unresumed case: environment id and event id are the same value,
    so there is no alias to record and none to follow -- `save_budget` and
    `load_budget` behave exactly as they did before this mechanism existed."""
    save_budget(Budget(ceiling_percent=70.0))

    assert load_budget(session).ceiling_percent == 70.0
    assert budget_module.paths.budget_file(session).exists()


def test_stale_aliases_are_swept_alongside_budgets(app_data_dir):
    """Without this, `aliases/` would grow one dead file per *resumed*
    session, forever -- exactly what `sweep_stale` already prevents for
    `budgets/`."""
    import os

    budget_module.record_alias("env-old", "event-old")
    stale = budget_module.paths.alias_file("env-old")
    os.utime(stale, (0, 0))

    removed = sweep_stale()

    assert removed == 1
    assert not stale.exists()


def test_the_sweep_spares_aliases_still_in_use(app_data_dir):
    budget_module.record_alias("env-fresh", "event-fresh")
    fresh = budget_module.paths.alias_file("env-fresh")

    assert sweep_stale() == 0
    assert fresh.exists()


def test_the_sweep_covers_a_stale_budget_and_a_stale_alias_in_one_pass(app_data_dir):
    import os

    save_budget(Budget(ceiling_percent=70.0), "old-budget-session")
    budget_module.record_alias("env-old-2", "event-old-2")
    stale_budget = budget_module.paths.budget_file("old-budget-session")
    stale_alias = budget_module.paths.alias_file("env-old-2")
    os.utime(stale_budget, (0, 0))
    os.utime(stale_alias, (0, 0))

    assert sweep_stale() == 2
    assert not stale_budget.exists()
    assert not stale_alias.exists()


def test_recording_an_unchanged_alias_does_not_rewrite_the_file(app_data_dir):
    """`session_of` reaches `record_alias` once per prompt *and* once per
    tool call, so a resumed session records the same mapping thousands of
    times. Re-writing it on each of those would put a temp-file-plus-rename
    and a `sweep_stale()` directory walk in front of every tool call, to
    persist a fact that has not changed since the first one.
    """
    import os

    budget_module.record_alias("env-hot", "event-hot")
    target = budget_module.paths.alias_file("env-hot")
    os.utime(target, (0, 0))
    before = target.stat().st_mtime

    budget_module.record_alias("env-hot", "event-hot")

    assert target.stat().st_mtime == before
    assert budget_module._resolve_alias("env-hot") == "event-hot"


def test_a_changed_alias_is_still_written_through(app_data_dir):
    """The skip above is keyed on the mapping being identical, not on a file
    merely existing -- an id that genuinely starts meaning something else
    must still be recorded."""
    budget_module.record_alias("env-moved", "event-first")
    budget_module.record_alias("env-moved", "event-second")

    assert budget_module._resolve_alias("env-moved") == "event-second"


def test_an_unwritable_alias_store_fails_open_instead_of_raising(app_data_dir, monkeypatch):
    """An exception escaping `record_alias` would be raised out of
    `session_of` on every single tool call. The module docstring is
    unambiguous about which failure is the recoverable one: a lost alias
    costs an override that lands on the wrong id -- no worse than before the
    mechanism existed -- while a raising gate costs the user their session.
    """

    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(budget_module.jsonstore, "write_json_atomic", _refuse)

    budget_module.record_alias("env-unwritable", "event-unwritable")

    assert budget_module._resolve_alias("env-unwritable") == "env-unwritable"


def test_record_alias_fails_open_when_mkdir_raises(app_data_dir, monkeypatch):
    """The write step is not the only I/O `record_alias` performs -- the
    `mkdir` immediately before it can fail too, and used to sit outside any
    guard that would catch it. It now shares the same total `try` as the
    write and the skip-if-unchanged read, so a `mkdir` failure must be
    exactly as harmless as the write failure covered above."""

    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(budget_module.Path, "mkdir", _refuse)

    budget_module.record_alias("env-mkdir-fails", "event-mkdir-fails")

    assert budget_module._resolve_alias("env-mkdir-fails") == "env-mkdir-fails"


def test_record_alias_fails_open_on_a_non_oserror_exception_from_the_write_path(app_data_dir, monkeypatch):
    """Pre-hardening, `record_alias` caught only `OSError` around the write.
    A `ValueError` -- or anything else -- escaping `jsonstore.write_json_atomic`
    (a bug in it, or a test/monkeypatch standing in for one) would have
    propagated straight out of `record_alias` and, from there, out of
    `session_of` on the next tool call. The guard now catches `Exception`,
    so the type of what goes wrong no longer matters."""

    def _refuse(*args, **kwargs):
        raise ValueError("not an OSError, on purpose")

    monkeypatch.setattr(budget_module.jsonstore, "write_json_atomic", _refuse)

    budget_module.record_alias("env-non-oserror", "event-non-oserror")

    assert budget_module._resolve_alias("env-non-oserror") == "env-non-oserror"


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "..", "....//....//x", "/etc/shadow", "a\\..\\..\\b", "."],
)
def test_an_alias_id_cannot_escape_the_alias_directory(app_data_dir, hostile):
    """Mirrors `test_a_session_id_cannot_escape_the_budget_directory` above:
    `alias_file` reuses the same `safe_session_token` allowlist as
    `budget_file`, so the same guarantee -- one path component, inside the
    directory, no traversal -- has to hold here too. (What a collision under
    that allowlist means for an alias, rather than whether traversal is
    prevented, is the separate concern `paths.alias_file`'s docstring now
    calls out; this test is only about escaping the directory.)"""
    alias_dir = budget_module.paths.alias_dir()

    budget_module.record_alias(hostile, "event-for-a-hostile-environment-id")

    written = list(alias_dir.glob("*.json"))
    assert len(written) == 1
    target = written[0]
    assert target.parent.resolve() == alias_dir.resolve()
    assert target.name == target.parts[-1]  # one component, no separators
    assert alias_dir.resolve() in target.resolve().parents


# --- Flaw 1: an alias must apply only to an environment id, never to an ----
# ---         id a caller already names explicitly --------------------------
# `_resolve`, pre-fix, ran `_resolve_alias` on *every* id it settled on,
# explicit or environment. `hooks/gate.py`'s `session_of` hands the
# conversation id -- the authoritative one, read straight from the event
# payload -- to `load_budget` as an *explicit* argument. If that id has ever
# also served as some other session's environment id and gotten aliased
# (ids on this machine are drawn from one pool, so this is not far-fetched),
# the pre-fix code would silently redirect it to whatever that alias points
# at instead of trusting the id it was given.


def test_an_alias_is_not_applied_to_an_explicitly_named_session_id(app_data_dir):
    """FAILS against the pre-fix `_resolve`, which ran every id -- explicit
    or environment -- through `_resolve_alias` unconditionally. Here,
    `conv-A` is a real conversation id with a real ceiling on record; only
    *afterwards* does it happen to also get used as some other session's
    environment id and end up aliased to `conv-B`. An explicit
    `load_budget("conv-A")` must still return `conv-A`'s own ceiling --
    not `conv-B`'s file, which does not even exist. Pre-fix, the ceiling
    silently stops enforcing: `load_budget("conv-A")` returns `None` and the
    gate allows every turn."""
    save_budget(Budget(ceiling_percent=70.0), "conv-A")
    # `conv-A` is later reused as an environment id and aliased away --
    # unrelated to, and after, the ceiling above being set.
    budget_module.record_alias("conv-A", "conv-B")

    restored = load_budget("conv-A")

    assert restored is not None
    assert restored.ceiling_percent == 70.0


def test_an_alias_for_one_environment_id_leaves_an_unrelated_sessions_ceiling_untouched(
    app_data_dir, monkeypatch
):
    """Flaw 1's blast radius at its worst: 'one session overwrites
    another's cap'. An alias on record for environment id `session-A-env`
    (because it once needed translating to `session-S1`'s conversation id)
    must not leak into a completely unrelated session `session-B`, which has
    no alias recorded for it at all -- not on read, and not on write."""
    monkeypatch.setenv(SESSION_ENV, "session-S1")
    save_budget(Budget(ceiling_percent=70.0))
    budget_module.record_alias("session-A-env", "session-S1")

    monkeypatch.setenv(SESSION_ENV, "session-B")
    assert load_budget() is None  # not redirected onto session-S1's ceiling
    assert save_budget(Budget(ceiling_percent=99.0)) is True

    assert load_budget("session-S1").ceiling_percent == 70.0  # untouched
    monkeypatch.setenv(SESSION_ENV, "session-B")
    assert load_budget().ceiling_percent == 99.0  # session-B kept its own


# --- Flaw 2: single-hop resolution reopens the bug on a chained resume -----
# A session resumed twice in a row leaves two links on record: `E1 -> C`
# from the first resume, then `E2 -> E1` from the second. A writer holding
# only `E2` has to cross both hops to land on `C`, the id `hooks/gate.py`
# actually reads budgets under -- stopping after one hop resolves `E2` to
# `E1` and writes a file nothing reads.


def test_a_writer_holding_the_first_hop_of_a_chain_still_lands_on_the_true_terminal_id(
    app_data_dir, monkeypatch
):
    """FAILS against the pre-fix, single-hop `_resolve_alias`: it would
    resolve `E2` to `E1` and write `budgets/E1.json`, which the gate -- which
    reads under `C` -- never looks up."""
    budget_module.record_alias("E1", "C")
    budget_module.record_alias("E2", "E1")

    monkeypatch.setenv(SESSION_ENV, "E2")
    assert save_budget(Budget(ceiling_percent=95.0)) is True

    assert budget_module.paths.budget_file("C").exists()
    assert not budget_module.paths.budget_file("E1").exists()
    assert load_budget("C").ceiling_percent == 95.0


def test_resolve_alias_follows_a_chain_of_more_than_one_hop(app_data_dir):
    """The same scenario as the writer-level test above, isolated to the
    resolver itself: `_resolve_alias` must cross every recorded hop, not
    just the first one."""
    budget_module.record_alias("E1", "C")
    budget_module.record_alias("E2", "E1")

    assert budget_module._resolve_alias("E2") == "C"


def test_resolve_alias_terminates_on_a_cycle_instead_of_looping_forever(app_data_dir):
    """A corrupted or hand-edited pair of alias files can point at each
    other. Following that chain must terminate -- returning the last id
    actually reached before the loop would repeat -- rather than recursing
    or looping without bound, which would turn a diagnostic mapping failure
    into a hung or crashing gate."""
    budget_module.record_alias("cycle-A", "cycle-B")
    budget_module.record_alias("cycle-B", "cycle-A")

    assert budget_module._resolve_alias("cycle-A") == "cycle-B"
    assert budget_module._resolve_alias("cycle-B") == "cycle-A"


def test_resolve_alias_fails_open_when_the_chain_exceeds_the_hop_bound(app_data_dir):
    """A chain deeper than `_MAX_ALIAS_HOPS` -- implausible for a real
    resume history, but not impossible for a corrupted map that keeps
    producing new ids without ever cycling back to one already seen --
    must still terminate, returning wherever resolution had gotten to
    rather than reading indefinitely."""
    hop_count = budget_module._MAX_ALIAS_HOPS + 5
    ids = [f"hop-{i}" for i in range(hop_count + 1)]
    for env_id, event_id in zip(ids, ids[1:]):
        budget_module.record_alias(env_id, event_id)

    result = budget_module._resolve_alias(ids[0])

    assert result == ids[budget_module._MAX_ALIAS_HOPS]
    assert result != ids[-1]  # the true terminal id was never reached
