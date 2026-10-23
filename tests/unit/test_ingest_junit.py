"""The junit parser: accepted shapes, refused declarations and limits, and Untrusted strings."""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import junit
from asfb.types.untrusted import Untrusted
from tests.junit_samples import junit_xml


def _code(text: str) -> ErrorCode:
    with pytest.raises(AsfbError) as raised:
        junit.read_junit(text)
    return raised.value.code


def test_outcomes_follow_the_first_failure_error_or_skipped_child() -> None:
    cases = junit.read_junit(junit_xml())
    assert [case.outcome for case in cases] == ["passed", "failed", "error", "skipped", "failed"]


def test_nested_testsuites_are_read_and_strings_are_untrusted() -> None:
    cases = junit.read_junit(junit_xml())
    assert isinstance(cases[0].classname, Untrusted)
    assert isinstance(cases[1].error_type, Untrusted)
    assert cases[0].error_type is None
    assert cases[0].message is None


def test_a_bare_testsuite_root_is_accepted() -> None:
    xml = '<testsuite><testcase classname="a" name="b"/></testsuite>'
    assert len(junit.read_junit(xml)) == 1


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE testsuite [<!ENTITY a "b">]><testsuite/>',
        "<!doctype testsuite><testsuite/>",
        '<testsuite><testcase name="&a;"/></testsuite>',
        '<!ENTITY a "b"><testsuite/>',
    ],
)
def test_declarations_and_entities_are_refused(xml: str) -> None:
    assert _code(xml) is ErrorCode.E_RUN_PARSE


def test_broken_xml_and_wrong_roots_are_refused() -> None:
    assert _code("<testsuite>") is ErrorCode.E_RUN_PARSE
    assert _code("<html><body/></html>") is ErrorCode.E_RUN_PARSE
    assert _code("<testsuites><other/></testsuites>") is ErrorCode.E_RUN_PARSE


def test_too_many_cases_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(junit, "MAX_CASES", 2)
    xml = "<testsuite>" + '<testcase classname="a" name="b"/>' * 3 + "</testsuite>"
    assert _code(xml) is ErrorCode.E_RUN_PARSE


def test_oversized_report_is_a_source_size_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(junit, "MAX_JUNIT_BYTES", 10)
    assert _code(junit_xml()) is ErrorCode.E_SOURCE_SIZE
