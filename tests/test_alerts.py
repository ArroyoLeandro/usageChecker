"""Unit tests for claude_usage_tray.alerts — the edge-triggered state machine.

This is the prime test target the proposal names: `evaluate()` is pure (no
clock, no I/O, no mutation of its argument), so every rule in design.md's
transition table and every scenario in the usage-alerts spec is decidable
here, deterministically, with zero tkinter/pystray/PIL and zero display
server.

The table under test (design.md, "Alert evaluation is a pure function with
no clock"), applied per (profile, quota window), per poll:

  1. window missing / `resets_at` absent-or-null / `pct` None -> SKIP
  2. key UNSEEN                                 -> create, max_notified = 0
  3. canonical `resets_at` differs from stored  -> RE-ARM, max_notified = 0
  4. highest = max(t for t in thresholds if pct >= t, default=0)
     and highest > max_notified -> ONE event naming `highest`; store it
"""

from __future__ import annotations

import json

import pytest

from claude_usage_tray import alerts, jsonstore

THRESHOLDS = [80, 95]

WINDOW_A = "2026-07-01T00:00:00Z"
WINDOW_B = "2026-07-08T00:00:00Z"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _quota(pct: float | None, resets_at: str | None = WINDOW_A) -> dict:
    """One quota-window entry shaped exactly like `api._quota()` returns."""
    return {"utilization": pct, "resets_at": resets_at}


def _poll(pct: float | None, resets_at: str | None = WINDOW_A, window: str = "session") -> list[tuple[str, dict]]:
    """A one-profile poll touching a single quota window."""
    return [("p1", {window: _quota(pct, resets_at)})]


def _multi_poll(*pcts: float, resets_at: str | None = WINDOW_A) -> list[tuple[str, dict]]:
    """A poll over `p1`, `p2`, ... each reporting its own session pct."""
    return [
        (f"p{index}", {"session": _quota(pct, resets_at)}) for index, pct in enumerate(pcts, start=1)
    ]


def _entry(state: alerts.AlertState, profile_id: str = "p1", window: str = "session") -> dict:
    return state[alerts.state_key(profile_id, window)]


# ---------------------------------------------------------------------------
# Requirement: Edge-Triggered Per (Profile x Window x Threshold)
# ---------------------------------------------------------------------------


def test_first_crossing_fires_and_records_the_tier():
    state, events = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    assert len(events) == 1
    assert events[0] == alerts.AlertEvent(profile_id="p1", window="session", threshold=80, pct=80.0)
    assert _entry(state)["max_notified_threshold"] == 80


def test_repeated_polls_above_the_same_tier_do_not_re_fire():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    for pct in (82.0, 85.0, 89.0):
        state, events = alerts.evaluate(state, _poll(pct), THRESHOLDS)
        assert events == [], f"{pct}% re-fired the already-notified 80 tier"

    assert _entry(state)["max_notified_threshold"] == 80


def test_reaching_a_higher_configured_tier_fires_once_more():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(96.0), THRESHOLDS)

    assert [event.threshold for event in events] == [95]
    assert _entry(state)["max_notified_threshold"] == 95


def test_below_the_first_tier_nothing_fires_but_the_window_is_tracked():
    state, events = alerts.evaluate({}, _poll(12.0), THRESHOLDS)

    assert events == []
    assert _entry(state) == {"resets_at": "2026-07-01T00:00:00+00:00", "max_notified_threshold": 0}


def test_jump_past_two_tiers_emits_one_event_naming_the_highest():
    # design.md: "Rule 4 takes the max, not the set. No backlog, no burst."
    state, events = alerts.evaluate({}, _poll(97.0), THRESHOLDS)

    assert len(events) == 1
    assert events[0].threshold == 95
    assert _entry(state)["max_notified_threshold"] == 95


def test_each_quota_window_is_armed_independently():
    items = [("p1", {"session": _quota(96.0), "weekly": _quota(81.0), "weekly_fable": _quota(10.0)})]
    state, events = alerts.evaluate({}, items, THRESHOLDS)

    fired = {(event.window, event.threshold) for event in events}
    assert fired == {("session", 95), ("weekly", 80)}
    assert _entry(state, window="weekly_fable")["max_notified_threshold"] == 0


def test_each_profile_is_armed_independently():
    items = [("p1", {"session": _quota(85.0)}), ("p2", {"session": _quota(85.0)})]
    state, events = alerts.evaluate({}, items, THRESHOLDS)

    assert {event.profile_id for event in events} == {"p1", "p2"}

    # p1 is already notified; a brand-new p3 at the same pct still fires.
    state, events = alerts.evaluate(state, [("p1", {"session": _quota(85.0)}), ("p3", {"session": _quota(85.0)})], THRESHOLDS)
    assert [event.profile_id for event in events] == ["p3"]


