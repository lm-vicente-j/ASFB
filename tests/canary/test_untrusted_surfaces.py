import json
import logging
import pickle
from collections.abc import Callable

import pytest

from asfb.types.untrusted import Untrusted
from tests.canary.harness import find_leaks, new_canary

_LOG = logging.getLogger("asfb.canary")


def _probe(action: Callable[[], object]) -> list[str]:
    try:
        result = action()
    except Exception as error:
        if not isinstance(error, TypeError):
            raise
        return [str(error), repr(error), repr(error.args)]
    return [repr(result)]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(repr, id="repr"),
        pytest.param(str, id="str"),
        pytest.param(format, id="format"),
        pytest.param(lambda u: f"{u}", id="fstring"),
        pytest.param(lambda u: f"{u!r}", id="fstring-repr"),
        pytest.param(lambda u: f"{u:>40}", id="fstring-spec"),
        pytest.param(lambda u: "%s" % (u,), id="percent"),  # noqa: UP031
        pytest.param(json.dumps, id="json"),
        pytest.param(pickle.dumps, id="pickle"),
        pytest.param(lambda u: pickle.dumps(u, protocol=0), id="pickle-protocol-0"),
    ],
)
def test_surface_raises_or_redacts(
    operation: Callable[[Untrusted[str]], object], capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    texts = _probe(lambda: operation(Untrusted(canary)))
    captured = capfd.readouterr()
    assert find_leaks([*texts, captured.out, captured.err], [canary]) == []


def test_logging_never_exposes(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    value = Untrusted(canary)
    texts = _probe(lambda: _LOG.warning("value=%s", value))
    texts += _probe(lambda: _LOG.warning("redacted=%r", value))
    captured = capfd.readouterr()
    assert find_leaks([*texts, caplog.text, captured.out, captured.err], [canary]) == []


def test_print_never_exposes(capfd: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    value = Untrusted(canary)
    texts = _probe(lambda: print(value))  # noqa: T201
    texts += _probe(lambda: print(repr(value)))  # noqa: T201
    captured = capfd.readouterr()
    assert find_leaks([*texts, captured.out, captured.err], [canary]) == []
