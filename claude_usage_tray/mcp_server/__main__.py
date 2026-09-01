"""Entry point: `python3 -m claude_usage_tray.mcp_server`.

Separate from `server.py` so importing the handlers in a test never risks
starting a read loop on the test runner's stdin.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