# ---------------------------------------------------------------------------
# Requirement: Keyed by profile_id, Re-Arms on resets_at Change
# ---------------------------------------------------------------------------


def test_state_is_keyed_by_profile_id_and_quota_window():
    state, _ = alerts.evaluate({}, _poll(85.0), THRESHOLDS)

    assert list(state) == ["p1::session"]


def test_quota_window_rollover_re_arms_every_tier():
    state, _ = alerts.evaluate({}, _poll(96.0), THRESHOLDS)
    assert _entry(state)["max_notified_threshold"] == 95

    state, events = alerts.evaluate(state, _poll(81.0, WINDOW_B), THRESHOLDS)

    assert [event.threshold for event in events] == [80]
    assert _entry(state)["resets_at"] == "2026-07-08T00:00:00+00:00"
    assert _entry(state)["max_notified_threshold"] == 80


def test_path_repointed_to_another_account_re_arms_like_a_rollover():
    # profile_id is unchanged; only the observed resets_at differs, because
    # config_dir now points at a different account. No special-case code.
    state, _ = alerts.evaluate({}, _poll(96.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(96.0, WINDOW_B), THRESHOLDS)

    assert [event.threshold for event in events] == [95]


@pytest.mark.parametrize(
    "encoding",
    ["2026-07-01T12:00:00Z", "2026-07-01T12:00:00+00:00", "2026-07-01T14:00:00+02:00"],
)
def test_equivalent_resets_at_encodings_canonicalize_to_one_window(encoding):
    # Without canonicalization these compare unequal and spuriously re-arm.
    state, _ = alerts.evaluate({}, _poll(85.0, "2026-07-01T12:00:00Z"), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(85.0, encoding), THRESHOLDS)

    assert events == [], f"{encoding} was treated as a different window"


def test_unparseable_resets_at_falls_back_to_the_raw_string_and_is_still_stable():
    state, _ = alerts.evaluate({}, _poll(85.0, "not-a-timestamp"), THRESHOLDS)
    assert _entry(state)["resets_at"] == "not-a-timestamp"

    state, events = alerts.evaluate(state, _poll(88.0, "not-a-timestamp"), THRESHOLDS)
    assert events == [], "an unchanged unparseable resets_at must not re-arm"


# ---------------------------------------------------------------------------
# Requirement: Absent resets_at Leaves State Untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_absent_resets_at_neither_fires_nor_mutates_an_existing_entry(missing):
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    before = json.dumps(state, sort_keys=True)

    state, events = alerts.evaluate(state, _poll(99.0, missing), THRESHOLDS)

    assert events == []
    assert json.dumps(state, sort_keys=True) == before


def test_absent_resets_at_does_not_create_an_entry():
    state, events = alerts.evaluate({}, _poll(99.0, None), THRESHOLDS)

    assert state == {}
    assert events == []


def test_transient_failure_then_recovery_does_not_spuriously_re_fire():
    # The spec's named scenario, end to end.
    state, events = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    assert len(events) == 1

    state, events = alerts.evaluate(state, [("p1", {"error": "No se pudo conectar"})], THRESHOLDS)
    assert events == []
    assert _entry(state)["max_notified_threshold"] == 80

    state, events = alerts.evaluate(state, _poll(82.0), THRESHOLDS)
    assert events == [], "recovery at 82% re-fired an already-notified 80 tier"


def test_missing_window_key_is_skipped_not_created():
    state, events = alerts.evaluate({}, [("p1", {"session": _quota(85.0)})], THRESHOLDS)

    assert events[0].window == "session"
    assert alerts.state_key("p1", "weekly") not in state
    assert alerts.state_key("p1", "weekly_fable") not in state


def test_null_utilization_is_skipped():
    state, events = alerts.evaluate({}, _poll(None), THRESHOLDS)

    assert state == {}
    assert events == []


# ---------------------------------------------------------------------------
# Requirement: Drop-and-Rise Within the Same Window
# ---------------------------------------------------------------------------


def test_drop_then_rise_to_the_same_tier_does_not_re_fire():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(60.0), THRESHOLDS)
    assert events == []
    assert _entry(state)["max_notified_threshold"] == 80, "falling usage must never re-arm"

    state, events = alerts.evaluate(state, _poll(85.0), THRESHOLDS)
    assert events == [], "85% maps to the already-notified 80 tier"


