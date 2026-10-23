"""Session outputs for a project whose file contents, file names and directory names carry a canary.

The listing, the views, the emitted JSON, error texts and captured output must contain no form of
the canary, both in process (isolate=False) and through the isolated worker (isolate=True).
"""

from pathlib import Path

import pytest

from asfb.errors import AsfbError
from asfb.session import Listing, Sanitized, Session
from asfb.types.key import SessionKey
from tests.canary.harness import find_leaks, new_canary, variants


@pytest.mark.parametrize("isolate", [False, True], ids=["in-process", "isolated"])
@pytest.mark.parametrize("suffix", [".html", ".txt", ".md"])
def test_session_outputs_carry_no_canary(
    isolate: bool,
    suffix: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    canary = new_canary()
    assert variants(canary)
    root = tmp_path / "project"
    folder = root / f"dir-{canary}"
    folder.mkdir(parents=True)
    content = {
        ".html": f'<h1>{canary}</h1><p title="{canary}">body</p>',
        ".txt": f"{canary}\n\nbody {canary}\n",
        ".md": f"# {canary}\n\n[link](https://x.example/{canary})\n",
    }[suffix]
    (folder / f"page-{canary}{suffix}").write_text(content, encoding="utf-8")
    session = Session([str(root)], key=SessionKey.generate(), isolate=isolate)
    outputs: list[str] = []
    errors: list[str] = []

    listing = session.sanitize(".")
    assert isinstance(listing, Listing)
    outputs.extend([listing.json, str(listing.view)])
    outputs.extend(str(failure.doc) for failure in listing.failed)
    target = str(listing.view).split(" ", 1)[1].split("\n", 1)[0]
    document = session.sanitize(target)
    assert isinstance(document, Sanitized)
    outputs.extend([document.json, str(document.view), str(document.ref)])
    assert canary not in str(document.view)

    try:
        session.sanitize(f"dir-{canary}/missing-{canary}.html")
    except AsfbError as error:
        errors.extend([str(error), repr(error), repr(error.args)])

    captured = capfd.readouterr()
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []
