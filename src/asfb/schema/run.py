"""The v1 run models. JunitReport crosses the worker protocol only; RunV1 is what egress emits."""

from typing import Annotated, Literal

from pydantic import Field

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import EnumValue

MAX_SUITE = 64
MAX_CASES = 10_000
Outcome = Literal["passed", "failed", "error", "skipped"]
Exit = Literal[
    "ok",
    "tests_failed",
    "interrupted",
    "internal_error",
    "usage_error",
    "no_tests",
    "timeout",
    "other",
]


class Totals(StrictModel):
    tests: Annotated[int, Field(ge=0, le=1_000_000)]
    failures: Annotated[int, Field(ge=0, le=1_000_000)]
    errors: Annotated[int, Field(ge=0, le=1_000_000)]
    skipped: Annotated[int, Field(ge=0, le=1_000_000)]


class Case(StrictModel):
    """One testcase. Class and name are split into tokens; the traceback is never kept."""

    suite: Annotated[tuple[Token, ...], Field(max_length=MAX_SUITE)]
    name: Token | None
    params: Token | None
    outcome: Outcome
    error_type: EnumValue | Token | None
    message: Token | None


class JunitReport(StrictModel):
    """What the worker returns for a junit job. Never emitted; RunV1 carries it out."""

    schema_id: Literal["asfb.junit/1"] = "asfb.junit/1"
    totals: Totals
    cases: Annotated[tuple[Case, ...], Field(max_length=MAX_CASES)]


class RunV1(StrictModel):
    """The result of one test run: exit class, report state, totals and the parsed cases."""

    schema_id: Literal["asfb.run/1"] = "asfb.run/1"
    profile: Profile
    vocab: tuple[VocabRef, ...]
    tool: EnumValue
    exit: Exit
    report: Literal["parsed", "dropped"]
    totals: Totals | None
    cases: Annotated[tuple[Case, ...], Field(max_length=MAX_CASES)]
