"""A canary in paths, file names and content never reaches stdout or stderr, even on errors."""

from pathlib import Path

import pytest

from asfb.cli import main
from tests.canary.harness import new_canary


@pytest.mark.parametrize("argv_kind", ["sanitize", "missing", "usage"])
def test_canary_never_reaches_stdout_or_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv_kind: str
) -> None:
    canary = new_canary()
    site = tmp_path / f"site_{canary}"
    site.mkdir()
    (site / f"{canary}.html").write_text(f"<p>{canary}</p>", encoding="utf-8")
    if argv_kind == "sanitize":
        status = main(["sanitize", str(site)])
    elif argv_kind == "missing":
        status = main(["sanitize", str(site / f"no_{canary}.html")])
    else:
        status = main(["sanitize", str(site), "--profile", canary])
    captured = capsys.readouterr()
    assert status in (0, 1, 2)
    assert canary not in captured.out
    assert canary not in captured.err
