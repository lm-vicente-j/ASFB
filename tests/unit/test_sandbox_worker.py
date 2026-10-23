"""The worker's serve() in-process: framing, kinds, key handling and static codes.

main() is not called here: it installs the audit hook, which cannot be removed in the pytest
process. The subprocess tests in test_sandbox_client.py cover main().
"""

import io
import json

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol, worker
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import DocV1
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted

KEY = bytes(range(32))
CANARY = "CANARY-WRK-4411"


def _request(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "v": 1,
        "kind": "html",
        "key": KEY.hex(),
        "profile": "strict",
        "text": "<p>hello</p>",
    }
    body.update(overrides)
    return protocol.frame(json.dumps(body).encode("utf-8"))


def _serve(data: bytes) -> bytes:
    out = io.BytesIO()
    worker.serve(io.BytesIO(data), out)
    return out.getvalue()


def _reply(data: bytes) -> protocol.OkReply | protocol.ErrReply | ErrorCode:
    body = protocol.unframe(_serve(data), protocol.MAX_RESPONSE)
    assert isinstance(body, bytes)
    return protocol.parse_reply(body)


def test_valid_html_request_gets_an_ok_reply() -> None:
    reply = _reply(_request())
    assert isinstance(reply, protocol.OkReply)
    assert isinstance(reply.doc, DocV1)
    assert reply.doc.source == "html"
    assert reply.pairs


def test_ok_reply_pairs_recompute_under_the_key() -> None:
    reply = _reply(_request(text=f"<p title='{CANARY}'>{CANARY}</p>"))
    assert isinstance(reply, protocol.OkReply)
    key = SessionKey(KEY)
    for token, surface in reply.pairs:
        assert token_for(key, Untrusted(surface)) == token


def test_response_is_one_frame_with_nothing_after_it() -> None:
    raw = _serve(_request())
    size = int.from_bytes(raw[:4], "big")
    assert len(raw) == 4 + size


@pytest.mark.parametrize("kind", ["pdf", "", "HTML", "Junit"])
def test_unknown_kinds_get_worker_protocol(kind: str) -> None:
    reply = _reply(_request(kind=kind))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_WORKER_PROTOCOL"


def test_bad_key_gets_worker_protocol() -> None:
    reply = _reply(_request(key="zz" * 32))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_WORKER_PROTOCOL"


def test_empty_stdin_gets_worker_protocol() -> None:
    reply = _reply(b"")
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_WORKER_PROTOCOL"


def test_request_over_the_cap_gets_worker_protocol() -> None:
    raw = (protocol.MAX_REQUEST + 1).to_bytes(4, "big") + b"x"
    reply = _reply(raw)
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_WORKER_PROTOCOL"


def test_html_parse_error_is_reported_by_static_code() -> None:
    text = f"<!DOCTYPE html><p>{CANARY}</p><!DOCTYPE {CANARY}>"
    reply = _reply(_request(text=text))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_HTML_PARSE"
    assert CANARY.encode() not in _serve(_request(text=text))


def test_too_deep_html_is_reported_by_static_code() -> None:
    reply = _reply(_request(text="<div>" * 65 + "x"))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_HTML_DEPTH"


def test_unknown_profile_gets_worker_protocol() -> None:
    reply = _reply(_request(profile="lenient"))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_WORKER_PROTOCOL"


def test_every_worker_reply_code_is_in_the_worker_subset() -> None:
    for text in ("<p>x</p>", "<!DOCTYPE html><!DOCTYPE html>", "<div>" * 65):
        reply = _reply(_request(text=text))
        if isinstance(reply, protocol.ErrReply):
            assert reply.code in protocol.WORKER_CODES


def test_key_is_not_echoed_in_the_reply() -> None:
    raw = _serve(_request())
    assert KEY.hex().encode() not in raw
