"""The MCP stdio wire: newline-delimited JSON-RPC 2.0 over stdin/stdout.

Transport only. It knows about frames, ids, notifications and error codes,
and nothing about quotas -- `server.py` supplies the handlers. Split out for
the usual reason the rest of this repo is split: the protocol loop has
awkward cases (a notification must not be answered, a malformed frame must
not kill the process) that are worth testing without a network call, and the
domain has cases worth testing without a pipe.

**The one rule that matters here: stdout belongs to the protocol.** A stray
`print`, a warning, a traceback -- anything else written to stdout corrupts
the frame stream and the client drops the connection with an error that
points nowhere near the cause. Diagnostics go to stderr, which the client
collects as server logs. That is why this module owns the only `sys.stdout`
write in the package.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Iterable, TextIO

#: JSON-RPC 2.0 reserved codes; MCP adds no codes of its own for this server.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

Handler = Callable[[dict[str, Any]], Any]


def log(message: str) -> None:
    """Diagnostics to stderr. Never stdout -- see the module docstring."""
    print(message, file=sys.stderr, flush=True)


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch(message: dict[str, Any], handlers: dict[str, Handler]) -> dict[str, Any] | None:
    """Route one decoded frame. Returns the reply, or None to stay silent.

    Silence is the correct response to a notification (a frame with no
    `id`): JSON-RPC forbids replying to one, and MCP's handshake sends
    `notifications/initialized` immediately after `initialize`. Answering it
    is the classic way to break a server that otherwise looks fine.
    """
    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")

    if not isinstance(method, str):
        return None if is_notification else _error(request_id, INVALID_REQUEST, "missing method")

    handler = handlers.get(method)
    if handler is None:
        if is_notification:
            return None
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    try:
        result = handler(message.get("params") or {})
    except Exception as exc:  # noqa: BLE001 -- a handler bug must not kill the loop
        log(f"handler for {method} raised: {exc!r}")
        return None if is_notification else _error(request_id, INTERNAL_ERROR, str(exc))

    return None if is_notification else _result(request_id, result)


def serve(handlers: dict[str, Handler], *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Read frames until EOF, dispatching each one.

    EOF is the normal shutdown: the client closed the pipe, so the server
    exits quietly rather than treating it as a failure.
    """
    source: Iterable[str] = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(sink, _error(None, PARSE_ERROR, "invalid JSON"))
            continue
        if not isinstance(message, dict):
            _write(sink, _error(None, INVALID_REQUEST, "frame is not an object"))
            continue
        reply = dispatch(message, handlers)
        if reply is not None:
            _write(sink, reply)


def _write(sink: TextIO, payload: dict[str, Any]) -> None:
    sink.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sink.flush()
