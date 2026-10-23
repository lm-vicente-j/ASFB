"""Python through the public surfaces: the CLI, the MCP tools, the vault suffix rules, and the
audit-free canary path of an edit. Every answer is checked for the canary and for the token shape.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from asfb.cli import main
from asfb.mcp import server
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey
from asfb.vault import paths
from tests.canary.harness import find_leaks, new_canary, variants

KEY = SessionKey(bytes(range(32)))
SOURCE = "def ingest(payload):\n    return payload\n"
TOKEN = re.compile(r"t_[0-9a-f]{16}")


def _request(method: str, params: object = None) -> bytes:
    body: dict[str, object] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return json.dumps(body).encode("utf-8")


def _call(session: Session, name: str, arguments: object) -> dict[str, Any]:
    reply = server.handle(session, _request("tools/call", {"name": name, "arguments": arguments}))
    assert reply is not None
    result: dict[str, Any] = json.loads(reply)["result"]
    return result


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text(SOURCE, encoding="utf-8")
    return root


def test_cli_sanitizes_a_python_file_as_json(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sanitize", str(project / "mod.py"), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_id"] == "asfb.code/1"
    assert document["ext"] == "py"


def test_cli_prints_the_python_view(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sanitize", str(project / "mod.py")]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith(".py")
    assert out.splitlines()[1].startswith("def t_")
    assert "ingest" not in out


def test_mcp_sanitize_and_edit_a_python_file(project: Path) -> None:
    session = Session([str(project)], key=KEY, python=sys.executable, run_network="host")
    listing = _call(session, "sanitize", {"target": "."})
    assert listing["isError"] is False
    file_result = _call(session, "sanitize", {"target": "mod.py"})
    assert file_result["isError"] is False
    ref = file_result["content"][0]["text"].splitlines()[0]
    view_lines = file_result["content"][0]["text"].splitlines()
    assert any("return t_" in line for line in view_lines)
    token = TOKEN.search(next(line for line in view_lines if "return t_" in line))
    assert token is not None
    edited = _call(
        session, "edit", {"doc": ref, "old": f"return {token.group(0)}", "new": "return 0"}
    )
    assert edited["isError"] is False
    assert (project / "mod.py").read_text(
        encoding="utf-8"
    ) == "def ingest(payload):\n    return 0\n"


def test_mcp_refuses_an_invented_token_in_a_python_edit(project: Path) -> None:
    session = Session([str(project)], key=KEY, python=sys.executable, run_network="host")
    ref = _call(session, "sanitize", {"target": "mod.py"})["content"][0]["text"].splitlines()[0]
    result = _call(
        session,
        "edit",
        {"doc": ref, "old": "return t_0123456789abcdef", "new": "return 0"},
    )
    assert result["isError"] is True
    assert result["content"][0]["text"] == "E_EDIT_UNKNOWN_TOKEN"


def test_python_suffixes_are_document_suffixes_of_the_vault() -> None:
    assert ".py" in paths.DOCUMENT_SUFFIXES
    assert ".pyi" in paths.DOCUMENT_SUFFIXES


def test_an_edit_of_a_python_file_leaks_no_canary(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    root = tmp_path / "project"
    root.mkdir()
    text = f"{canary}_value = 1\n# {canary}\nmsg = '{canary}'\nreturn_value = {canary}_value\n"
    (root / "mod.py").write_text(text, encoding="utf-8")
    session = Session([str(root)], key=KEY, isolate=True)
    stored = session.sanitize("mod.py")
    assert isinstance(stored, Sanitized)
    edited = session.edit(str(stored.ref), "msg", "message")
    diffed = session.diff(str(stored.ref), str(edited.ref))
    captured = capfd.readouterr()
    outputs = [stored.json, stored.view, edited.json, edited.view, diffed.json, diffed.view]
    assert find_leaks([*outputs, captured.out, captured.err], [canary]) == []
    assert "message" in (root / "mod.py").read_text(encoding="utf-8")
    assert variants(canary)
