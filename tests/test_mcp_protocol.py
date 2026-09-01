"""The MCP wire, driven end to end over in-memory pipes.

No SDK backs this server, so the handshake is ours to get right and ours to
regress. Each test below is a way a client silently drops the connection:
answering a notification, writing a non-frame to stdout, or dying on a
malformed line.
"""

from __future__ import annotations

import io
import json

import pytest

from claude_usage_tray.mcp_server import jsonrpc, server


def _drive(frames: list[dict]) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames))
    stdout = io.StringIO()
    jsonrpc.serve(server.HANDLERS, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line]


def _request(request_id, method, **params) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def test_initialize_echoes_the_protocol_version_the_client_asked_for():
    """This server has no version-specific behaviour, so refusing a version
    it would in fact have spoken correctly is a self-inflicted break."""
    replies = _drive([_request(1, "initialize", protocolVersion="2024-11-05")])

    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[0]["result"]["serverInfo"]["name"] == "claude-usage"
    assert "tools" in replies[0]["result"]["capabilities"]


def test_initialize_without_a_version_falls_back_to_a_known_one():
    replies = _drive([_request(1, "initialize")])

    assert replies[0]["result"]["protocolVersion"] == server.DEFAULT_PROTOCOL_VERSION


def test_the_initialized_notification_is_never_answered():
    """JSON-RPC forbids replying to a frame with no id, and this is the
    exact notification every client sends right after `initialize` -- so
    answering it breaks the handshake of a server that otherwise looks
    perfectly healthy."""
    replies = _drive(
        [
            _request(1, "initialize"),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request(2, "tools/list"),
        ]
    )

    assert [reply["id"] for reply in replies] == [1, 2]


def test_the_usage_tool_takes_only_optional_arguments():
    """The zero-argument call is the intended one: the server already knows
    which account launched it. Any `required` field here would force every
    caller to answer a question it has no business needing to answer."""
    replies = _drive([_request(1, "tools/list")])

    tools = {tool["name"]: tool for tool in replies[0]["result"]["tools"]}
    schema = tools["get_claude_usage"]["inputSchema"]
    assert set(schema["properties"]) == {"config_dir", "force_refresh"}
    assert "required" not in schema


def test_a_tool_call_returns_both_text_and_structured_content(monkeypatch):
    report = {"ok": True, "summary": "5h 25% · 7d 66%", "highest_used_percent": 66.0}
    monkeypatch.setattr(server, "get_usage", lambda *_args, **_kwargs: report)

    replies = _drive([_request(1, "tools/call", name="get_claude_usage", arguments={})])

    result = replies[0]["result"]
    assert result["structuredContent"] == report
    assert json.loads(result["content"][0]["text"]) == report
    assert result["isError"] is False


def test_tool_arguments_reach_get_usage():
    seen = {}

    def fake_get_usage(config_dir=None, *, force_refresh=False):
        seen.update(config_dir=config_dir, force_refresh=force_refresh)
        return {"ok": True}

    original = server.get_usage
    server.get_usage = fake_get_usage
    try:
        _drive(
            [
                _request(
                    1,
                    "tools/call",
                    name="get_claude_usage",
                    arguments={"config_dir": "~/.claude-work", "force_refresh": True},
                )
            ]
        )
    finally:
        server.get_usage = original

    assert seen == {"config_dir": "~/.claude-work", "force_refresh": True}


def test_a_failed_report_is_a_tool_error_not_a_protocol_error(monkeypatch):
    """A logged-out account is a well-formed answer the model should read.
    Raising it to the JSON-RPC layer would hide the message behind a
    transport failure."""
    monkeypatch.setattr(
        server,
        "get_usage",
        lambda *_args, **_kwargs: {"ok": False, "error": "session expired", "auth_error": True},
    )

    replies = _drive([_request(1, "tools/call", name="get_claude_usage", arguments={})])

    assert "error" not in replies[0]
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["structuredContent"]["error"] == "session expired"


def test_an_unknown_tool_is_reported_without_dropping_the_connection():
    replies = _drive([_request(1, "tools/call", name="get_something_else", arguments={})])

    assert replies[0]["result"]["isError"] is True
    assert "unknown tool" in replies[0]["result"]["content"][0]["text"]


def test_an_unknown_method_is_a_method_not_found_error():
    replies = _drive([_request(1, "resources/list")])

    assert replies[0]["error"]["code"] == jsonrpc.METHOD_NOT_FOUND


def test_a_malformed_line_is_answered_and_the_loop_survives_it():
    stdin = io.StringIO("not json\n\n" + json.dumps(_request(2, "ping")) + "\n")
    stdout = io.StringIO()

    jsonrpc.serve(server.HANDLERS, stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line]

    assert replies[0]["error"]["code"] == jsonrpc.PARSE_ERROR
    assert replies[1]["id"] == 2


def test_a_raising_handler_becomes_an_error_frame_not_a_dead_server():
    handlers = dict(server.HANDLERS, boom=lambda _params: 1 / 0)
    stdin = io.StringIO(json.dumps(_request(1, "boom")) + "\n" + json.dumps(_request(2, "ping")) + "\n")
    stdout = io.StringIO()

    jsonrpc.serve(handlers, stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line]

    assert replies[0]["error"]["code"] == jsonrpc.INTERNAL_ERROR
    assert replies[1]["id"] == 2


def test_every_frame_written_is_valid_json_on_its_own_line():
    """The invariant that makes stdout usable as a transport: one frame per
    line, nothing else. A stray print anywhere in the package would corrupt
    the stream with an error that points nowhere near its cause."""
    stdin = io.StringIO(json.dumps(_request(1, "initialize")) + "\n")
    stdout = io.StringIO()

    jsonrpc.serve(server.HANDLERS, stdin=stdin, stdout=stdout)

    for line in stdout.getvalue().splitlines():
        assert json.loads(line)["jsonrpc"] == "2.0"


def test_get_usage_reports_a_missing_config_directory_instead_of_querying(tmp_path, monkeypatch):
    """Calling the API with credentials that cannot exist wastes a request
    and returns a vaguer error than 'that directory is not there'."""
    monkeypatch.setattr(
        server.api,
        "fetch_usage",
        lambda *_args, **_kwargs: pytest.fail("must not reach the network"),
    )

    result = server.get_usage(str(tmp_path / "nope"))

    assert result["ok"] is False
    assert result["auth_error"] is True
    assert "No Claude config directory" in result["error"]


def test_get_usage_never_shells_out_to_refresh_a_token(tmp_path, monkeypatch):
    """Refreshing runs `claude update` as a subprocess. Reasonable for a
    tray the user is looking at; not for a background server spawned by
    another Claude."""
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    captured = {}

    def fake_fetch(profile, *, try_refresh=True, force=False):
        captured.update(try_refresh=try_refresh, force=force, config_dir=profile.config_dir)
        return {"error": "no session", "auth_error": True}

    monkeypatch.setattr(server.api, "fetch_usage", fake_fetch)

    server.get_usage(str(tmp_path), force_refresh=True)

    assert captured["try_refresh"] is False
    assert captured["force"] is True
    assert captured["config_dir"] == tmp_path
