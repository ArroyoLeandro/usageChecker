"""claude_usage_tray.mcp_server -- the usage data, exposed to an AI agent.

A second frontend over the same L2/L3 core the tray uses (`config.py`,
`api.py`), not a second implementation of it. The tray renders the usage for
a human looking at an icon; this package renders it for a model deciding
whether it can afford another turn. Neither knows about the other, and the
fetch/backoff/rate-limit logic is written once, in `api.py`.

Layer 5, alongside `app.py`: both are entry points that compose lower
layers, and the layering gate therefore forbids an edge in either direction
between them -- which is the property that keeps this package from quietly
growing a dependency on the tray's runtime.

Deliberately free of third-party dependencies. `server.py` speaks the MCP
stdio protocol with `json` and `sys` rather than the `mcp` SDK so the server
starts under a bare system interpreter, with no virtualenv and no install
step. See that module's docstring for the full reasoning.
"""

from __future__ import annotations