def test_drop_then_rise_past_a_new_tier_fires_for_that_tier():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    state, _ = alerts.evaluate(state, _poll(60.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(96.0), THRESHOLDS)

    assert [event.threshold for event in events] == [95]
    assert _entry(state)["max_notified_threshold"] == 95


# ---------------------------------------------------------------------------
# Purity: evaluate() must not mutate its argument
# ---------------------------------------------------------------------------


def test_evaluate_never_mutates_the_state_it_is_given():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    snapshot = json.dumps(state, sort_keys=True)

    new_state, _ = alerts.evaluate(state, _poll(96.0), THRESHOLDS)

    assert json.dumps(state, sort_keys=True) == snapshot, "evaluate() mutated its input"
    assert new_state is not state
    assert _entry(new_state)["max_notified_threshold"] == 95


def test_unchanged_poll_returns_an_equal_state_so_the_caller_can_skip_the_write():
    # design.md's persistence cadence: app.py writes iff new_state != state.
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    new_state, events = alerts.evaluate(state, _poll(80.0), THRESHOLDS)

    assert events == []
    assert new_state == state


# ---------------------------------------------------------------------------
# Thresholds edited mid-window (falls out of rule 4, no special case)
# ---------------------------------------------------------------------------


def test_adding_a_lower_tier_mid_window_fires_nothing():
    state, _ = alerts.evaluate({}, _poll(85.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(85.0), [50, 80, 95])

    assert events == []


def test_adding_a_higher_tier_mid_window_can_fire():
    state, _ = alerts.evaluate({}, _poll(92.0), [80])
    state, events = alerts.evaluate(state, _poll(92.0), [80, 90])

    assert [event.threshold for event in events] == [90]


def test_empty_threshold_list_fires_nothing_but_still_tracks_the_window():
    state, events = alerts.evaluate({}, _poll(99.0), [])

    assert events == []
    assert _entry(state)["max_notified_threshold"] == 0


def test_unsorted_thresholds_still_pick_the_highest_crossed_tier():
    state, events = alerts.evaluate({}, _poll(97.0), [95, 80])

    assert [event.threshold for event in events] == [95]


# ---------------------------------------------------------------------------
# prune / invalidate_profile
# ---------------------------------------------------------------------------


def test_prune_drops_entries_for_profiles_absent_from_config():
    items = [("p1", {"session": _quota(85.0)}), ("p2", {"session": _quota(85.0)})]
    state, _ = alerts.evaluate({}, items, THRESHOLDS)

    pruned = alerts.prune(state, {"p1"})

    assert list(pruned) == ["p1::session"]
    assert len(state) == 2, "prune() must not mutate its argument"


def test_prune_keeps_every_window_of_a_known_profile():
    items = [("p1", {"session": _quota(85.0), "weekly": _quota(85.0)})]
    state, _ = alerts.evaluate({}, items, THRESHOLDS)

    assert alerts.prune(state, {"p1"}) == state


def test_prune_with_no_known_profiles_empties_the_state():
    state, _ = alerts.evaluate({}, _poll(85.0), THRESHOLDS)

    assert alerts.prune(state, set()) == {}


def test_invalidate_profile_drops_only_that_profiles_entries():
    items = [("p1", {"session": _quota(85.0)}), ("p2", {"session": _quota(85.0)})]
    state, _ = alerts.evaluate({}, items, THRESHOLDS)

    invalidated = alerts.invalidate_profile(state, "p1")

    assert list(invalidated) == ["p2::session"]
    assert len(state) == 2, "invalidate_profile() must not mutate its argument"


def test_invalidated_profile_re_arms_on_the_next_poll():
    state, _ = alerts.evaluate({}, _poll(85.0), THRESHOLDS)
    state = alerts.invalidate_profile(state, "p1")
    state, events = alerts.evaluate(state, _poll(85.0), THRESHOLDS)

    assert [event.threshold for event in events] == [80]


# ---------------------------------------------------------------------------
# Requirement: Persisted Across Restarts
# ---------------------------------------------------------------------------


def test_state_round_trips_through_disk(tmp_path):
    path = tmp_path / "alert-state.json"
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    alerts.save_state(path, state)

    assert alerts.load_state(path) == state


def test_restart_does_not_re_notify_an_already_announced_tier(tmp_path):
    path = tmp_path / "alert-state.json"
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)
    alerts.save_state(path, state)

    # "Restart": state comes back from disk, not from an empty dict.
    reloaded = alerts.load_state(path)
    _, events = alerts.evaluate(reloaded, _poll(85.0), THRESHOLDS)

    assert events == []


def test_saved_state_uses_the_documented_schema(tmp_path):
    path = tmp_path / "alert-state.json"
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    alerts.save_state(path, state)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["entries"]["p1::session"] == {
        "resets_at": "2026-07-01T00:00:00+00:00",
        "max_notified_threshold": 80,
    }


def test_save_state_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "alert-state.json"
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    alerts.save_state(path, state)

    assert [item.name for item in tmp_path.iterdir()] == ["alert-state.json"]


def test_missing_state_file_loads_as_empty(tmp_path):
    assert alerts.load_state(tmp_path / "nope.json") == {}


# ---------------------------------------------------------------------------
# Requirement: Corrupt alert-state.json Is Discarded Without Backup
#
# The deliberate asymmetry vs config.json: no quarantine, no notice, no
# backup file. Asserted on the directory listing, which is where the
# asymmetry is actually observable.
# ---------------------------------------------------------------------------


def test_corrupt_state_file_is_discarded_silently_without_a_backup(tmp_path):
    path = tmp_path / "alert-state.json"
    path.write_text("{not json at all", encoding="utf-8")

    assert alerts.load_state(path) == {}
    assert [item.name for item in tmp_path.iterdir()] == ["alert-state.json"], (
        "alert-state.json must NOT be quarantined -- that is config.json's policy"
    )
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_corrupt_state_file_is_overwritten_by_the_next_save_without_preserving_it(tmp_path):
    path = tmp_path / "alert-state.json"
    path.write_text("{not json at all", encoding="utf-8")

    state = alerts.load_state(path)
    state, _ = alerts.evaluate(state, _poll(80.0), THRESHOLDS)
    alerts.save_state(path, state)

    assert [item.name for item in tmp_path.iterdir()] == ["alert-state.json"]
    assert alerts.load_state(path) == state


@pytest.mark.parametrize(
    "payload",
    [
        "[]",  # root is not an object
        '{"schema_version": 1}',  # no entries key
        '{"schema_version": 1, "entries": []}',  # entries is not an object
        '{"schema_version": 1, "entries": {"p1::session": "nope"}}',  # entry not an object
    ],
)
def test_schema_invalid_state_file_is_discarded_silently(tmp_path, payload):
    path = tmp_path / "alert-state.json"
    path.write_text(payload, encoding="utf-8")

    assert alerts.load_state(path) == {}
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_undecodable_state_file_is_discarded_silently(tmp_path):
    # Regression: load_state runs during app startup, so an escaping
    # UnicodeDecodeError here killed the app before any window existed.
    path = tmp_path / "alert-state.json"
    path.write_bytes(b'{"entries": "\xff\xfe"}')

    assert alerts.load_state(path) == {}
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_unreadable_state_file_is_discarded_silently(tmp_path, monkeypatch):
    path = tmp_path / "alert-state.json"
    path.write_text("{}", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(jsonstore, "read_json", boom)

    assert alerts.load_state(path) == {}


def test_load_state_tolerates_a_null_resets_at_entry(tmp_path):
    # The documented schema allows "resets_at": "<iso>|null".
    path = tmp_path / "alert-state.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "entries": {"p1::session": {"resets_at": None, "max_notified_threshold": 80}}}
        ),
        encoding="utf-8",
    )

    state = alerts.load_state(path)

    assert state == {"p1::session": {"resets_at": None, "max_notified_threshold": 80}}


