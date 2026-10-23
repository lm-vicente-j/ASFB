"""The v1 log model: a header and a list of records, each with an optional level, an optional time
bucket, a message token and a list of fields.

Public: the level term (log.levels), a field name that is a log.fields term, the value of an
exc_type field when it is a py.exceptions term, true, false and null. Every other string is a
token: the message, the logger, the values and the names that are not standard. A number is the
log2 bucket of its absolute value with its sign. A time is the log2 bucket of the delta in
milliseconds to the previous timed record, with a flag for a step back in time. The instant itself
is never emitted. Nested objects are groups of fields; arrays and deeper objects are one token.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import Bucket, EnumValue

MAX_RECORDS = 200_000  # the ingest record limit (ingest/limits.py MAX_LOG_RECORDS)
MAX_FIELDS = 256  # the ingest field limit per record, nested fields included
Format = Literal["jsonl", "text"]
Sign = Literal["neg", "pos"]
LOG_VOCAB_IDS: Final = ("log.fields", "log.levels", "py.exceptions")


class LogTime(StrictModel):
    """The step from the previous timed record: index is the bit count of the delta in ms."""

    scheme: Literal["log2"] = "log2"
    unit: Literal["ms"] = "ms"
    index: Annotated[int, Field(ge=0, le=64)]
    backwards: bool


class LogNumber(StrictModel):
    sign: Sign
    bucket: Bucket


class LogGroup(StrictModel):
    fields: Annotated[tuple["LogField", ...], Field(max_length=MAX_FIELDS)]


LogValue = Token | EnumValue | LogNumber | bool | None | LogGroup


class LogField(StrictModel):
    name: EnumValue | Token
    value: LogValue

    @model_validator(mode="after")
    def _vocab(self) -> Self:
        if isinstance(self.name, EnumValue) and self.name.vocab != "log.fields":
            raise ValueError("a field name is a log.fields term or a token")
        if isinstance(self.value, EnumValue) and self.value.vocab != "py.exceptions":
            raise ValueError("a field value is a py.exceptions term, a token or a literal")
        return self


LogGroup.model_rebuild()
LogField.model_rebuild()


class LogRecord(StrictModel):
    level: EnumValue | None
    time: LogTime | None
    message: Token | None
    fields: Annotated[tuple[LogField, ...], Field(max_length=MAX_FIELDS)]

    @model_validator(mode="after")
    def _level(self) -> Self:
        if self.level is not None and self.level.vocab != "log.levels":
            raise ValueError("a record level is a log.levels term")
        return self


class LogV1(StrictModel):
    schema_id: Literal["asfb.log/1"] = "asfb.log/1"
    profile: Profile
    format: Format
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=len(LOG_VOCAB_IDS))]
    raw: Annotated[int, Field(ge=0, le=MAX_RECORDS)]
    records: Annotated[tuple[LogRecord, ...], Field(max_length=MAX_RECORDS)]

    @model_validator(mode="after")
    def _vocab_ids(self) -> Self:
        if tuple(ref.id for ref in self.vocab) != LOG_VOCAB_IDS:
            raise ValueError("log declares exactly the three log vocabularies")
        if self.raw > len(self.records):
            raise ValueError("raw records are records")
        return self
