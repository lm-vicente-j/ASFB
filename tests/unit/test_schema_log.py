"""The v1 log model: its validators, its committed JSON Schema, its gate and its view."""

import json
import re
from importlib import resources

import pytest
from pydantic import ValidationError

from asfb.egress import Emitted, emit, log_units, render
from asfb.errors import EgressError
from asfb.ingest import log as ingest_log
from asfb.sanitize.log import censor_log_with_pairs
from asfb.schema import LOG_RESOURCE
from asfb.schema.diff import DiffV1, RefV1
from asfb.schema.doc import Profile, VocabRef
from asfb.schema.log import LogField, LogNumber, LogRecord, LogTime, LogV1
from asfb.types.docid import DocId
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.values import Bucket, BucketScheme, EnumValue
from asfb.vocab import registry

TOKEN = Token("t_0123456789abcdef")
KEY = SessionKey(bytes(range(32)))
REFS = (
    VocabRef(id="log.fields", version=1),
    VocabRef(id="log.levels", version=1),
    VocabRef(id="py.exceptions", version=2),
)
YEAR = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SAMPLE = (
    "2024-05-01T10:00:00.000Z INFO app: started by alice\n"
    "2024-05-01T10:00:01.250Z WARN app.db: slow for user 4711\n"
    '{"timestamp":"2025-03-09T08:00:00Z","level":"error","msg":"boom","exc_type":"KeyError"}\n'
    "  continuation of the error\n"
    "1999-12-31T23:59:59Z ERROR old\n"
)


def _record(**fields: object) -> LogRecord:
    base: dict[str, object] = {"level": None, "time": None, "message": TOKEN, "fields": ()}
    base.update(fields)
    return LogRecord.model_validate(base)


def _doc(**fields: object) -> LogV1:
    base: dict[str, object] = {
        "profile": Profile.STRICT,
        "format": "text",
        "vocab": REFS,
        "raw": 0,
        "records": (),
    }
    base.update(fields)
    return LogV1.model_validate(base)


def _refused(build: object) -> None:
    with pytest.raises(ValidationError):
        build()  # type: ignore[operator]


def test_committed_schema_is_the_model_schema() -> None:
    committed = resources.files("asfb.schema").joinpath(LOG_RESOURCE).read_text(encoding="utf-8")
    assert json.loads(committed) == LogV1.model_json_schema()


def test_empty_log_is_valid() -> None:
    assert _doc().schema_id == "asfb.log/1"


def test_field_name_must_be_a_log_field_term_or_a_token() -> None:
    LogField(name=TOKEN, value=TOKEN)
    LogField(name=EnumValue(vocab="log.fields", term="message"), value=TOKEN)
    _refused(lambda: LogField(name=EnumValue(vocab="log.levels", term="INFO"), value=TOKEN))


def test_field_value_may_be_an_exception_term_only_as_a_public_value() -> None:
    LogField(name=TOKEN, value=EnumValue(vocab="py.exceptions", term="KeyError"))
    _refused(lambda: LogField(name=TOKEN, value=EnumValue(vocab="log.levels", term="INFO")))


def test_record_level_must_be_a_log_level() -> None:
    _record(level=EnumValue(vocab="log.levels", term="WARN"))
    _refused(lambda: _record(level=EnumValue(vocab="log.fields", term="level")))


def test_time_index_is_a_bucket_index() -> None:
    LogTime(index=0, backwards=False)
    LogTime(index=64, backwards=True)
    _refused(lambda: LogTime(index=65, backwards=False))
    _refused(lambda: LogTime(index=-1, backwards=False))


def test_number_carries_a_sign_and_a_log2_bucket() -> None:
    LogNumber(sign="neg", bucket=Bucket(scheme=BucketScheme.LOG2, index=3))
    _refused(
        lambda: LogNumber.model_validate({"sign": "zero", "bucket": {"scheme": "log2", "index": 3}})
    )


def test_vocabularies_are_exactly_the_three_log_ones() -> None:
    _refused(lambda: _doc(vocab=REFS[:2]))
    _refused(lambda: _doc(vocab=(REFS[1], REFS[0], REFS[2])))


def test_raw_count_cannot_exceed_the_records() -> None:
    _refused(lambda: _doc(raw=1, records=()))


def test_a_record_field_list_is_bounded() -> None:
    field = LogField(name=TOKEN, value=TOKEN)
    _refused(lambda: _record(fields=(field,) * 257))


def test_format_is_jsonl_or_text_only() -> None:
    _doc(format="jsonl")
    _refused(lambda: _doc(format="json"))


def test_sample_gives_a_view_with_a_header_and_one_line_per_record() -> None:
    model, _pairs = censor_log_with_pairs(KEY, ingest_log.read_text(SAMPLE, "text"), Profile.STRICT)
    emitted = emit(model, vocab=registry())
    view = str(render(emitted))
    lines = view.split("\n")
    # The JSON line of a .log text source is raw; the continuation line joins it.
    assert lines[0] == "log format=text raw=1"
    assert lines[1].startswith("INFO ")
    assert lines[2].startswith("WARN dt=b11 ")  # 1250 ms has 11 bits
    assert len(lines) == 1 + len(model.records)
    assert len(log_units(emitted)) == len(model.records)


def test_no_year_or_date_reaches_the_json_or_the_view() -> None:
    model, _pairs = censor_log_with_pairs(KEY, ingest_log.read_text(SAMPLE, "text"), Profile.STRICT)
    emitted = emit(model, vocab=registry())
    view = str(render(emitted))
    for blob in (emitted, view):
        clear = re.sub(r"t_[0-9a-f]{16}", "", blob)  # tokens are hex and may hold any run of digits
        assert YEAR.search(clear) is None
        assert DATE.search(clear) is None


def test_the_full_gate_accepts_the_sample_and_the_view_is_stable() -> None:
    model, _pairs = censor_log_with_pairs(
        KEY, ingest_log.read_text(SAMPLE, "jsonl"), Profile.STRICT
    )
    emitted = emit(model, vocab=registry())
    assert render(emitted) == render(emitted)


def test_tampered_log_fails_the_gate_at_render() -> None:
    model, _pairs = censor_log_with_pairs(KEY, ingest_log.read_text(SAMPLE, "text"), Profile.STRICT)
    emitted = emit(model, vocab=registry())
    tampered = emitted.replace('"format":"text"', '"format":"text","extra":1', 1)
    with pytest.raises(EgressError):
        render(Emitted(tampered))


def test_record_diff_units_are_valid_in_a_diff_model() -> None:
    diff = DiffV1(
        profile=Profile.STRICT,
        vocab=(),
        units="record",
        a=RefV1(doc=DocId("d_0123456789abcdef"), revision=1),
        b=RefV1(doc=DocId("d_0123456789abcdef"), revision=2),
        hunks=(),
    )
    assert diff.units == "record"
