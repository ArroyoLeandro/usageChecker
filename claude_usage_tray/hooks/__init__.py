"""claude_usage_tray.hooks -- the usage ceiling, enforced by the harness.

The MCP server tells an agent where the quota stands; nothing about its
answer is binding, and an agent that decides to keep working keeps working.
This package is the other half: a hook the *harness* runs, outside the
model's reach, which refuses a turn once the account passes a ceiling the
user set. The model does not see it coming and cannot argue with it.

Layer 5, peer to `app.py` and `mcp_server/`. The gate therefore cannot
import the MCP server, which is why the one number both of them turn on --
`budget.highest_used_percent` -- lives at L2 where both can reach it.

Every path through this package fails open. See `budget.py`'s docstring for
why that is not laziness: a gate that blocks on its own errors locks the
user out of the session they would need in order to unblock it.
"""

from __future__ import annotations
