"""Log censoring: levels, time buckets, numbers, names, values, and the pairs each token mints."""

import re

import pytest

from asfb.ingest import log
from asfb.sanitize.log import censor_log_with_pairs
from asfb.schema import log as schema
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.values import EnumValue

KEY = SessionKey(bytes(range(32)))
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")


def _model(text: str, fmt: log.Format = "text") -> schema.LogV1:
    model, _pairs = censor_log_with_pairs(KEY, log.read_text(text, fmt), Profile.STRICT)
    return model


def _json(text: str, fmt: log.Format = "text") -> str:
    return _model(text, fmt).model_dump_json()


def _clear(text: str) -> str:
    """The output without its tokens: what is left is public words, buckets and literals."""
    return TOKEN_RE.sub("", text)


def test_one_second_apart_is_index_ten() -> None:
    model = _model("2024-05-01T10:00:00Z INFO a\n2024-05-01T10:00:01Z INFO b\n")
    assert model.records[0].time is None
    assert model.records[1].time is not None
    assert model.records[1].time.index == 10
    assert model.records[1].time.backwards is False


def test_zero_delta_is_index_zero_and_a_step_back_is_flagged() -> None:
    model = _model(
        "2024-05-01T10:00:00Z INFO a\n2024-05-01T10:00:00Z INFO b\n2024-05-01T09:59:59Z INFO c\n"
    )
    assert model.records[1].time == schema.LogTime(index=0, backwards=False)
    assert model.records[2].time == schema.LogTime(index=10, backwards=True)


def test_delta_is_taken_from_the_previous_timed_record() -> None:
    model = _model("2024-05-01T10:00:00Z INFO a\nINFO no time here\n2024-05-01T10:00:04Z INFO b\n")
    assert model.records[1].time is None
    assert model.records[2].time is not None
    assert model.records[2].time.index == 12


def test_time_index_stays_inside_the_bucket_range() -> None:
    model = _model("1970-01-01T00:00:00Z INFO a\n2999-12-31T23:59:59Z INFO b\n")
    assert model.records[1].time is not None
    assert 0 <= model.records[1].time.index <= 64


@pytest.mark.parametrize(
    "fragment",
    ["2024", "2023", "05-01", "10:00:00", "1714557600", "1714557600000", "T10:00"],
)
def test_no_absolute_time_reaches_the_output(fragment: str) -> None:
    text = (
        '{"timestamp":"2024-05-01T10:00:00.250Z","msg":"a"}\n'
        '{"ts":"2024-05-01T10:00:02Z","msg":"b"}\n'
        '{"time":1714557600,"msg":"c"}\n'
        "2024-05-01T10:00:03Z INFO d\n"
    )
    assert fragment not in _clear(_json(text, "jsonl"))
    assert fragment not in _clear(_json(text, "text"))


def test_second_time_key_is_dropped_and_not_shown() -> None:
    text = '{"timestamp":"2024-05-01T10:00:00Z","ts":"2024-05-01T10:00:09Z","msg":"a"}\n'
    model = _model(text, "jsonl")
    assert model.records[0].fields == ()
    assert "2024" not in model.model_dump_json()


def test_unparseable_time_value_is_a_token() -> None:
    model = _model('{"timestamp":"yesterday 10:00","msg":"a"}\n', "jsonl")
    assert model.records[0].time is None
    field = model.records[0].fields[0]
    assert isinstance(field.name, EnumValue)
    assert field.name.term == "timestamp"
    assert isinstance(field.value, Token)
    assert "yesterday" not in model.model_dump_json()


def test_level_is_canonical_and_the_original_case_is_lost() -> None:
    model = _model("[warn] disk low\n", "text")
    level = model.records[0].level
    assert level is not None
    assert level.vocab == "log.levels"
    assert level.term == "WARN"
    assert "warn" not in _json("[warn] disk low\n")


def test_json_level_and_syslog_digit_levels() -> None:
    model = _model(
        '{"level":"info","msg":"a"}\n{"severity":3,"msg":"b"}\n{"level":"loud","msg":"c"}\n',
        "jsonl",
    )
    assert model.records[0].level is not None
    assert model.records[0].level.term == "INFO"
    assert model.records[1].level is not None
    assert model.records[1].level.term == "3"
    assert model.records[2].level is None
    assert isinstance(model.records[2].fields[0].value, Token)


