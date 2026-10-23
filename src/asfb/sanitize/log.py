"""Log censoring: records, levels, time buckets, numbers, field names and values.

Rules, applied to the entries that ingest/log.py reads:
- The level is its log.levels term (ASCII case folded) or absent. A JSON level (or severity) is the
  first such field whose value is a level word or a syslog digit from 0 to 7.
- A message is one token: the text header message, or the first msg or message field that is a
  string. An empty message is absent.
- A time (the timestamp, time or ts field of a JSON record, or the text header) is never emitted.
  Its instant gives the delta to the previous timed record, as a log2 bucket of the delta in ms and
  a backwards flag. The first timed record has no time. Only the first instant of a record is kept.
  A time value that is not a valid instant is an ordinary value, hence a token.
- A number is a log2 bucket of its absolute value with its sign (index saturates at 64).
- A field name is its log.fields term when it equals one, else a token. An exc_type value is its
  py.exceptions term when it equals one, else a token. Every other string is a token of its raw
  surface. Booleans and null are shown as they are.
- An array, or an object past the depth limit, is one token of its compact JSON text.

Every token minted here records its (token, surface) pair for the vault. Only this module and
the ingest layer see the raw text; the model carries tokens and vocabulary terms.
"""

from typing import Final

from asfb.ingest import log as ingest
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.schema import log as schema
from asfb.schema.doc import Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import Bucket, BucketScheme, EnumValue
from asfb.vocab import log_level, registry

_MESSAGE_KEYS: Final = frozenset({"msg", "message"})
_LEVEL_KEYS: Final = frozenset({"level", "severity"})
_TIME_KEYS: Final = frozenset({"timestamp", "time", "ts"})
_EXCEPTION_KEY: Final = "exc_type"
_MAX_INDEX: Final = 64
_MILLIS: Final = 1000


def _mint(key: SessionKey, recorder: PairRecorder, raw: str) -> Token:
    """The token of a raw surface, and its (token, surface) pair for the vault."""
    token = token_for(key, Untrusted(raw))
    recorder.record(token, surface_of(raw))
    return token


def _bucket(size: int) -> Bucket:
    return Bucket(scheme=BucketScheme.LOG2, index=min(size.bit_length(), _MAX_INDEX))


def _number(value: int | float) -> schema.LogNumber:
    sign: schema.Sign = "neg" if value < 0 else "pos"
    return schema.LogNumber(sign=sign, bucket=_bucket(int(abs(value))))


def _level_word(word: str) -> EnumValue | None:
    term = log_level(word)
    return None if term is None else EnumValue(vocab="log.levels", term=term)


def _level_value(value: object) -> EnumValue | None:
    """A JSON level: a level word, or one syslog digit (a boolean never counts)."""
    if isinstance(value, Untrusted):
        return _level_word(unwrap(value))
    if type(value) is int and 0 <= value <= 7:
        return _level_word(str(value))
    return None


def _instant_value(value: object) -> int | None:
    """Milliseconds since the epoch of a JSON time value, or None when it is not a valid time.

    A number is Unix seconds. Ingest refuses non-finite numbers before they reach this function.
    """
    if isinstance(value, Untrusted):
        return ingest.parse_instant(unwrap(value))
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(value * _MILLIS)


def _field_name(name: str, key: SessionKey, recorder: PairRecorder) -> EnumValue | Token:
    if name in registry()["log.fields"]:
        return EnumValue(vocab="log.fields", term=name)
    return _mint(key, recorder, name)


def _field_value(
    name: str, value: ingest.LogValue, key: SessionKey, recorder: PairRecorder
) -> schema.LogValue:
    if isinstance(value, Untrusted):
        text = unwrap(value)
        if name == _EXCEPTION_KEY and text in registry()["py.exceptions"]:
            return EnumValue(vocab="py.exceptions", term=text)
        return _mint(key, recorder, text)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return _number(value)
    return schema.LogGroup(fields=tuple(_field(item, key, recorder) for item in value.fields))


def _field(item: ingest.LogField, key: SessionKey, recorder: PairRecorder) -> schema.LogField:
    name = unwrap(item.name)
    return schema.LogField(
        name=_field_name(name, key, recorder),
        value=_field_value(name, item.value, key, recorder),
    )


def _split(
    entry: ingest.LogEntry, key: SessionKey, recorder: PairRecorder
) -> tuple[Token | None, EnumValue | None, int | None, tuple[schema.LogField, ...]]:
    """The message, level, instant and public-or-token fields of one entry, in that order."""
    message: Token | None = None
    level: EnumValue | None = None
    if entry.message is not None:
        text = unwrap(entry.message)
        message = _mint(key, recorder, text) if surface_of(text) else None
    if entry.level is not None:
        level = _level_word(unwrap(entry.level))
    instant = entry.instant
    message_open = message is None and entry.message is None
    level_open = level is None
    instant_open = instant is None
    fields: list[schema.LogField] = []
    for item in entry.fields:
        name = unwrap(item.name)
        if message_open and name in _MESSAGE_KEYS and isinstance(item.value, Untrusted):
            message_open = False
            text = unwrap(item.value)
            message = _mint(key, recorder, text) if surface_of(text) else None
            continue
        if level_open and name in _LEVEL_KEYS:
            found = _level_value(item.value)
            if found is not None:
                level, level_open = found, False
                continue
        if name in _TIME_KEYS:
            moment = _instant_value(item.value)
            if moment is not None:
                # A time is never a field. Only the first valid one of a record is kept.
                if instant_open:
                    instant, instant_open = moment, False
                continue
        fields.append(_field(item, key, recorder))
    return message, level, instant, tuple(fields)


def censor_log_with_pairs(
    key: SessionKey, source: ingest.LogSource, profile: Profile, /
) -> tuple[schema.LogV1, SurfacePairs]:
    """Censor a parsed log into the v1 model and the (token, surface) pairs it minted."""
    recorder = PairRecorder()
    records: list[schema.LogRecord] = []
    raw = 0
    previous: int | None = None
    for entry in source.entries:
        if entry.raw:
            raw += 1
        message, level, instant, fields = _split(entry, key, recorder)
        time: schema.LogTime | None = None
        if instant is not None:
            if previous is not None:
                delta = instant - previous
                time = schema.LogTime(
                    index=min(abs(delta).bit_length(), _MAX_INDEX), backwards=delta < 0
                )
            previous = instant
        records.append(schema.LogRecord(level=level, time=time, message=message, fields=fields))
    refs = tuple(
        VocabRef(id=registry()[vocab_id].id, version=registry()[vocab_id].version)
        for vocab_id in schema.LOG_VOCAB_IDS
    )
    model = schema.LogV1(
        profile=profile,
        format=source.format,
        vocab=refs,
        raw=raw,
        records=tuple(records),
    )
    return model, SurfacePairs(recorder.items())
