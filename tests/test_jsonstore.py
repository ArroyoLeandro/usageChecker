"""Unit tests for claude_usage_tray.jsonstore — durability primitives.

All headless (`tmp_path`), no display server, no network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from claude_usage_tray import jsonstore


def test_write_then_read_round_trips(tmp_path):
    target = tmp_path / "config.json"
    jsonstore.write_json_atomic(target, {"profiles": [], "schema_version": 2})
    assert jsonstore.read_json(target) == {"profiles": [], "schema_version": 2}


def test_read_json_returns_none_for_missing_file(tmp_path):
    assert jsonstore.read_json(tmp_path / "nope.json") is None


def test_read_json_raises_corrupt_file_for_invalid_json(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(jsonstore.CorruptFile) as excinfo:
        jsonstore.read_json(target)
    assert excinfo.value.path == target


def test_read_json_raises_corrupt_file_for_undecodable_bytes(tmp_path):
    # Regression: this used to escape as UnicodeDecodeError, which is
    # neither CorruptFile nor OSError -- so it sailed past every caller's
    # corruption policy and took the app down at startup, before any window
    # existed to report it. A file that is not valid UTF-8 is corrupt.
    target = tmp_path / "config.json"
    target.write_bytes(b'{"profiles": "\xff\xfe"}')
    with pytest.raises(jsonstore.CorruptFile) as excinfo:
        jsonstore.read_json(target)
    assert excinfo.value.path == target


def test_temp_file_is_created_in_same_directory_as_target(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    seen_dirs = []
    original_mkstemp = jsonstore.tempfile.mkstemp

    def spying_mkstemp(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(jsonstore.tempfile, "mkstemp", spying_mkstemp)
    jsonstore.write_json_atomic(target, {"a": 1})
    assert seen_dirs == [str(tmp_path)]


def test_temp_file_is_removed_on_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    jsonstore.write_json_atomic(target, {"a": 1})  # establish a valid prior file

    def boom(*args, **kwargs):
        raise ValueError("simulated json.dump failure")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(ValueError):
        jsonstore.write_json_atomic(target, {"b": 2})

    leftover_temp_files = list(tmp_path.glob("*.tmp"))
    assert leftover_temp_files == []
    # The prior valid file must be untouched by the failed write.
    assert jsonstore.read_json(target) == {"a": 1}


def test_os_replace_overwrites_existing_target(tmp_path):
    target = tmp_path / "config.json"
    jsonstore.write_json_atomic(target, {"a": 1})
    jsonstore.write_json_atomic(target, {"a": 2})
    assert jsonstore.read_json(target) == {"a": 2}


def test_quarantine_preserves_bytes_exactly(tmp_path):
    target = tmp_path / "config.json"
    original_bytes = b"{not valid json at all"
    target.write_bytes(original_bytes)

    quarantine_path = jsonstore.quarantine(target)

    assert quarantine_path.read_bytes() == original_bytes


def test_quarantine_frees_the_original_path(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{broken", encoding="utf-8")

    jsonstore.quarantine(target)

    assert not target.exists()


def test_quarantine_does_not_collide_across_repeats(tmp_path):
    target = tmp_path / "config.json"

    target.write_text("{broken 1", encoding="utf-8")
    first = jsonstore.quarantine(target)

    target.write_text("{broken 2", encoding="utf-8")
    second = jsonstore.quarantine(target)

    assert first != second
    assert first.exists()
    assert second.exists()
    assert first.read_text() == "{broken 1"
    assert second.read_text() == "{broken 2"


def test_quarantine_uses_utc_iso_style_timestamp_in_name(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{broken", encoding="utf-8")

    fixed_now = datetime(2026, 7, 16, 18, 0, 0, tzinfo=timezone.utc)
    quarantine_path = jsonstore.quarantine(target, now=fixed_now)

    assert quarantine_path.name.startswith("config.json.corrupt-20260716T180000")
