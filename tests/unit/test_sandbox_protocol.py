"""Framing, size caps and strict parsing of the worker protocol. No subprocess is started."""

import json

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol
from asfb.schema.doc import DocV1, Profile, SourceKind

KEY = bytes(range(32))
EMPTY_DOC = DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())
TOKEN = "t_0123456789abcdef"  # noqa: S105


def _frame_of(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _ok_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "v": 1,
        "ok": True,
        "doc": json.loads(EMPTY_DOC.model_dump_json()),
        "pairs": [[TOKEN, "a"]],
    }
    body.update(overrides)
    return body


def _reply_bytes(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode("utf-8")


def test_frame_round_trip() -> None:
    payload = b'{"v":1}'
    framed = protocol.frame(payload)
    assert framed[:4] == (7).to_bytes(4, "big")
    assert protocol.unframe(framed, protocol.MAX_REQUEST) == payload


def test_read_limit_is_one_frame_plus_one_byte() -> None:
    assert protocol.read_limit(protocol.MAX_REQUEST) == 4 + protocol.MAX_REQUEST + 1


def test_caps_are_the_specified_sizes() -> None:
    assert protocol.MAX_REQUEST == 32 * 1024 * 1024
    assert protocol.MAX_RESPONSE == 64 * 1024 * 1024
    assert protocol.VERSION == 1


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00\x00",
        b"\x00\x00\x00\x05abc",  # truncated body
        b"\x00\x00\x00\x02abc",  # trailing byte
        b"\x00\x00\x00\x00x",  # zero length with trailing byte
    ],
    ids=["empty", "short-header", "truncated", "trailing", "zero-length-trailing"],
)
def test_bad_frame_shapes_are_protocol_errors(data: bytes) -> None:
    assert protocol.unframe(data, 1024) is ErrorCode.E_WORKER_PROTOCOL


def test_length_over_the_cap_is_refused_before_any_body_is_read() -> None:
    header_only = (protocol.MAX_RESPONSE + 1).to_bytes(4, "big")
    assert protocol.unframe(header_only, protocol.MAX_RESPONSE) is ErrorCode.E_WORKER_PROTOCOL


def test_length_exactly_at_the_cap_is_accepted() -> None:
    payload = b"x" * 16
    assert protocol.unframe(_frame_of(payload), 16) == payload
    assert protocol.unframe(_frame_of(payload), 15) is ErrorCode.E_WORKER_PROTOCOL


def test_request_over_the_cap_is_a_size_error() -> None:
    text = "a" * (protocol.MAX_REQUEST + 1)
    assert protocol.encode_request("html", KEY, "strict", text) is ErrorCode.E_SOURCE_SIZE


def test_request_with_non_str_text_is_refused() -> None:
    raw: object = b"<p>x</p>"
    assert (
        protocol.encode_request("html", KEY, "strict", raw)  # type: ignore[arg-type]
        is ErrorCode.E_SOURCE_UNSUPPORTED
    )


def test_request_with_lone_surrogate_is_a_decode_error() -> None:
    assert protocol.encode_request("html", KEY, "strict", "\ud800") is ErrorCode.E_SOURCE_DECODE


def test_request_round_trips_through_the_request_model() -> None:
    framed = protocol.encode_request("html", KEY, "diffable", "<p>x</p>")
    assert isinstance(framed, bytes)
    body = protocol.unframe(framed, protocol.MAX_REQUEST)
    assert isinstance(body, bytes)
    request = protocol.parse_request(body)
    assert isinstance(request, protocol.Request)
    assert request.key == KEY.hex()
    assert request.profile == "diffable"
    assert request.text == "<p>x</p>"


@pytest.mark.parametrize(
    "body",
    [
        b'{"v":1,"kind":"html","key":"00","profile":"strict","text":""}',  # short key
        b'{"v":1,"kind":"html","key":"%s","profile":"lenient","text":""}' % (b"0" * 64),
        b'{"v":"1","kind":"html","key":"%s","profile":"strict","text":""}' % (b"0" * 64),
        b'{"v":1,"kind":"html","key":"%s","profile":"strict","text":"","extra":1}' % (b"0" * 64),
        b'{"v":1,"kind":"html","key":"%s","profile":"strict"}' % (b"0" * 64),
        b'{"v":1,"v":1,"kind":"html","key":"%s","profile":"strict","text":""}' % (b"0" * 64),
        b"not json",
        b"\xff\xfe",
        b'{"v":1,"kind":"html","key":"%s","profile":"strict","text":NaN}' % (b"0" * 64),
    ],
    ids=[
        "short-key",
        "unknown-profile",
        "string-version",
        "extra-field",
        "missing-text",
        "duplicate-key",
        "not-json",
        "not-utf8",
        "nan",
    ],
)
def test_malformed_requests_are_protocol_errors(body: bytes) -> None:
    assert protocol.parse_request(body) is ErrorCode.E_WORKER_PROTOCOL


