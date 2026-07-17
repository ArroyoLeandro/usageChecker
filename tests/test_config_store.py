"""Unit tests for claude_usage_tray.config — profile model + durable store.

Exercises the profile-management and config-store specs directly: these
tests assert *desired* behavior (uuid identity, array order, no reserved
"principal", derived supports_refresh, corruption policy), not the old
code's characterized behavior -- the old code's bugs are exactly what this
rewrite removes. All headless (`tmp_path` via the `app_data_dir` fixture),
no display server, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_usage_tray import config, jsonstore, paths


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exists: bool = True) -> Path:
    claude_dir = tmp_path / "ambient-claude"
    if exists:
        claude_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    return claude_dir


def _profile(id_: str, name: str, config_dir: Path) -> config.ClaudeProfile:
    return config.ClaudeProfile(id=id_, name=name, config_dir=config_dir)


# ---------------------------------------------------------------------------
# Stable Opaque Identity
# ---------------------------------------------------------------------------


def test_uuid_identity_survives_name_and_path_edits(tmp_path):
    profiles = [_profile("abc-123", "Work", tmp_path / "a")]
    edited = config.edit_profile(profiles, "abc-123", name="Personal", config_dir=tmp_path / "b")
    assert edited[0].id == "abc-123"
    assert edited[0].name == "Personal"
    assert edited[0].config_dir == tmp_path / "b"


def test_new_profile_gets_a_fresh_uuid_distinct_from_existing(tmp_path):
    existing = [_profile("abc-123", "Work", tmp_path / "a")]
    updated = config.add_profile(existing, name="Personal", config_dir=tmp_path / "b")
    new_profile = updated[-1]
    assert new_profile.id != "abc-123"
    assert len(new_profile.id) > 0
    # uuid4 round-trips through the stdlib parser without raising.
    import uuid

    uuid.UUID(new_profile.id)


# ---------------------------------------------------------------------------
# First-Run Seed / re-seed
# ---------------------------------------------------------------------------


def test_first_run_seed_detects_and_persists_one_profile(tmp_path, monkeypatch, app_data_dir):
    claude_dir = _seed_claude_dir(tmp_path, monkeypatch, exists=True)

    result = config.load_config()

    assert len(result.config.profiles) == 1
    assert result.config.profiles[0].config_dir == claude_dir
    assert result.notices == []
    assert result.read_only is False
    # Persisted: a second load reads the same profile back, not a fresh seed.
    reloaded = config.load_config()
    assert reloaded.config.profiles[0].id == result.config.profiles[0].id


def test_first_run_no_detectable_dir_starts_empty_without_error(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)

    result = config.load_config()

    assert result.config.profiles == []
    assert result.notices == []
    assert result.read_only is False


def test_reseed_after_list_emptied(tmp_path, monkeypatch, app_data_dir):
    claude_dir = _seed_claude_dir(tmp_path, monkeypatch, exists=True)
    seeded = config.load_config()
    assert len(seeded.config.profiles) == 1

    # User deletes every profile and saves.
    config.save_config(config.AppConfig(profiles=[]))

    reseeded = config.load_config()
    assert len(reseeded.config.profiles) == 1
    assert reseeded.config.profiles[0].config_dir == claude_dir
    # Re-seeding mints a fresh id -- it is not the same profile resurrected.
    assert reseeded.config.profiles[0].id != seeded.config.profiles[0].id


# ---------------------------------------------------------------------------
# Array Order Is Display Order / no reserved index or id
# ---------------------------------------------------------------------------


def test_reorder_persists_across_reload(tmp_path, monkeypatch, app_data_dir):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    a, b, c = _profile("a", "A", tmp_path / "a"), _profile("b", "B", tmp_path / "b"), _profile("c", "C", tmp_path / "c")
    config.save_config(config.AppConfig(profiles=[a, b, c]))

    reordered = config.reorder_profiles([a, b, c], ["c", "a", "b"])
    config.save_config(config.AppConfig(profiles=reordered))

    reloaded = config.load_config()
    assert [p.id for p in reloaded.config.profiles] == ["c", "a", "b"]


def test_seeded_profile_at_index_zero_is_an_ordinary_entry(tmp_path, monkeypatch, app_data_dir):
    claude_dir = _seed_claude_dir(tmp_path, monkeypatch, exists=True)
    seeded = config.load_config().config.profiles

    other = _profile("other", "Other", tmp_path / "other")
    combined = [*seeded, other]

    reordered = config.reorder_profiles(combined, ["other", seeded[0].id])
    assert reordered[0].id == "other"

    deleted = config.delete_profile(combined, seeded[0].id)
    assert deleted == [other]


# ---------------------------------------------------------------------------
# Full CRUD
# ---------------------------------------------------------------------------


def test_edit_persists(tmp_path, monkeypatch, app_data_dir):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    profile = _profile("a", "A", tmp_path / "old")
    config.save_config(config.AppConfig(profiles=[profile]))

    edited = config.edit_profile([profile], "a", config_dir=tmp_path / "new")
    config.save_config(config.AppConfig(profiles=edited))

    reloaded = config.load_config()
    assert reloaded.config.profiles[0].config_dir == tmp_path / "new"


def test_delete_last_remaining_profile_yields_empty_list_no_error(tmp_path):
    profile = _profile("a", "A", tmp_path / "a")
    result = config.delete_profile([profile], "a")
    assert result == []


def test_edit_profile_raises_if_id_not_found(tmp_path):
    with pytest.raises(config.ProfileNotFoundError):
        config.edit_profile([], "missing", name="X")


# ---------------------------------------------------------------------------
# config_dir Uniqueness Is Validation, Not Identity
# ---------------------------------------------------------------------------


def test_duplicate_path_rejected_on_add(tmp_path):
    existing = [_profile("a", "A", tmp_path / "shared")]
    with pytest.raises(config.DuplicateConfigDirError):
        config.add_profile(existing, name="B", config_dir=tmp_path / "SHARED")


def test_duplicate_path_rejected_on_edit(tmp_path):
    existing = [
        _profile("a", "A", tmp_path / "one"),
        _profile("b", "B", tmp_path / "two"),
    ]
    with pytest.raises(config.DuplicateConfigDirError):
        config.edit_profile(existing, "b", config_dir=tmp_path / "One")


def test_deletion_always_resolves_by_id_never_by_path(tmp_path):
    a = _profile("a", "A", tmp_path / "same")
    b = _profile("b", "B", tmp_path / "same-but-different-id-holder")
    result = config.delete_profile([a, b], "a")
    assert result == [b]


# ---------------------------------------------------------------------------
# supports_refresh Is Derived, Never Stored
# ---------------------------------------------------------------------------


def test_supports_refresh_true_when_config_dir_matches_default(tmp_path, monkeypatch):
    claude_dir = tmp_path / "the-default"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    profile = _profile("a", "A", claude_dir)
    assert profile.supports_refresh is True


def test_supports_refresh_flips_false_after_editing_config_dir(tmp_path, monkeypatch):
    claude_dir = tmp_path / "the-default"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    profile = _profile("a", "A", claude_dir)
    assert profile.supports_refresh is True

    edited = config.edit_profile([profile], "a", config_dir=tmp_path / "elsewhere")[0]
    assert edited.supports_refresh is False


def test_supports_refresh_key_absent_from_persisted_json(tmp_path, monkeypatch, app_data_dir):
    claude_dir = tmp_path / "the-default"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    profile = _profile("a", "A", claude_dir)
    config.save_config(config.AppConfig(profiles=[profile]))

    raw = json.loads(paths.config_file().read_text(encoding="utf-8"))
    assert "supports_refresh" not in raw["profiles"][0]


# ---------------------------------------------------------------------------
# Corrupt config.json: preserved, surfaced, read_only-on-quarantine-failure
# ---------------------------------------------------------------------------


def test_corrupt_config_is_quarantined_notice_surfaced_then_reseeded(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=True)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = b"{not valid json at all"
    target.write_bytes(original_bytes)

    result = config.load_config()

    assert len(result.notices) == 1
    notice = result.notices[0]
    assert isinstance(notice, config.ConfigCorrupted)
    assert notice.quarantined_to is not None
    assert notice.quarantined_to.read_bytes() == original_bytes
    assert result.read_only is False
    # Only after quarantine+notice does it proceed to the first-run seed.
    assert len(result.config.profiles) == 1


def test_corrupt_config_survives_the_subsequent_reseed_and_save(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=True)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = b"{not valid json at all"
    target.write_bytes(original_bytes)

    result = config.load_config()
    quarantined_path = result.notices[0].quarantined_to

    # The reseed already wrote a fresh config.json (task 6.3's "persist").
    assert target.exists()
    assert quarantined_path.exists()
    assert quarantined_path.read_bytes() == original_bytes


def test_undecodable_config_is_quarantined_like_any_other_corruption(tmp_path, monkeypatch, app_data_dir):
    # The mirror of the alert-state case: config.json is irreplaceable, so
    # undecodable bytes must still reach quarantine rather than escaping as
    # a UnicodeDecodeError that crashes startup and preserves nothing.
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"profiles": "\xff\xfe"}'
    target.write_bytes(original)

    result = config.load_config()

    notice = result.notices[0]
    assert isinstance(notice, config.ConfigCorrupted)
    assert notice.quarantined_to is not None
    assert notice.quarantined_to.read_bytes() == original, "the corrupt bytes must survive intact"


def test_schema_invalid_config_is_treated_as_corrupt(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Valid JSON, but no "profiles" list -- fails schema validation.
    target.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

    result = config.load_config()

    assert len(result.notices) == 1
    assert isinstance(result.notices[0], config.ConfigCorrupted)
    assert result.config.profiles == []


def test_quarantine_failure_yields_read_only_and_no_writes(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=True)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"{broken")

    def boom(path, *, now=None):
        raise OSError("simulated: cannot rename, file locked")

    monkeypatch.setattr(jsonstore, "quarantine", boom)
    monkeypatch.setattr(config.jsonstore, "quarantine", boom)

    result = config.load_config()

    assert result.read_only is True
    assert result.config.profiles == []
    assert result.notices == [config.ConfigCorrupted(quarantined_to=None)]
    # Nothing was written -- the original corrupt bytes are untouched.
    assert target.read_bytes() == b"{broken"


# ---------------------------------------------------------------------------
# Legacy Files Are Ignored
# ---------------------------------------------------------------------------


def test_legacy_profiles_file_is_never_read_or_deleted(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)
    legacy = tmp_path / "claude_usage_profiles.json"
    legacy.write_text(json.dumps({"profiles": [{"name": "Legacy", "config_dir": str(tmp_path)}]}), encoding="utf-8")

    result = config.load_config()

    # The legacy schema (no "id") would fail our schema validation if ever
    # read, and it isn't even in the canonical location -- so first-run
    # behaves as if no config exists at all.
    assert result.config.profiles == []
    assert legacy.exists()
    assert legacy.read_text(encoding="utf-8") == json.dumps(
        {"profiles": [{"name": "Legacy", "config_dir": str(tmp_path)}]}
    )


# ---------------------------------------------------------------------------
# Headless Testability
# ---------------------------------------------------------------------------


def test_headless_round_trip(tmp_path, monkeypatch, app_data_dir):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    profiles = [_profile("a", "A", tmp_path / "a"), _profile("b", "B", tmp_path / "b")]
    config.save_config(config.AppConfig(profiles=profiles))

    result = config.load_config()

    assert [(p.id, p.name, p.config_dir) for p in result.config.profiles] == [
        ("a", "A", tmp_path / "a"),
        ("b", "B", tmp_path / "b"),
    ]


# ---------------------------------------------------------------------------
# Settings live under config.json's `settings` key (user-settings spec)
#
# `config.py` carries the blob raw and uninterpreted -- `settings.py` owns
# that schema, and the layering gate forbids a same-layer edge between them.
# These tests therefore assert *passthrough and preservation*, which is all
# this module promises; the schema itself is tested in test_settings.py.
# ---------------------------------------------------------------------------


def test_settings_blob_round_trips_through_the_store(tmp_path, monkeypatch, app_data_dir):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    blob = {"theme": "light", "font_size": 12, "window_size": "640x480", "alerts": {"enabled": False}}
    config.save_config(config.AppConfig(profiles=[_profile("a", "A", tmp_path / "a")], settings=blob))

    assert config.load_config().config.settings == blob


def test_settings_survive_a_profile_edit_in_the_same_save(tmp_path, monkeypatch, app_data_dir):
    # The user-settings spec's named scenario: "GIVEN a user has customized
    # theme and font size, WHEN a profile is added/edited/deleted, THEN the
    # settings values remain unchanged in the same save."
    import dataclasses

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    blob = {"theme": "light", "font_size": 14}
    loaded = config.AppConfig(profiles=[_profile("a", "A", tmp_path / "a")], settings=blob)

    for profiles in (
        config.add_profile(loaded.profiles, name="B", config_dir=tmp_path / "b"),
        config.edit_profile(loaded.profiles, "a", name="Renamed"),
        config.delete_profile(loaded.profiles, "a"),
    ):
        config.save_config(dataclasses.replace(loaded, profiles=profiles))
        assert config.load_config().config.settings == blob


def test_settings_survive_the_empty_list_reseed(tmp_path, monkeypatch, app_data_dir):
    # Regression: an empty profile list re-seeds by detection, but that says
    # nothing about the user's preferences -- deleting the last profile must
    # not silently reset their theme.
    claude_dir = _seed_claude_dir(tmp_path, monkeypatch)
    blob = {"theme": "light", "font_size": 14}
    config.save_config(config.AppConfig(profiles=[], settings=blob))

    result = config.load_config()

    assert result.config.settings == blob
    assert [p.config_dir for p in result.config.profiles] == [claude_dir], "the reseed itself still happened"
    assert json.loads(paths.config_file().read_text(encoding="utf-8"))["settings"] == blob


def test_settings_are_not_recovered_from_a_quarantined_config(tmp_path, monkeypatch, app_data_dir):
    # The mirror image: on the corruption path the settings are unreadable,
    # not merely unaccompanied, so defaults are the only honest answer.
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"schema_version": 2, "settings": {"theme": "light"}, "profiles": ', encoding="utf-8")

    result = config.load_config()

    assert result.config.settings == {}
    assert isinstance(result.notices[0], config.ConfigCorrupted)


def test_a_slice_1_config_with_no_settings_key_loads_as_an_empty_blob(tmp_path, monkeypatch, app_data_dir):
    # Forward compatibility: a config.json written before this slice existed.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema_version": 2, "profiles": [{"id": "a", "name": "A", "config_dir": str(tmp_path / "a")}]}),
        encoding="utf-8",
    )

    result = config.load_config()

    assert result.config.settings == {}
    assert result.notices == []
    assert [p.id for p in result.config.profiles] == ["a"]


@pytest.mark.parametrize("bogus", ["nonsense", [1, 2], 7, None])
def test_an_unusable_settings_key_degrades_instead_of_quarantining_the_profiles(
    tmp_path, monkeypatch, app_data_dir, bogus
):
    # Deliberate asymmetry inside config.json: a broken *profiles* array is
    # corruption (quarantine + notice), a broken *settings* value is not.
    # Losing hand-entered profiles over a mistyped preference would invert
    # the cost the two-file split exists to manage.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "profiles": [{"id": "a", "name": "A", "config_dir": str(tmp_path / "a")}],
                "settings": bogus,
            }
        ),
        encoding="utf-8",
    )

    result = config.load_config()

    assert result.notices == [], "a bad settings value must not quarantine the config"
    assert [p.id for p in result.config.profiles] == ["a"]
    assert result.config.settings == {}
    assert not list(target.parent.glob("*.corrupt-*"))


def test_saved_config_always_carries_a_settings_key(tmp_path, monkeypatch, app_data_dir):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-dir"))
    config.save_config(config.AppConfig(profiles=[_profile("a", "A", tmp_path / "a")]))

    payload = json.loads(paths.config_file().read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["settings"] == {}


# ---------------------------------------------------------------------------
# Single Canonical Location (no dual-location fallback)
# ---------------------------------------------------------------------------


def test_no_fallback_to_a_second_location_on_corruption(tmp_path, monkeypatch, app_data_dir):
    _seed_claude_dir(tmp_path, monkeypatch, exists=False)
    target = paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"{broken")

    # There is only one read/write path at all -- proven structurally: the
    # store never calls anything except paths.config_file(). A second
    # (legacy) file sitting right next to the corrupt one must not be
    # consulted.
    decoy = target.parent / "claude_usage_profiles.json"
    decoy.write_text(json.dumps({"profiles": []}), encoding="utf-8")

    result = config.load_config()
    assert result.notices and isinstance(result.notices[0], config.ConfigCorrupted)
