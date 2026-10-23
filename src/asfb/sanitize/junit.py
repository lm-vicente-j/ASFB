"""JUnit censoring: each testcase becomes schema nodes with tokens for every user string.

Public exception and outcome names stay visible as vocabulary terms. Class names are split on
dots, the name is split into its base and its bracketed parameters, and every other string is one
token minted under the session key. Each token is also recorded as a (token, surface) pair with
the same rule as HTML (see asfb.sanitize.pairs). Totals are counted from the cases, not read from
the XML attributes.
"""

from typing import Final

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.junit import RawCase
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.schema import run as schema
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue
from asfb.vocab import FrozenVocabulary, registry

PY_EXCEPTIONS: Final = "py.exceptions"


def _mint(key: SessionKey, text: Untrusted[str], surface: str, recorder: PairRecorder) -> Token:
    token = token_for(key, text)
    recorder.record(token, surface)
    return token


def _token_or_none(key: SessionKey, recorder: PairRecorder, raw: str | None) -> Token | None:
    if raw is None:
        return None
    value = normalize.value(Untrusted(raw))
    if not unwrap(value):
        return None
    return _mint(key, value, surface_of(raw), recorder)


def _suite(key: SessionKey, recorder: PairRecorder, raw: str) -> tuple[Token, ...]:
    whole = normalize.value(Untrusted(raw))
    if not unwrap(whole):
        return ()
    pieces = [(normalize.value(Untrusted(piece)), surface_of(piece)) for piece in raw.split(".")]
    parts = [(value, surface) for value, surface in pieces if unwrap(value)]
    if not parts or len(parts) > schema.MAX_SUITE:
        return (_mint(key, whole, surface_of(raw), recorder),)
    return tuple(_mint(key, value, surface, recorder) for value, surface in parts)


def _name(key: SessionKey, recorder: PairRecorder, raw: str) -> tuple[Token | None, Token | None]:
    """A name like `test_x[a-b]` splits into its base and its parameters, bracket excluded."""
    stripped = surface_of(raw)
    if stripped.endswith("]") and "[" in stripped:
        cut = stripped.index("[")
        base, params = stripped[:cut], stripped[cut + 1 : -1]
    else:
        base, params = stripped, ""
    return _token_or_none(key, recorder, base), _token_or_none(key, recorder, params)


def _vocabulary() -> FrozenVocabulary:
    entry = registry()[PY_EXCEPTIONS]
    if not isinstance(entry, FrozenVocabulary):
        raise AsfbError(ErrorCode.E_VOCAB_DATA)
    return entry


def _error_type(
    key: SessionKey, recorder: PairRecorder, raw: str | None, known: FrozenVocabulary
) -> EnumValue | Token | None:
    if raw is None:
        return None
    value = unwrap(normalize.value(Untrusted(raw)))
    if not value:
        return None
    if value in known:
        return EnumValue(vocab=PY_EXCEPTIONS, term=value)
    return _token_or_none(key, recorder, raw)


def _case(
    key: SessionKey, recorder: PairRecorder, case: RawCase, known: FrozenVocabulary
) -> schema.Case:
    name, params = _name(key, recorder, unwrap(case.name))
    error_type = None if case.error_type is None else unwrap(case.error_type)
    message = None if case.message is None else unwrap(case.message)
    return schema.Case(
        suite=_suite(key, recorder, unwrap(case.classname)),
        name=name,
        params=params,
        outcome=case.outcome,
        error_type=_error_type(key, recorder, error_type, known),
        message=_token_or_none(key, recorder, message),
    )


def _totals(cases: tuple[RawCase, ...]) -> schema.Totals:
    def count(outcome: str) -> int:
        return sum(1 for case in cases if case.outcome == outcome)

    return schema.Totals(
        tests=len(cases),
        failures=count("failed"),
        errors=count("error"),
        skipped=count("skipped"),
    )


def censor_junit(
    key: SessionKey, cases: tuple[RawCase, ...], /
) -> tuple[schema.JunitReport, SurfacePairs]:
    """Censor parsed junit cases into the report model and the pairs it minted."""
    recorder = PairRecorder()
    known = _vocabulary()
    built = tuple(_case(key, recorder, case, known) for case in cases)
    report = schema.JunitReport(totals=_totals(cases), cases=built)
    return report, SurfacePairs(recorder.items())
