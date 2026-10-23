"""The db job in the worker: the request carries base64 bytes, the reply is a db model or a code."""

import base64
import io
import json

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol, worker
from asfb.sanitize.tokens import token_for
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))


def _request(data: bytes | str, **overrides: object) -> bytes:
    text = data if isinstance(data, str) else base64.b64encode(data).decode("ascii")
    body: dict[str, object] = {
        "v": 1,
        "kind": "db",
        "key": KEY.export().hex(),
        "profile": "strict",
        "text": text,
        "name": None,
    }
    body.update(overrides)
    return protocol.frame(json.dumps(body).encode("utf-8"))


def _serve(data: bytes) -> dict[str, object]:
    out = io.BytesIO()
    worker.serve(io.BytesIO(data), out)
    body = protocol.unframe(out.getvalue(), protocol.MAX_RESPONSE)
    assert isinstance(body, bytes)
    parsed: dict[str, object] = json.loads(body)
    return parsed


def test_a_database_gets_an_ok_reply_with_a_db_model() -> None:
    reply = _serve(_request(image(SAMPLE_SCHEMA)))
    assert reply["ok"] is True
    assert reply["doc"]["schema_id"] == "asfb.db/1"  # type: ignore[index]
    assert reply["spans"] == []


def test_the_reply_pairs_recompute_under_the_job_key() -> None:
    reply = _serve(_request(image(SAMPLE_SCHEMA)))
    pairs = reply["pairs"]
    assert isinstance(pairs, list)
    assert pairs
    for token, surface in pairs:
        assert isinstance(surface, str)
        # The request key is the job key here, so each pair must recompute under it.
        assert token_for(KEY, Untrusted(surface)) == token


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, ErrorCode.E_DB_NOT_SQLITE),
        (image(SAMPLE_SCHEMA)[:600], ErrorCode.E_DB_CORRUPT),
    ],
)
def test_a_refused_database_gets_a_static_code(data: bytes, code: ErrorCode) -> None:
    reply = _serve(_request(data))
    assert reply == {"v": 1, "ok": False, "code": code.value}


def test_bytes_that_are_not_base64_get_worker_protocol() -> None:
    reply = _serve(_request("not base64 !!"))
    assert reply == {"v": 1, "ok": False, "code": ErrorCode.E_WORKER_PROTOCOL.value}


def test_the_db_codes_are_worker_codes() -> None:
    for name in (
        "E_DB_NOT_SQLITE",
        "E_DB_TOO_LARGE",
        "E_DB_CORRUPT",
        "E_DB_UNSUPPORTED_RUNTIME",
        "E_DB_DENIED",
        "E_DB_BUDGET_EXCEEDED",
        "E_DB_LIMIT_EXCEEDED",
    ):
        assert name in protocol.WORKER_CODES


def test_a_virtual_table_in_the_worker_reply_has_an_unknown_count() -> None:
    reply = _serve(_request(image(SAMPLE_SCHEMA)))
    doc = reply["doc"]
    assert isinstance(doc, dict)
    virtual = [table for table in doc["tables"] if table["rows"] == "unknown"]
    assert len(virtual) == 1
    assert virtual[0]["columns"] == []
