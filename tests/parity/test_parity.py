from __future__ import annotations

import os
from typing import Callable

import pytest

from tests.parity import harness, models
from tests.parity.models import MODELS, ModelSpec, ModelUnavailable

pytestmark = pytest.mark.parity

MARGIN = float(os.environ.get("ASFB_PARITY_MARGIN") or 0.05)
TOKEN_OVERHEAD_MAX = float(os.environ.get("ASFB_TOKEN_OVERHEAD_MAX") or 0.20)
SAMPLES = int(os.environ.get("ASFB_PARITY_SAMPLES") or 1)


@pytest.fixture(scope="session")
def model_run() -> Callable[[ModelSpec], dict]:
    cache: dict[str, dict] = {}

    def get(spec: ModelSpec) -> dict:
        if spec.key not in cache:
            try:
                complete = models.get_client(spec)
            except ModelUnavailable as exc:
                pytest.skip(str(exc))
            data = harness.run_model(spec, SAMPLES, complete=complete)
            harness.save_results(data)
            cache[spec.key] = data
        return cache[spec.key]

    return get


def _asfb_summary(model_run: Callable[[ModelSpec], dict], spec: ModelSpec) -> tuple[dict, dict]:
    data = model_run(spec)
    if data["asfb_unavailable"] or data["summary"]["asfb_rate"] is None:
        pytest.skip(f"baseline recorded; {data['asfb_unavailable']}")
    return data, data["summary"]


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.key)
def test_no_canary_leak(model_run, spec: ModelSpec) -> None:
    _, s = _asfb_summary(model_run, spec)
    assert s["leaks"] == 0, f"{s['leaks']} ASFB prompt(s) contained a canary"


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.key)
def test_task_success_parity(model_run, spec: ModelSpec) -> None:
    _, s = _asfb_summary(model_run, spec)
    assert s["base_rate"] > 0, "baseline solved no task; parity would be meaningless"
    assert s["asfb_rate"] >= s["base_rate"] - MARGIN, (
        f"ASFB pass rate {s['asfb_rate']:.3f} below baseline {s['base_rate']:.3f} "
        f"minus margin {MARGIN}"
    )


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.key)
def test_token_overhead(model_run, spec: ModelSpec) -> None:
    _, s = _asfb_summary(model_run, spec)
    overhead = s["token_overhead"]
    assert overhead is not None, "baseline used zero tokens; overhead undefined"
    base_total = s["base_tokens"]["total"]
    asfb_total = s["asfb_tokens"]["total"]
    assert overhead <= TOKEN_OVERHEAD_MAX, (
        f"token overhead {overhead * 100:+.1f}% exceeds {TOKEN_OVERHEAD_MAX * 100:.0f}% "
        f"(baseline {base_total:,} vs ASFB {asfb_total:,} tokens)"
    )