def test_message_is_one_token_never_word_by_word() -> None:
    model = _model('{"msg":"login failed for alice@example.com"}\n', "jsonl")
    message = model.records[0].message
    assert isinstance(message, Token)
    assert "alice" not in model.model_dump_json()
    assert "login" not in model.model_dump_json()


def test_message_surface_and_value_tokens_are_in_the_pairs() -> None:
    text = '{"msg":"hello world","user":"bob"}\n'
    model, pairs = censor_log_with_pairs(KEY, log.read_text(text, "jsonl"), Profile.STRICT)
    surfaces = dict(pairs.items())
    message = model.records[0].message
    assert message is not None
    assert surfaces[message] == "hello world"
    minted = set(TOKEN_RE.findall(model.model_dump_json()))
    assert minted <= set(surfaces)


def test_standard_names_are_public_and_others_are_tokens() -> None:
    model = _model('{"msg":"a","service":"api","customer_id":"c-1"}\n', "jsonl")
    fields = model.records[0].fields
    assert isinstance(fields[0].name, EnumValue)
    assert fields[0].name.term == "service"
    assert isinstance(fields[1].name, Token)


def test_exception_type_is_public_only_when_it_is_a_py_exception() -> None:
    model = _model(
        '{"exc_type":"ValueError","msg":"a"}\n{"exc_type":"AcmeError","msg":"b"}\n', "jsonl"
    )
    known = model.records[0].fields[0].value
    unknown = model.records[1].fields[0].value
    assert isinstance(known, EnumValue)
    assert known.vocab == "py.exceptions"
    assert known.term == "ValueError"
    assert isinstance(unknown, Token)
    assert "AcmeError" not in model.model_dump_json()


def test_numbers_are_log2_buckets_with_sign() -> None:
    model = _model('{"n":-3000,"m":0,"f":1.5,"big":1e30}\n', "jsonl")
    numbers = [field.value for field in model.records[0].fields]
    assert all(isinstance(number, schema.LogNumber) for number in numbers)
    found = [number for number in numbers if isinstance(number, schema.LogNumber)]
    assert [(number.sign, number.bucket.index) for number in found] == [
        ("neg", 12),
        ("pos", 0),
        ("pos", 1),
        ("pos", 64),
    ]


def test_booleans_and_null_are_public_values() -> None:
    model = _model('{"ok":true,"gone":false,"none":null}\n', "jsonl")
    assert [field.value for field in model.records[0].fields] == [True, False, None]


def test_arrays_and_deep_objects_are_one_token_each() -> None:
    model = _model('{"tags":["alpha","beta"]}\n', "jsonl")
    value = model.records[0].fields[0].value
    assert isinstance(value, Token)
    assert "alpha" not in model.model_dump_json()


def test_nested_objects_are_groups_of_fields() -> None:
    model = _model('{"req":{"id":7,"path":"/x"}}\n', "jsonl")
    group = model.records[0].fields[0].value
    assert isinstance(group, schema.LogGroup)
    assert len(group.fields) == 2
    assert "/x" not in model.model_dump_json()


def test_raw_lines_count_and_carry_one_token() -> None:
    model = _model('{"msg":"ok"}\n{"msg": broken\n', "jsonl")
    assert model.raw == 1
    assert len(model.records) == 2
    assert model.records[1].level is None
    assert model.records[1].time is None
    assert model.records[1].fields == ()


def test_same_message_gives_the_same_token() -> None:
    model = _model("INFO same text\nWARN same text\n")
    first, second = model.records[0].message, model.records[1].message
    assert first is not None
    assert first == second


def test_logger_of_a_text_record_is_a_token_named_by_its_standard_name() -> None:
    model = _model("INFO app.db: slow\n")
    field = model.records[0].fields[0]
    assert isinstance(field.name, EnumValue)
    assert field.name.term == "logger"
    assert isinstance(field.value, Token)
    assert "app.db" not in model.model_dump_json()
