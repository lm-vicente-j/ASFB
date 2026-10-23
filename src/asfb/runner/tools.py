"""The test tools `run` may start. Each one has a fixed argv shape and a fixed report parser.

The agent never passes flags: the argv is built here from the resolved targets only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from asfb.schema.run import Exit

_PYTEST_EXITS: Final[dict[int, Exit]] = {
    0: "ok",
    1: "tests_failed",
    2: "interrupted",
    3: "internal_error",
    4: "usage_error",
    5: "no_tests",
}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: Literal["pytest"]
    parser: Literal["junit"]

    def argv(self, python: str, report: str, targets: Sequence[str]) -> list[str]:
        """The command for the given interpreter, report file and absolute targets."""
        return [
            python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit2",
            f"--junitxml={report}",
            "--",
            *targets,
        ]

    def exit_name(self, code: int | None) -> Exit:
        """The exit class of a return code. None means the run timed out; codes this tool does
        not define are `other`."""
        if code is None:
            return "timeout"
        return _PYTEST_EXITS.get(code, "other")


TOOLS: Final[dict[str, ToolSpec]] = {"pytest": ToolSpec(name="pytest", parser="junit")}
