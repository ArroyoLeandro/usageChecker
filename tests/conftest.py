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


#: Every provider must honour one environment variable naming its config
#: directory -- `providers._claude` reads `CLAUDE_CONFIG_DIR`,
#: `providers._codex` reads `CODEX_HOME`. Adding an adapter means adding its
#: variable here, so that the isolation below stays complete by construction.
PROVIDER_HOME_ENV = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}


@pytest.fixture(autouse=True)
def isolated_provider_dirs(tmp_path, monkeypatch):
    """Point every provider's default config directory at a path that does
    not exist, for every test.

    Autouse and unconditional, because provider auto-detection reads the
    *machine*: without this, `load_config()`'s first-run seed picks up
    whichever CLIs the developer happens to have installed, so the same test
    passes on a box with no `~/.codex` and fails on one with it. That is
    exactly the failure this fixture was written in response to.

    Tests that want a provider detected opt in by setting its variable
    themselves -- a `monkeypatch.setenv` in the test body runs after this
    fixture and therefore wins.
    """
    for provider_id, variable in PROVIDER_HOME_ENV.items():
        monkeypatch.setenv(variable, str(tmp_path / f"absent-{provider_id}"))


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
