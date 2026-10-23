# ruff: noqa: E501
"""Shared junit XML and key for the M2-11..M2-15 tests. Canary values are passed in by callers."""

from asfb.types.key import SessionKey

KEY = SessionKey(bytes(range(32)))


def junit_xml(canary: str = "") -> str:
    """A pytest-style report. With a canary, it appears in every field the parser must drop."""
    extra = canary
    return f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
<testcase classname="test_x" name="test_ok" time="0.0"/>
<testcase classname="test_x" name="test_bad" time="0.0"><failure type="AssertionError" message="assert 1 == 2 {extra}">def test_bad(): {extra}</failure><system-out>{extra}</system-out></testcase>
<testcase classname="test_x" name="test_param[a-b]" time="0.0"><error type="pytest.collect" message="boom {extra}"/></testcase>
<testcase classname="pkg.mod.Cls" name="test_skip" time="0"><skipped type="pytest.skip" message="nope"/></testcase>
<testsuite name="nested"><testcase classname="" name="nested_{extra}" time="0"><failure>{extra}</failure></testcase></testsuite>
</testsuite></testsuites>"""
