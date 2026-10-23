"""A canary in a test name, an assert message, a print and a file name never reaches run output."""

from pathlib import Path

from asfb.session import Session
from tests.canary.harness import new_canary
from tests.junit_samples import KEY


def test_canary_is_absent_from_run_output_and_errors(tmp_path: Path) -> None:
    canary = new_canary()
    tests = tmp_path / "proj" / "tests"
    tests.mkdir(parents=True)
    body = f"def test_{canary}():\n    print('{canary}')\n    assert 1 == 2, '{canary}'\n"
    (tests / f"test_{canary}.py").write_text(body, encoding="utf-8")
    import sys

    session = Session([str(tmp_path / "proj")], key=KEY, python=sys.executable, run_network="host")
    result = session.run("pytest", ["tests"])
    assert canary not in result.view
    assert canary not in result.json
    assert canary not in repr(result)
