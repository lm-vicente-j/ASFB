"""The MCP server: protocol replies through handle() and serve(), and the four tools end to end."""

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from asfb.mcp import server
from asfb.session import Session
from tests.junit_samples import KEY


def _request(method: str, params: object = None, request_id: object = 1) -> bytes:
    message: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message).encode("utf-8")


def _call(session: Session, name: str, arguments: object) -> dict[str, Any]:
    reply = server.handle(session, _request("tools/call", {"name": name, "arguments": arguments}))
    assert reply is not None
    result: dict[str, Any] = json.loads(reply)["result"]
    return result


@pytest.fixture
def session(tmp_path: Path) -> Session:
    site = tmp_path / "site"
    site.mkdir()
    (site / "page.html").write_text("<p>Hola Ana</p>", encoding="utf-8")
    return Session([str(site)], key=KEY, python=sys.executable, run_network="host")


def test_initialize_echoes_a_known_version_and_defaults_otherwise(session: Session) -> None:
    known = json.loads(
        server.handle(session, _request("initialize", {"protocolVersion": "2025-03-26"})) or b""
    )
    assert known["result"]["protocolVersion"] == "2025-03-26"
    assert known["result"]["serverInfo"]["name"] == "asfb"
    unknown = json.loads(
        server.handle(session, _request("initialize", {"protocolVersion": "1999"})) or b""
    )
    assert unknown["result"]["protocolVersion"] == "2025-06-18"


def test_notifications_get_no_reply(session: Session) -> None:
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    assert server.handle(session, note) is None


def test_ping_and_tools_list(session: Session) -> None:
    assert json.loads(server.handle(session, _request("ping")) or b"")["result"] == {}
    listed = json.loads(server.handle(session, _request("tools/list")) or b"")["result"]["tools"]
    assert [tool["name"] for tool in listed] == ["sanitize", "edit", "run", "diff"]
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in listed)


def test_sanitize_directory_and_file(session: Session, tmp_path: Path) -> None:
    listing = _call(session, "sanitize", {"target": "."})
    assert listing["isError"] is False
    assert listing["content"][0]["text"].startswith("d_")
    file_result = _call(session, "sanitize", {"target": "page.html"})
    text = file_result["content"][0]["text"]
    assert text.splitlines()[0].startswith("d_")
    assert "@1" in text.splitlines()[0]


def test_edit_end_to_end_then_diff(session: Session) -> None:
    ref = _call(session, "sanitize", {"target": "page.html"})["content"][0]["text"].splitlines()[0]
    edited = _call(session, "edit", {"doc": ref, "old": "<p>", "new": "<h2>"})
    assert edited["isError"] is False
    new_text = edited["content"][0]["text"].splitlines()
    assert new_text[0].endswith("@2")
    assert new_text[1].startswith("<h2>")
    diffed = _call(session, "diff", {"a": ref, "b": new_text[0]})
    assert diffed["isError"] is False
    assert "-<p>" in diffed["content"][0]["text"]
    assert "+<h2>" in diffed["content"][0]["text"]


def test_edit_refuses_a_stale_reference(session: Session) -> None:
    ref = _call(session, "sanitize", {"target": "page.html"})["content"][0]["text"].splitlines()[0]
    _call(session, "edit", {"doc": ref, "old": "<p>", "new": "<h2>"})
    stale = _call(session, "edit", {"doc": ref, "old": "<h2>", "new": "<p>"})
    assert stale["content"][0]["text"] == "E_EDIT_STALE"


def test_run_returns_the_summary(session: Session) -> None:
    result = _call(session, "run", {"tool": "pytest", "targets": ["."]})
    assert result["content"][0]["text"].startswith("pytest exit=")


@pytest.mark.parametrize(
    "arguments",
    [
        {"target": "page.html", "extra": 1},
        {},
        {"target": 5},
        {"target": "x" * 4097},
        ["not", "an", "object"],
    ],
)
def test_bad_arguments_give_e_mcp_args(session: Session, arguments: object) -> None:
    result = _call(session, "sanitize", arguments)
    assert result == {"content": [{"type": "text", "text": "E_MCP_ARGS"}], "isError": True}


def test_unknown_method_and_bad_call_params(session: Session) -> None:
    unknown = json.loads(server.handle(session, _request("nope")) or b"")
    assert unknown["error"]["code"] == -32601
    bad = json.loads(server.handle(session, _request("tools/call", {"name": "zzz"})) or b"")
    assert bad["error"]["code"] == -32602


def test_invalid_json_gets_a_null_id_parse_error(session: Session) -> None:
    reply = json.loads(server.handle(session, b"{not json") or b"")
    assert reply == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }


def test_batch_and_non_object_are_invalid_requests(session: Session) -> None:
    for raw in (b"[1,2]", b"42", b'{"id":1,"method":"ping"}'):
        reply = json.loads(server.handle(session, raw) or b"")
        assert reply["error"]["code"] == -32600


def test_duplicate_keys_are_a_parse_error(session: Session) -> None:
    raw = b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}'
    assert json.loads(server.handle(session, raw) or b"")["error"]["code"] == -32700


def test_serve_answers_lines_in_order_and_refuses_an_over_long_line(session: Session) -> None:
    lines = [_request("ping", request_id=1), _request("ping", request_id=2)]
    stdin = io.BytesIO(b"\n".join(lines) + b"\n" + b"x" * (server.MAX_LINE + 10) + b"\n")
    stdout = io.BytesIO()
    assert server.serve(session, stdin, stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [reply.get("id") for reply in replies] == [1, 2, None]
    assert replies[2]["error"]["code"] == -32600


def test_serve_writes_nothing_to_stderr(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    server.serve(session, io.BytesIO(b"{bad\n"), io.BytesIO())
    assert capsys.readouterr().err == ""
