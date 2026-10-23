"""A canary in every field the junit parser must drop or tokenize never reaches the output."""

import pytest

from asfb.egress import Emitted, emit, render
from asfb.ingest.junit import read_junit
from asfb.sandbox.client import run_job
from asfb.sanitize.junit import censor_junit
from asfb.schema.doc import Profile, VocabRef
from asfb.schema.run import JunitReport, RunV1
from asfb.types.values import EnumValue
from asfb.vocab import registry
from tests.canary.harness import new_canary
from tests.junit_samples import KEY, junit_xml


def _emitted(report: JunitReport) -> Emitted:
    run = RunV1(
        profile=Profile.STRICT,
        vocab=(VocabRef(id="py.exceptions", version=2), VocabRef(id="run.tools", version=1)),
        tool=EnumValue(vocab="run.tools", term="pytest"),
        exit="tests_failed",
        report="parsed",
        totals=report.totals,
        cases=report.cases,
    )
    return emit(run, vocab=registry())


@pytest.mark.parametrize("isolated", [False, True])
def test_canary_is_absent_from_every_output(isolated: bool) -> None:
    canary = new_canary()
    xml = junit_xml(canary)
    if isolated:
        model, pairs = run_job("junit", KEY, Profile.STRICT, xml)
        assert isinstance(model, JunitReport)
    else:
        model, pairs = censor_junit(KEY, read_junit(xml))
    emitted = _emitted(model)
    view = render(emitted)
    assert canary not in emitted
    assert canary not in view
    assert canary not in repr(pairs)
    assert canary not in model.model_dump_json()
