"""Shared pytest fixtures.

Through Phase 4, this file also installed `sys.modules` stubs for
`tkinter`/`pystray` so `claude_usage_tray.app` — and, transitively, the pure
helpers still living inside it at the time — could be imported on this
display-less Linux dev/CI box. Phase 5 (tasks.md 5.4) extracted the last
such helper (`icons.py`); no test imports `claude_usage_tray.app` anymore,
so the stub scaffolding was deleted here. `test_import_hygiene.py` and
`test_layering.py` guarantee it never becomes necessary again: every pure
module stays provably free of `tkinter`/`pystray`, transitively.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def app_data_dir(tmp_path, monkeypatch):
    """Isolated app-data root for tests touching paths/config/jsonstore.

    Points `claude_usage_tray.platform.app_data_root` at a throwaway
    `tmp_path` directory so no test ever reads or writes the real
    `%APPDATA%\\ClaudeUsage` (or `~/.config`) location.
    """
    root = tmp_path / "app-data-root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("claude_usage_tray.platform.app_data_root", lambda: root)
    return root
