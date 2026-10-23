"""The MCP tools on a SQLite file: sanitize returns the censored view, the reference of a database
cannot be edited, and a PNG named .db comes back as a static error code."""

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from asfb.mcp import server
from asfb.session import Session
from tests.junit_samples import KEY
from tests.sqlite_images import SAMPLE_SCHEMA, image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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
    (site / "app.db").write_bytes(image(SAMPLE_SCHEMA))
    (site / "fake.db").write_bytes(PNG)
    return Session([str(site)], key=KEY, python=sys.executable, run_network="host")


def test_sanitize_a_database_returns_the_view_without_the_names(session: Session) -> None:
    result = _call(session, "sanitize", {"target": "app.db"})
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text.splitlines()[0].startswith("d_")
    assert "@1" in text.splitlines()[0]
    assert "db page_size=b13 wal=no" in text
    assert "clientes" not in text
    assert "a@x" not in text


def test_a_database_reference_cannot_be_edited(session: Session) -> None:
    ref = _call(session, "sanitize", {"target": "app.db"})["content"][0]["text"].splitlines()[0]
    edited = _call(session, "edit", {"doc": ref, "old": "x", "new": "y"})
    assert edited["isError"] is True
    assert edited["content"][0]["text"] == "E_EDIT_UNSUPPORTED_KIND"


def test_a_png_named_db_gives_a_static_code(session: Session) -> None:
    result = _call(session, "sanitize", {"target": "fake.db"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "E_DB_NOT_SQLITE"


def test_the_listing_of_the_root_names_the_database(session: Session) -> None:
    result = _call(session, "sanitize", {"target": "."})
    assert result["isError"] is False
    assert result["content"][0]["text"].startswith("d_")
    assert "clientes" not in result["content"][0]["text"]


def test_serve_answers_a_database_call_on_stdio(session: Session) -> None:
    request = _request("tools/call", {"name": "sanitize", "arguments": {"target": "app.db"}})
    stdout = io.BytesIO()
    assert server.serve(session, io.BytesIO(request + b"\n"), stdout) == 0
    reply = json.loads(stdout.getvalue().splitlines()[0])
    assert reply["result"]["isError"] is False
    assert "clientes" not in reply["result"]["content"][0]["text"]