def test_a_stored_null_resets_at_re_arms_on_the_next_real_window():
    state = {"p1::session": {"resets_at": None, "max_notified_threshold": 95}}
    state, events = alerts.evaluate(state, _poll(85.0), THRESHOLDS)

    assert [event.threshold for event in events] == [80]


# ---------------------------------------------------------------------------
# Requirement: Per-Profile Thresholds With a Global Default
#
# The state machine was already keyed by profile_id, so per-profile tiers add
# no key, no migration and no second state shape -- only a resolution step in
# front of rule 4. These tests exercise that resolution against the existing
# rules rather than restating them.
# ---------------------------------------------------------------------------


def test_a_profile_without_an_override_uses_the_global_list():
    state, events = alerts.evaluate({}, _poll(85.0), THRESHOLDS, thresholds_by_profile={"p2": [50]})

    assert [event.threshold for event in events] == [80]
    assert _entry(state)["max_notified_threshold"] == 80


def test_an_override_replaces_the_global_list_for_that_profile_only():
    state, events = alerts.evaluate(
        {}, _multi_poll(60.0, 60.0), THRESHOLDS, thresholds_by_profile={"p1": [50]}
    )

    # p1 crossed its own 50 tier; p2 is at 60 with the global [80, 95] and
    # must stay silent.
    assert [(event.profile_id, event.threshold) for event in events] == [("p1", 50)]
    assert _entry(state, "p2")["max_notified_threshold"] == 0


