"""asfb.run/1 and its worker model: closed shapes, bounded lists, committed schema."""

import json
from importlib import resources

import pydantic
import pytest

from asfb.schema import RUN_RESOURCE
from asfb.schema.doc import Profile, VocabRef
from asfb.schema.run import Case, JunitReport, RunV1, Totals
from asfb.types.token import Token
from asfb.types.values import EnumValue

VOCAB = (VocabRef(id="py.exceptions", version=2), VocabRef(id="run.tools", version=1))
TOTALS = Totals(tests=0, failures=0, errors=0, skipped=0)


def _run(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": Profile.STRICT,
        "vocab": VOCAB,
        "tool": EnumValue(vocab="run.tools", term="pytest"),
        "exit": "ok",
        "report": "parsed",
        "totals": TOTALS,
        "cases": (),
    }
    base.update(changes)
    return base


def test_run_model_accepts_a_parsed_summary() -> None:
    assert RunV1(**_run()).schema_id == "asfb.run/1"  # type: ignore[arg-type]


@pytest.mark.parametrize("change", [{"exit": "bogus"}, {"report": "partial"}, {"extra": 1}])
def test_run_model_refuses_unknown_values_and_fields(change: dict[str, object]) -> None:
    with pytest.raises(pydantic.ValidationError):
        RunV1(**_run(**change))  # type: ignore[arg-type]


def test_case_refuses_extra_fields_and_long_suites() -> None:
    with pytest.raises(pydantic.ValidationError):
        Case(
            suite=(),
            name=None,
            params=None,
            outcome="passed",
            error_type=None,
            message=None,
            traceback="x",
        )  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        Case(
            suite=tuple(Token("t_0123456789abcdef") for _ in range(65)),
            name=None,
            params=None,
            outcome="passed",
            error_type=None,
            message=None,
        )


def test_totals_are_bounded() -> None:
    with pytest.raises(pydantic.ValidationError):
        Totals(tests=-1, failures=0, errors=0, skipped=0)


def test_junit_report_is_a_separate_model_with_its_own_schema_id() -> None:
    report = JunitReport(totals=TOTALS, cases=())
    assert report.schema_id == "asfb.junit/1"


def test_committed_run_schema_equals_the_model() -> None:
    text = resources.files("asfb.schema").joinpath(RUN_RESOURCE).read_text(encoding="utf-8")
    assert json.loads(text) == RunV1.model_json_schema()
