"""Log ingest: JSON Lines and plain text are split and parsed, unparseable lines become raw entries,
and every limit refuses the whole source with its code."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import limits, log
from asfb.types.untrusted import Untrusted, unwrap

LOG_CODES = {
    ErrorCode.E_LOG_TOO_LARGE,
    ErrorCode.E_LOG_TOO_MANY_RECORDS,
    ErrorCode.E_LOG_LINE_TOO_LONG,
    ErrorCode.E_LOG_ENCODING_UNSUPPORTED,
    ErrorCode.E_LOG_TOO_MANY_FIELDS,
    ErrorCode.E_LOG_FIELD_NAME_TOO_LONG,
    ErrorCode.E_SOURCE_UNSUPPORTED,
}


def _refused(text: str, fmt: log.Format) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        log.read_text(text, fmt)
    return info.value.code


def _attempt(text: str, fmt: log.Format) -> log.LogSource | ErrorCode:
    try:
        return log.read_text(text, fmt)
    except AsfbError as error:
        return error.code


def _shown(entry: log.LogEntry) -> tuple[str | None, str | None, bool]:
    message = None if entry.message is None else unwrap(entry.message)
    level = None if entry.level is None else unwrap(entry.level)
    return message, level, entry.raw


def test_broken_json_line_is_one_raw_entry_and_no_error() -> None:
    text = '{"msg":"ok"}\n{"msg": broken\n{"msg":"again"}\n'
    source = log.read_text(text, "jsonl")
    assert [entry.raw for entry in source.entries] == [False, True, False]
    assert _shown(source.entries[1]) == ('{"msg": broken', None, True)


@pytest.mark.parametrize(
    "line",
    ["[1, 2]", '"just a string"', "42", '{"a": NaN}', '{"a": 1, "a": 2}', "{" * 2000],
)
def test_jsonl_lines_that_are_not_one_object_are_raw(line: str) -> None:
    source = log.read_text(line + "\n", "jsonl")
    assert len(source.entries) == 1
    assert source.entries[0].raw is True
    assert source.entries[0].fields == ()


def test_blank_lines_are_skipped_in_both_formats() -> None:
    assert len(log.read_text("\n  \n{}\n\n", "jsonl").entries) == 1
    assert len(log.read_text("\n\nINFO one\n\n", "text").entries) == 1


def test_json_object_fields_and_nesting() -> None:
    line = '{"msg":"hi","n":3,"ok":true,"none":null,"user":{"id":5},"tags":["a","b"]}'
    source = log.read_text(line, "jsonl")
    entry = source.entries[0]
    names = [unwrap(field.name) for field in entry.fields]
    assert names == ["msg", "n", "ok", "none", "user", "tags"]
    assert entry.fields[1].value == 3
    assert entry.fields[2].value is True
    assert entry.fields[3].value is None
    assert isinstance(entry.fields[4].value, log.LogGroup)
    tags = entry.fields[5].value
    assert isinstance(tags, Untrusted)
    assert unwrap(tags) == '["a","b"]'


def test_objects_past_the_depth_limit_are_one_token() -> None:
    depth = limits.MAX_LOG_JSON_DEPTH
    inner = "1"
    for _level in range(depth + 2):
        inner = "{" + '"k":' + inner + "}"
    entry = log.read_text(inner, "jsonl").entries[0]
    node: object = entry.fields[0].value
    levels = 1
    while isinstance(node, log.LogGroup):
        levels += 1
        node = node.fields[0].value
    assert levels == depth
    assert isinstance(node, Untrusted)


def test_text_header_gives_instant_level_logger_and_message() -> None:
    source = log.read_text("2024-05-01T10:00:01.250Z WARN app.db: slow query\n", "text")
    entry = source.entries[0]
    assert entry.raw is False
    assert entry.instant == log.parse_instant("2024-05-01T10:00:01.250Z")
    assert _shown(entry) == ("slow query", "WARN", False)
    assert [unwrap(field.name) for field in entry.fields] == ["logger"]
    logger = entry.fields[0].value
    assert isinstance(logger, Untrusted)
    assert unwrap(logger) == "app.db"


def test_text_continuation_lines_join_the_entry_before() -> None:
    text = "2024-05-01T10:00:00Z ERROR boom\n  File 'x.py', line 3\n\tlast\n"
    source = log.read_text(text, "text")
    assert len(source.entries) == 1
    assert _shown(source.entries[0]) == ("boom\n  File 'x.py', line 3\n\tlast", "ERROR", False)


def test_leading_continuation_without_an_entry_is_raw() -> None:
    source = log.read_text("   orphan\nINFO ok\n", "text")
    assert [entry.raw for entry in source.entries] == [True, False]


def test_unknown_level_word_stays_in_the_message() -> None:
    entry = log.read_text("2024-05-01T10:00:00Z Starting server\n", "text").entries[0]
    assert _shown(entry) == ("Starting server", None, False)


def test_level_is_matched_case_insensitively_inside_brackets() -> None:
    entry = log.read_text("[warn] disk low\n", "text").entries[0]
    assert _shown(entry) == ("disk low", "warn", False)


@pytest.mark.parametrize(
    "line",
    [
        "2024-13-01T10:00:00Z INFO bad month",
        "2024-02-30T10:00:00Z INFO no such day",
        "2024-05-01T10:00:60Z INFO no leap second",
        "2024-05-01T10:00:00+25:00 INFO bad zone",
        "some plain line with no header",
    ],
)
def test_line_without_a_valid_header_is_raw(line: str) -> None:
    entry = log.read_text(line + "\n", "text").entries[0]
    assert entry.raw is True
    assert entry.instant is None
    assert entry.level is None


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("a.log", "text"),
        ("a.jsonl", "jsonl"),
        ("A.NDJSON", "jsonl"),
        (None, "text"),
        ("a.txt", None),
    ],
)
def test_format_follows_the_suffix(name: str | None, fmt: str | None) -> None:
    assert log.format_of(name) == fmt


@pytest.mark.parametrize(
    ("stamp", "millis"),
    [
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T00:00:00.5Z", 500),
        ("1970-01-01T01:00:00+01:00", 0),
        ("1970-01-01T00:00:00,123456789", 123),
        ("2024-05-01 10:00:00", log.parse_instant("2024-05-01T10:00:00Z")),
    ],
)
def test_parse_instant_is_utc_milliseconds(stamp: str, millis: int | None) -> None:
    assert log.parse_instant(stamp) == millis


@pytest.mark.parametrize("stamp", ["2024-05-01", "10:00:00", "2024-05-01T10:00:00 Z", None, 3])
def test_parse_instant_refuses_what_is_not_a_full_stamp(stamp: object) -> None:
    assert log.parse_instant(stamp) is None


def test_decoded_drops_a_bom_and_refuses_other_bytes() -> None:
    assert log.decoded("﻿INFO x".encode()) == "INFO x"
    assert log.decoded(b"\xff\xfe") is ErrorCode.E_LOG_ENCODING_UNSUPPORTED


def test_lone_surrogate_is_refused_with_its_code() -> None:
    assert _refused("INFO \ud800\n", "text") is ErrorCode.E_LOG_ENCODING_UNSUPPORTED


def test_source_size_is_checked_before_splitting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_LOG_BYTES", 10)
    assert _refused("INFO a long message\n", "text") is ErrorCode.E_LOG_TOO_LARGE


def test_line_length_is_checked_in_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_LOG_LINE", 8)
    assert _refused("INFO ok\nINFO this line is long\n", "text") is ErrorCode.E_LOG_LINE_TOO_LONG
    assert _refused('{"a": "ééééé"}', "jsonl") is (ErrorCode.E_LOG_LINE_TOO_LONG)


def test_record_count_refuses_the_whole_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_LOG_RECORDS", 2)
    assert _refused("{}\n{}\n{}\n", "jsonl") is ErrorCode.E_LOG_TOO_MANY_RECORDS
    assert _refused("INFO a\nINFO b\nINFO c\n", "text") is ErrorCode.E_LOG_TOO_MANY_RECORDS


def test_record_count_default_is_200_000() -> None:
    assert limits.MAX_LOG_RECORDS == 200_000
    text = "INFO x\n" * (limits.MAX_LOG_RECORDS + 1)
    assert _refused(text, "text") is ErrorCode.E_LOG_TOO_MANY_RECORDS


def test_field_count_counts_nested_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_LOG_FIELDS", 3)
    assert _refused('{"a":1,"b":2,"c":3,"d":4}', "jsonl") is ErrorCode.E_LOG_TOO_MANY_FIELDS
    assert _refused('{"a":1,"b":{"c":2,"d":3}}', "jsonl") is ErrorCode.E_LOG_TOO_MANY_FIELDS
    assert len(log.read_text('{"a":1,"b":{"c":2}}', "jsonl").entries) == 1


def test_field_name_length_refuses_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_LOG_NAME", 4)
    assert _refused('{"abcde": 1}', "jsonl") is ErrorCode.E_LOG_FIELD_NAME_TOO_LONG
    assert _refused('{"a": {"abcde": 1}}', "jsonl") is ErrorCode.E_LOG_FIELD_NAME_TOO_LONG


def test_default_limits_are_the_documented_ones() -> None:
    assert limits.MAX_LOG_BYTES == 16 * 1024 * 1024
    assert limits.MAX_LOG_LINE == 64 * 1024
    assert limits.MAX_LOG_FIELDS == 256
    assert limits.MAX_LOG_NAME == 128
    assert limits.MAX_LOG_JSON_DEPTH == 16


PIECES = st.sampled_from(
    [
        "{",
        "}",
        '"msg"',
        ":",
        ",",
        "1",
        "null",
        "\n",
        "\r\n",
        " ",
        "\t",
        "INFO ",
        "[WARN] ",
        "2024-05-01T10:00:00Z ",
        "2024-13-40T10:00:00Z ",
        "app.db: ",
        "  continuation",
        "\u0000",
        "‮",
        "é",
        "NaN",
        "[",
        "]",
    ]
)


@settings(max_examples=300, deadline=None)
@given(st.lists(PIECES, max_size=60).map("".join), st.sampled_from(["jsonl", "text"]))
def test_reader_gives_a_source_or_one_static_code(text: str, fmt: log.Format) -> None:
    outcome = _attempt(text, fmt)
    if isinstance(outcome, ErrorCode):
        assert outcome in LOG_CODES
        return
    source = outcome
    assert source.format == fmt
    for entry in source.entries:
        assert entry.instant is None or fmt == "text"


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(max_size=12), st.integers() | st.booleans() | st.none()))
def test_json_objects_keep_one_field_per_key(obj: dict[str, object]) -> None:
    source = log.read_text(json.dumps(obj), "jsonl")
    entry = source.entries[0]
    assert [unwrap(field.name) for field in entry.fields] == list(obj)