def test_an_override_does_not_replace_the_global_list_wholesale():
    # An override is not a global edit: p2 must keep firing on [80, 95].
    _, events = alerts.evaluate(
        {}, _multi_poll(60.0, 85.0), THRESHOLDS, thresholds_by_profile={"p1": [50]}
    )

    assert {(event.profile_id, event.threshold) for event in events} == {("p1", 50), ("p2", 80)}


def test_an_override_applies_to_every_quota_window_of_that_profile():
    items = [("p1", {"session": _quota(60.0), "weekly": _quota(60.0), "weekly_fable": _quota(10.0)})]
    _, events = alerts.evaluate({}, items, THRESHOLDS, thresholds_by_profile={"p1": [50]})

    assert {(event.window, event.threshold) for event in events} == {("session", 50), ("weekly", 50)}


def test_a_higher_override_suppresses_what_the_global_list_would_have_fired():
    _, events = alerts.evaluate({}, _poll(85.0), THRESHOLDS, thresholds_by_profile={"p1": [90]})

    assert events == [], "85% must not fire for a profile whose own first tier is 90"


def test_an_override_still_emits_one_event_naming_the_highest_tier_crossed():
    # Rule 4 is unchanged -- it simply reads a different tier list.
    state, events = alerts.evaluate({}, _poll(97.0), THRESHOLDS, thresholds_by_profile={"p1": [50, 60, 95]})

    assert [event.threshold for event in events] == [95]
    assert _entry(state)["max_notified_threshold"] == 95


def test_an_override_re_arms_on_a_window_rollover_like_any_other_profile():
    overrides = {"p1": [50]}
    state, _ = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile=overrides)
    state, events = alerts.evaluate(state, _poll(60.0, WINDOW_B), THRESHOLDS, thresholds_by_profile=overrides)

    assert [event.threshold for event in events] == [50]


def test_an_override_does_not_re_fire_an_already_notified_tier():
    overrides = {"p1": [50]}
    state, _ = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile=overrides)
    state, events = alerts.evaluate(state, _poll(70.0), THRESHOLDS, thresholds_by_profile=overrides)

    assert events == []


def test_an_override_honours_the_absent_resets_at_rule():
    overrides = {"p1": [50]}
    state, _ = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile=overrides)
    before = json.dumps(state, sort_keys=True)

    state, events = alerts.evaluate(state, _poll(99.0, None), THRESHOLDS, thresholds_by_profile=overrides)

    assert events == []
    assert json.dumps(state, sort_keys=True) == before


def test_an_override_honours_drop_and_rise():
    overrides = {"p1": [50]}
    state, _ = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile=overrides)
    state, _ = alerts.evaluate(state, _poll(20.0), THRESHOLDS, thresholds_by_profile=overrides)
    state, events = alerts.evaluate(state, _poll(55.0), THRESHOLDS, thresholds_by_profile=overrides)

    assert events == [], "falling usage must never re-arm, override or not"


def test_adding_an_override_mid_window_falls_out_of_rule_4_with_no_special_case():
    # Already notified at 80 on the global list; a new, lower personal tier
    # fires nothing, exactly as adding a lower global tier does.
    state, _ = alerts.evaluate({}, _poll(85.0), THRESHOLDS)
    state, events = alerts.evaluate(state, _poll(85.0), THRESHOLDS, thresholds_by_profile={"p1": [50, 80]})

    assert events == []


def test_adding_a_higher_override_tier_mid_window_can_fire():
    state, _ = alerts.evaluate({}, _poll(92.0), [80])
    state, events = alerts.evaluate(state, _poll(92.0), [80], thresholds_by_profile={"p1": [80, 90]})

    assert [event.threshold for event in events] == [90]


def test_removing_an_override_returns_the_profile_to_the_global_list():
    state, events = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile={"p1": [50]})
    assert [event.threshold for event in events] == [50]

    # The override is gone; 85% now maps to the global 80 tier, which is
    # higher than the notified 50, so it fires.
    state, events = alerts.evaluate(state, _poll(85.0), THRESHOLDS)
    assert [event.threshold for event in events] == [80]


def test_an_empty_override_map_is_exactly_the_pre_existing_behavior():
    # The migration guarantee, stated as a test: a config with no per-profile
    # tiers must produce byte-identical results to the old two-argument call.
    without = alerts.evaluate({}, _multi_poll(85.0, 96.0), THRESHOLDS)
    with_empty = alerts.evaluate({}, _multi_poll(85.0, 96.0), THRESHOLDS, thresholds_by_profile={})

    assert without == with_empty


