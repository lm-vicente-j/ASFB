"""Junit censoring: public names stay visible, user strings become tokens, pairs recompute."""

from typing import Literal

from asfb.ingest.junit import RawCase, read_junit
from asfb.sanitize.junit import censor_junit
from asfb.sanitize.tokens import token_for
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue
from tests.junit_samples import KEY, junit_xml


def _raw(
    classname: str = "",
    name: str = "",
    outcome: Literal["passed", "failed", "error", "skipped"] = "passed",
    error_type: str | None = None,
    message: str | None = None,
) -> RawCase:
    return RawCase(
        classname=Untrusted(classname),
        name=Untrusted(name),
        outcome=outcome,
        error_type=None if error_type is None else Untrusted(error_type),
        message=None if message is None else Untrusted(message),
    )


def test_totals_are_counted_from_the_cases() -> None:
    report, _pairs = censor_junit(KEY, read_junit(junit_xml()))
    assert report.totals.model_dump() == {"tests": 5, "failures": 2, "errors": 1, "skipped": 1}


def test_public_exception_is_a_vocabulary_term_and_pytest_skip_is_tokenized() -> None:
    report, _pairs = censor_junit(KEY, read_junit(junit_xml()))
    failed = report.cases[1]
    assert failed.error_type == EnumValue(vocab="py.exceptions", term="AssertionError")
    skipped = report.cases[3]
    assert isinstance(skipped.error_type, str)
    assert skipped.error_type.startswith("t_")


def test_class_is_split_on_dots_and_name_on_brackets() -> None:
    report, _pairs = censor_junit(KEY, read_junit(junit_xml()))
    assert len(report.cases[3].suite) == 3
    error = report.cases[2]
    assert error.name is not None
    assert error.params is not None
    assert error.params == token_for(KEY, Untrusted("a-b"))


def test_empty_classname_and_control_only_name_give_no_suite_and_no_name() -> None:
    report, _pairs = censor_junit(KEY, (_raw(classname="", name="​", outcome="passed"),))
    assert report.cases[0].suite == ()
    assert report.cases[0].name is None


def test_every_pair_recomputes_its_token() -> None:
    _report, pairs = censor_junit(KEY, read_junit(junit_xml("CANARYX")))
    for token, surface in pairs.items():
        assert token_for(KEY, Untrusted(surface)) == token


def test_a_key_other_than_the_session_key_gives_other_tokens() -> None:
    other = SessionKey(bytes(range(1, 33)))
    report, _pairs = censor_junit(KEY, (_raw(name="x", outcome="passed"),))
    again, _again_pairs = censor_junit(other, (_raw(name="x", outcome="passed"),))
    assert report.cases[0].name != again.cases[0].name
