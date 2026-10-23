"""Read a JUnit XML report (the pytest junit family) into raw cases. Fails closed.

Only the testsuites or testsuite root, the testcase elements, and the first failure, error or
skipped child of each case are read. DOCTYPE and ENTITY declarations are refused before parsing.
Every string leaves as Untrusted. Public functions raise AsfbError outside any except block.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Final, Literal

from asfb.errors import AsfbError, ErrorCode
from asfb.types.untrusted import Untrusted

MAX_JUNIT_BYTES: Final = 8 * 1024 * 1024
MAX_CASES: Final = 10_000
_ROOTS: Final = ("testsuites", "testsuite")
_OUTCOMES: Final[dict[str, Literal["failed", "error", "skipped"]]] = {
    "failure": "failed",
    "error": "error",
    "skipped": "skipped",
}
_DECLARATIONS: Final = ("<!doctype", "<!entity")


@dataclass(frozen=True, slots=True, eq=False)
class RawCase:
    """One testcase: its class and name, its outcome, and the type and message of its child."""

    classname: Untrusted[str]
    name: Untrusted[str]
    outcome: Literal["passed", "failed", "error", "skipped"]
    error_type: Untrusted[str] | None
    message: Untrusted[str] | None


def _root(text: str) -> ET.Element | ErrorCode:
    if any(marker in text.lower() for marker in _DECLARATIONS):
        return ErrorCode.E_RUN_PARSE
    try:
        root = ET.fromstring(text)  # noqa: S314
    except (ET.ParseError, ValueError, RecursionError):
        return ErrorCode.E_RUN_PARSE
    if root.tag not in _ROOTS:
        return ErrorCode.E_RUN_PARSE
    if root.tag == "testsuites" and any(child.tag != "testsuite" for child in root):
        return ErrorCode.E_RUN_PARSE
    return root


def _case(element: ET.Element) -> RawCase:
    outcome: Literal["passed", "failed", "error", "skipped"] = "passed"
    error_type: Untrusted[str] | None = None
    message: Untrusted[str] | None = None
    for child in element:
        if child.tag in _OUTCOMES:
            outcome = _OUTCOMES[child.tag]
            error_type = Untrusted(child.get("type", ""))
            message = Untrusted(child.get("message", ""))
            break
    return RawCase(
        classname=Untrusted(element.get("classname", "")),
        name=Untrusted(element.get("name", "")),
        outcome=outcome,
        error_type=error_type,
        message=message,
    )


def _cases(text: str) -> tuple[RawCase, ...] | ErrorCode:
    if type(text) is not str:
        return ErrorCode.E_RUN_PARSE
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return ErrorCode.E_RUN_PARSE
    if size > MAX_JUNIT_BYTES:
        return ErrorCode.E_SOURCE_SIZE
    root = _root(text)
    if isinstance(root, ErrorCode):
        return root
    cases: list[RawCase] = []
    # iter() walks the whole tree without recursion, so nested testsuite elements are covered.
    for element in root.iter("testcase"):
        if len(cases) == MAX_CASES:
            return ErrorCode.E_RUN_PARSE
        cases.append(_case(element))
    return tuple(cases)


def read_junit(text: str, /) -> tuple[RawCase, ...]:
    """Parse a junit report into its testcases. Raises AsfbError(E_RUN_PARSE or E_SOURCE_SIZE)."""
    outcome = _cases(text)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