def test_thresholds_by_profile_defaults_to_none_so_every_existing_call_site_is_unchanged():
    state_a, events_a = alerts.evaluate({}, _poll(85.0), THRESHOLDS)
    state_b, events_b = alerts.evaluate({}, _poll(85.0), THRESHOLDS, thresholds_by_profile=None)

    assert (state_a, events_a) == (state_b, events_b)


def test_an_override_for_an_unknown_profile_is_inert():
    _, events = alerts.evaluate({}, _poll(85.0), THRESHOLDS, thresholds_by_profile={"ghost": [10]})

    assert [event.threshold for event in events] == [80]


def test_an_override_is_tolerated_unsorted_and_duplicated_off_disk():
    _, events = alerts.evaluate({}, _poll(97.0), THRESHOLDS, thresholds_by_profile={"p1": [95, 50, 95]})

    assert [event.threshold for event in events] == [95]


def test_an_empty_override_list_fires_nothing_but_still_tracks_the_window():
    # Distinct from "no override": an explicitly empty tier list means this
    # profile has nothing to cross, which rule 4 already handles.
    state, events = alerts.evaluate({}, _poll(99.0), THRESHOLDS, thresholds_by_profile={"p1": []})

    assert events == []
    assert _entry(state)["max_notified_threshold"] == 0


