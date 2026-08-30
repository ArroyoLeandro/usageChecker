"""Entry point: `python3 -m claude_usage_tray.hooks`.

Reads one hook event as JSON on stdin and writes at most one JSON object on
stdout. Always exits 0 -- see `gate.py` on why the gate never fails closed.
"""

from __future__ import annotations

import sys

from .gate import main

if __name__ == "__main__":
    sys.exit(main())
