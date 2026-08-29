"""claude_usage_tray.accounts -- which Claude installation to report on.

The tray answers this by asking the user: it holds a list of profiles the
user typed in. A headless consumer has no user to ask -- it is spawned by a
Claude Code process, with no UI and no session of its own. So it answers
from the environment instead, and the environment turns out to already carry
the answer: Claude Code exports `CLAUDE_CONFIG_DIR` and the processes it
spawns inherit it. A server started by a Claude running on
`~/.claude-personal` therefore reports on `~/.claude-personal` with no
configuration at all.

**Why L3 rather than inside `mcp_server/`.** It started there, with one
caller. `hooks/` became the second -- and `hooks` and `mcp_server` are both
L5 entry points, so the layering gate forbids an edge between them, exactly
as it does for `alerts`/`settings` at L2. The shared answer had to fall to a
layer both can reach. It cannot go lower than L3: it depends on `config`
(L2), and same-layer edges are forbidden too.

The precedence below is ordered most-specific-wins, so that the zero-config
case is also the correct case:

1. the tool call's own `config_dir` argument -- an explicit question about a
   named account, which nothing ambient should override;
2. `CLAUDE_USAGE_CONFIG_DIR` -- a deliberate pin in the MCP server entry,
   for pointing a server at an account other than the one that launched it;
3. `CLAUDE_CONFIG_DIR` -- the calling Claude. The default that matters;
4. `~/.claude` -- the stock location, when nothing above is set.

`config.normalize_config_dir` does the parsing for all four, so a path typed
with a `~`, or pasted as the `.credentials.json` file itself, is accepted
here exactly as it is in the profiles window.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import ClaudeProfile, default_claude_dir, normalize_config_dir

#: Pin the server to one account regardless of who launched it.
OVERRIDE_ENV = "CLAUDE_USAGE_CONFIG_DIR"

#: Exported by Claude Code and inherited by the servers it spawns.
INHERITED_ENV = "CLAUDE_CONFIG_DIR"

PROFILE_ID = "mcp"


@dataclass(frozen=True)
class Resolution:
    """A config directory, and the reason it was the one chosen.

    `source` is reported back in every tool result on purpose. "Which
    account are these numbers from?" is the first thing that goes wrong with
    a multi-account setup, and a caller that can see *why* a directory was
    picked can diagnose a wrong answer without reading this file.
    """

    config_dir: Path
    source: str

    @property
    def exists(self) -> bool:
        return self.config_dir.is_dir()


def resolve_config_dir(requested: str | None = None) -> Resolution:
    """Apply the precedence above. Reads `os.environ` directly.

    Reading the real environment rather than an injected mapping is
    deliberate: `config.default_claude_dir` consults `CLAUDE_CONFIG_DIR`
    itself, so a fake mapping here would disagree with the fallback in
    exactly the case the fallback exists for. Tests monkeypatch the
    environment instead, which keeps one source of truth.
    """
    explicit = normalize_config_dir(requested)
    if explicit is not None:
        return Resolution(explicit, "argument")

    override = normalize_config_dir(os.environ.get(OVERRIDE_ENV))
    if override is not None:
        return Resolution(override, OVERRIDE_ENV)

    inherited = normalize_config_dir(os.environ.get(INHERITED_ENV))
    if inherited is not None:
        return Resolution(inherited, INHERITED_ENV)

    return Resolution(default_claude_dir(), "default")


def profile_for(resolution: Resolution) -> ClaudeProfile:
    """An ephemeral `ClaudeProfile` for `api.fetch_usage` to work against.

    Not persisted and never written to `config.json`: this server reads the
    user's Claude installation, it does not manage the tray's profile list.
    The id is the fixed sentinel `"mcp"` rather than a fresh uuid4 so that
    `api.py`'s per-profile cache and 429 backoff state -- keyed by config
    dir, but reached through this object -- stay stable across calls within
    one server process.
    """
    directory = resolution.config_dir
    return ClaudeProfile(
        id=PROFILE_ID,
        name=directory.name or str(directory),
        config_dir=directory,
    )