def test_evaluate_with_overrides_still_never_mutates_its_state():
    overrides = {"p1": [50]}
    state, _ = alerts.evaluate({}, _poll(60.0), THRESHOLDS, thresholds_by_profile=overrides)
    snapshot = json.dumps(state, sort_keys=True)

    alerts.evaluate(state, _poll(96.0), THRESHOLDS, thresholds_by_profile=overrides)

    assert json.dumps(state, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# Requirement: Per-Profile, Per-Window Thresholds With a Global Default
#
# The gap this closes: this state machine has ALWAYS keyed on (profile,
# quota window) -- every test above relies on it -- while the config could
# only say one list per profile and apply it to all three. These are the
# tests for the half that was missing.
# ---------------------------------------------------------------------------


PER_WINDOW = {"session": [80, 95], "weekly": [80, 95], "weekly_fable": [80, 95]}


def _windows_poll(**pcts: float) -> list[tuple[str, dict]]:
    """A one-profile poll reporting a pct for each named quota window."""
    return [("p1", {window: _quota(pct) for window, pct in pcts.items()})]


def test_the_global_default_can_differ_per_window():
    thresholds = {"session": [50], "weekly": [80, 95], "weekly_fable": [99]}

    _, events = alerts.evaluate({}, _windows_poll(session=60.0, weekly=60.0, weekly_fable=60.0), thresholds)

    assert [(e.window, e.threshold) for e in events] == [("session", 50)]


def test_an_override_applies_only_to_the_window_it_names():
    # The override must not leak across windows -- the whole point of the split.
    _, events = alerts.evaluate(
        {},
        _windows_poll(session=60.0, weekly=60.0, weekly_fable=60.0),
        PER_WINDOW,
        thresholds_by_profile={"p1": {"session": [50]}},
    )

    assert [(e.window, e.threshold) for e in events] == [("session", 50)]


def test_different_windows_of_one_profile_can_hold_different_tiers():
    _, events = alerts.evaluate(
        {},
        _windows_poll(session=55.0, weekly_fable=65.0),
        PER_WINDOW,
        thresholds_by_profile={"p1": {"session": [50], "weekly_fable": [30, 60]}},
    )

    assert sorted((e.window, e.threshold) for e in events) == [
        ("session", 50),
        ("weekly_fable", 60),
    ]


def test_a_window_absent_from_an_override_inherits_that_windows_global_list():
    _, events = alerts.evaluate(
        {},
        _windows_poll(session=60.0, weekly=85.0),
        {"session": [80, 95], "weekly": [80, 95], "weekly_fable": [80, 95]},
        thresholds_by_profile={"p1": {"session": [50]}},
    )

    assert sorted((e.window, e.threshold) for e in events) == [
        ("session", 50),
        ("weekly", 80),  # inherited, not overridden
    ]


def test_a_window_still_accepts_multiple_tiers():
    thresholds = {"session": [80, 95], "weekly": [50, 80, 95], "weekly_fable": [80, 95]}
    state, events = alerts.evaluate({}, _windows_poll(weekly=55.0), thresholds)
    assert [e.threshold for e in events] == [50]

    state, events = alerts.evaluate(state, _windows_poll(weekly=85.0), thresholds)
    assert [e.threshold for e in events] == [80]

    state, events = alerts.evaluate(state, _windows_poll(weekly=96.0), thresholds)
    assert [e.threshold for e in events] == [95]


def test_a_per_window_override_obeys_the_hysteresis_rule():
    overrides = {"p1": {"session": [50]}}
    state, _ = alerts.evaluate({}, _poll(60.0), PER_WINDOW, thresholds_by_profile=overrides)
    state, _ = alerts.evaluate(state, _poll(20.0), PER_WINDOW, thresholds_by_profile=overrides)
    _, events = alerts.evaluate(state, _poll(55.0), PER_WINDOW, thresholds_by_profile=overrides)

    assert events == [], "an override changes which tiers exist, never the hysteresis"


def test_a_per_window_override_re_arms_on_a_window_change():
    overrides = {"p1": {"session": [50]}}
    state, _ = alerts.evaluate({}, _poll(60.0), PER_WINDOW, thresholds_by_profile=overrides)
    _, events = alerts.evaluate(
        state, _poll(60.0, WINDOW_B), PER_WINDOW, thresholds_by_profile=overrides
    )

    assert [e.threshold for e in events] == [50]


# --- A Bare Tier List Means "Every Window" ----------------------------------
#
# The same rule `settings.py` applies to a stored config, applied here too --
# so the state machine and the config loader cannot disagree about what a bare
# list means. Every test above this section passes a bare list and still
# passes, which is the broadest evidence of it.


def test_a_bare_global_list_means_the_same_tiers_in_every_window():
    _, events = alerts.evaluate({}, _windows_poll(session=85.0, weekly=85.0, weekly_fable=85.0), [80])

    assert sorted(e.window for e in events) == ["session", "weekly", "weekly_fable"]
    assert {e.threshold for e in events} == {80}


def test_a_bare_override_list_means_the_same_tiers_in_every_window():
    _, events = alerts.evaluate(
        {},
        _windows_poll(session=60.0, weekly=60.0, weekly_fable=60.0),
        PER_WINDOW,
        thresholds_by_profile={"p1": [50]},
    )

    assert sorted(e.window for e in events) == ["session", "weekly", "weekly_fable"]
    assert {e.threshold for e in events} == {50}


def test_a_bare_list_and_its_per_window_spelling_are_indistinguishable():
    poll = _windows_poll(session=85.0, weekly=85.0, weekly_fable=85.0)
    bare_state, bare_events = alerts.evaluate({}, poll, [80, 95])
    mapped_state, mapped_events = alerts.evaluate({}, poll, PER_WINDOW)

    assert bare_state == mapped_state
    assert bare_events == mapped_events


@pytest.mark.parametrize("bogus", ["80,95", None, 7, {"a": 1}])
def test_an_unreadable_global_thresholds_value_fires_nothing_rather_than_raising(bogus):
    # `settings.py` never produces this; a direct caller could. Firing nothing
    # is the safe answer -- raising would kill the poll thread.
    _, events = alerts.evaluate({}, _poll(99.0), bogus)

    assert events == []


def test_an_empty_override_list_is_not_the_same_as_inheriting():
    # `None` means inherit; `[]` means this window has no tiers and can never
    # fire. Collapsing them would make an override impossible to distinguish
    # from an absence.
    _, events = alerts.evaluate(
        {}, _poll(99.0), PER_WINDOW, thresholds_by_profile={"p1": {"session": []}}
    )

    assert events == []


# ---------------------------------------------------------------------------
# Change: repeat-alert cooldown (injected clock, per profile x window)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

_T = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_COOLDOWN = timedelta(minutes=60)


def test_first_crossing_records_last_notified_at_when_a_clock_is_injected():
    state, events = alerts.evaluate({}, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)

    assert [event.threshold for event in events] == [80]
    assert _entry(state)["last_notified_at"] == _T.isoformat()


def test_no_clock_records_no_last_notified_at_so_legacy_behaviour_is_unchanged():
    state, events = alerts.evaluate({}, _poll(80.0), THRESHOLDS)

    assert [event.threshold for event in events] == [80]
    assert "last_notified_at" not in _entry(state)


def test_a_rollover_repeat_within_the_cooldown_is_suppressed_then_allowed_after():
    # First crossing at T fires and stamps last_notified_at.
    state, events = alerts.evaluate(
        {}, _poll(80.0, resets_at=WINDOW_A), THRESHOLDS, now=_T, cooldown=_COOLDOWN
    )
    assert [event.threshold for event in events] == [80]

    # The window rolls over 10 min later with usage still above 80. Rule 3
    # re-arms (max_notified back to 0), but the cooldown suppresses the re-fire
    # and does NOT advance max_notified -- and last_notified_at survives the
    # rollover so the cooldown can span it.
    state, events = alerts.evaluate(
        state,
        _poll(82.0, resets_at=WINDOW_B),
        THRESHOLDS,
        now=_T + timedelta(minutes=10),
        cooldown=_COOLDOWN,
    )
    assert events == []
    assert _entry(state)["max_notified_threshold"] == 0
    assert _entry(state)["last_notified_at"] == _T.isoformat()

    # 70 min after the first notification the cooldown has elapsed; the same
    # rolled-over window fires again.
    state, events = alerts.evaluate(
        state,
        _poll(82.0, resets_at=WINDOW_B),
        THRESHOLDS,
        now=_T + timedelta(minutes=70),
        cooldown=_COOLDOWN,
    )
    assert [event.threshold for event in events] == [80]


def test_a_higher_tier_within_the_cooldown_is_delayed_not_lost():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)

    # A jump to 96 five minutes later would cross the 95 tier, but it is within
    # the cooldown: suppressed, and max_notified is NOT advanced, so it is not
    # lost.
    state, events = alerts.evaluate(
        state, _poll(96.0), THRESHOLDS, now=_T + timedelta(minutes=5), cooldown=_COOLDOWN
    )
    assert events == []
    assert _entry(state)["max_notified_threshold"] == 80

    # Once the cooldown has elapsed the still-high usage fires the 95 tier.
    state, events = alerts.evaluate(
        state, _poll(96.0), THRESHOLDS, now=_T + timedelta(minutes=61), cooldown=_COOLDOWN
    )
    assert [event.threshold for event in events] == [95]
    assert _entry(state)["max_notified_threshold"] == 95


