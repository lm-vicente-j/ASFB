"""A canary in content and paths never reaches MCP stdout across a whole session."""

import io
import json
import sys
from pathlib import Path

from asfb.mcp import server
from asfb.session import Session
from tests.canary.harness import new_canary
from tests.junit_samples import KEY


def test_canary_never_reaches_stdout(tmp_path: Path) -> None:
    canary = new_canary()
    site = tmp_path / f"site_{canary}"
    site.mkdir()
    (site / f"{canary}.html").write_text(f"<p>{canary}</p>", encoding="utf-8")
    session = Session([str(site)], key=KEY, python=sys.executable, run_network="host")
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "sanitize", "arguments": {"target": "."}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "edit",
                "arguments": {"doc": "d_0000000000000000@1", "old": canary, "new": canary},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "run", "arguments": {"tool": "pytest", "targets": ["."]}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "sanitize", "arguments": {"target": canary, "extra": canary}},
        },
    ]
    stdin = io.BytesIO(b"\n".join(json.dumps(r).encode() for r in requests) + b"\n")
    stdout = io.BytesIO()
    server.serve(session, stdin, stdout)
    assert canary.encode() not in stdout.getvalue()