def test_well_formed_request_is_accepted() -> None:
    body = b'{"v":1,"kind":"junit","key":"%s","profile":"strict","text":"x"}' % (b"f" * 64)
    request = protocol.parse_request(body)
    assert isinstance(request, protocol.Request)
    assert request.kind == "junit"


def test_ok_reply_round_trips() -> None:
    body = protocol.encode_ok(EMPTY_DOC, [(TOKEN, "a")])
    assert isinstance(body, bytes)
    reply = protocol.parse_reply(body)
    assert isinstance(reply, protocol.OkReply)
    assert reply.doc == EMPTY_DOC
    assert reply.pairs == ((TOKEN, "a"),)


def test_error_reply_round_trips() -> None:
    reply = protocol.parse_reply(protocol.encode_error(ErrorCode.E_HTML_PARSE))
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_HTML_PARSE"


@pytest.mark.parametrize(
    "body",
    [
        _ok_body(extra=1),
        _ok_body(pairs=[["t_0123456789ABCDEF", "a"]]),
        _ok_body(pairs=[[TOKEN]]),
        _ok_body(pairs=[[TOKEN, 3]]),
        _ok_body(pairs=[[TOKEN, "a", "b"]]),
        _ok_body(doc={"schema_id": "asfb.doc/1"}),
        _ok_body(ok=1),
        _ok_body(v=True),
        _ok_body(v=2),
        {"v": 1, "ok": True},
        {"v": 1, "ok": False, "code": "E_HTML_PARSE", "extra": 1},
        {"v": 1, "ok": False, "code": "E_EGRESS_TYPE"},
        {"v": 1, "ok": False, "code": "E_NOT_A_CODE"},
        {"v": 1, "ok": False},
        {"v": 1, "ok": False, "code": 7},
    ],
    ids=[
        "ok-extra-key",
        "bad-token-case",
        "pair-missing-surface",
        "surface-not-str",
        "pair-too-long",
        "doc-invalid",
        "ok-as-int",
        "version-as-bool",
        "wrong-version",
        "ok-missing-doc",
        "err-extra-key",
        "code-not-in-subset",
        "unknown-code",
        "err-missing-code",
        "code-not-str",
    ],
)
def test_invalid_replies_are_protocol_errors(body: dict[str, object]) -> None:
    assert protocol.parse_reply(_reply_bytes(body)) is ErrorCode.E_WORKER_PROTOCOL


def test_reply_with_duplicate_keys_is_refused() -> None:
    raw = b'{"v":1,"ok":false,"code":"E_HTML_PARSE","code":"E_HTML_DEPTH"}'
    assert protocol.parse_reply(raw) is ErrorCode.E_WORKER_PROTOCOL


def test_reply_with_nan_is_refused() -> None:
    raw = b'{"v":1,"ok":false,"code":"E_HTML_PARSE","x":NaN}'
    assert protocol.parse_reply(raw) is ErrorCode.E_WORKER_PROTOCOL


def test_worker_codes_are_a_subset_of_error_codes() -> None:
    assert protocol.WORKER_CODES <= {code.value for code in ErrorCode}
    for forbidden in ("E_EGRESS_TYPE", "E_VIEW", "E_UNTRUSTED_ACCESS", "E_WORKER_START"):
        assert forbidden not in protocol.WORKER_CODES


def test_kinds_are_html_and_junit() -> None:
    assert protocol.KINDS == frozenset({"html", "junit", "text", "md", "code", "log", "db"})


def test_encode_ok_with_no_pairs_sends_an_empty_list() -> None:
    body = protocol.encode_ok(EMPTY_DOC, [])
    assert isinstance(body, bytes)
    assert json.loads(body)["pairs"] == []