def test_a_recent_notification_never_suppresses_a_different_profile_or_window():
    # p1/session was notified at T.
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)

    # 5 minutes later, within the cooldown: p1/session jumps to a higher tier
    # (suppressed -- same entry), while p2/session and p1/weekly cross for the
    # first time (must fire -- distinct entries the cooldown may not touch).
    items = [
        ("p1", {"session": _quota(96.0), "weekly": _quota(85.0)}),
        ("p2", {"session": _quota(90.0)}),
    ]
    _, events = alerts.evaluate(
        state, items, THRESHOLDS, now=_T + timedelta(minutes=5), cooldown=_COOLDOWN
    )

    fired = {(event.profile_id, event.window): event.threshold for event in events}
    assert fired.get(("p2", "session")) == 80
    assert fired.get(("p1", "weekly")) == 80
    assert ("p1", "session") not in fired


def test_a_missing_last_notified_at_means_never_notified_and_fires_immediately():
    # An entry as written before the cooldown existed: no last_notified_at.
    state = {
        alerts.state_key("p1", "session"): {
            "resets_at": "2026-07-01T00:00:00+00:00",
            "max_notified_threshold": 0,
        }
    }

    _, events = alerts.evaluate(state, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)

    assert [event.threshold for event in events] == [80]


def test_a_pre_cooldown_state_file_round_trips_and_fires_immediately(tmp_path):
    path = tmp_path / "alert-state.json"
    # A file written before last_notified_at existed round-trips unchanged
    # (no last_notified_at key materialized) and re-arms/fires normally.
    jsonstore.write_json_atomic(
        path,
        {
            "schema_version": 1,
            "entries": {
                "p1::session": {
                    "resets_at": "2026-07-01T00:00:00+00:00",
                    "max_notified_threshold": 0,
                }
            },
        },
    )

    loaded = alerts.load_state(path)
    assert "last_notified_at" not in loaded["p1::session"]

    _, events = alerts.evaluate(loaded, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)
    assert [event.threshold for event in events] == [80]


def test_last_notified_at_round_trips_through_disk(tmp_path):
    path = tmp_path / "alert-state.json"
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS, now=_T, cooldown=_COOLDOWN)
    assert state["p1::session"]["last_notified_at"] == _T.isoformat()

    alerts.save_state(path, state)

    assert alerts.load_state(path) == state


def test_a_zero_cooldown_disables_suppression():
    state, _ = alerts.evaluate({}, _poll(80.0), THRESHOLDS, now=_T, cooldown=timedelta(0))

    # Immediately re-arm within what would be a cooldown; with cooldown 0 there
    # is no suppression, so the rolled-over window fires again at once.
    _, events = alerts.evaluate(
        state,
        _poll(82.0, resets_at=WINDOW_B),
        THRESHOLDS,
        now=_T + timedelta(minutes=1),
        cooldown=timedelta(0),
    )
    assert [event.threshold for event in events] == [80]
