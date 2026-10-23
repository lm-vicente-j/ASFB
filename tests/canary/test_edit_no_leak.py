"""Edit outputs for documents whose text, attribute and file name carry a canary, in HTML,
markdown and plain text.

The agent only sees tokens (read from the view, as it would), so the canary never comes back from
a successful edit. Every refusal must also stay free of it: results, views, refs, error texts and
captured output, both in process (isolate=False) and through the isolated worker (isolate=True).
"""

import re
from pathlib import Path

import pytest

from asfb.errors import AsfbError
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey
from tests.canary.harness import find_leaks, new_canary, variants

TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")
# suffix, document text, old and new of the successful edit, and an old text that no longer
# matches after it. {c} is the canary, {t} the first token of the view, {u} the second.
CASES = [
    (
        ".html",
        '<h1>{c}</h1><p title="{c}">body</p>',
        "<h1>{t}</h1>",
        "<h2>{t}</h2>",
        "<h1>{t}</h1>",
    ),
    (".md", "# {c}\n\nbody {c}\n", "# {t}", "## {t}", "> {t}"),
    # The canary is the whole first line of the text; its token is unique in the file.
    (".txt", "{c}\n\nbody other\n", "{t}", "{u}", "{t}"),
]


@pytest.mark.parametrize("isolate", [False, True], ids=["in-process", "isolated"])
@pytest.mark.parametrize(
    ("suffix", "body", "old", "new", "missing"), CASES, ids=["html", "md", "txt"]
)
def test_edit_outputs_carry_no_canary(
    isolate: bool,
    suffix: str,
    body: str,
    old: str,
    new: str,
    missing: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    canary = new_canary()
    assert variants(canary)
    root = tmp_path / "project"
    root.mkdir()
    path = root / f"page-{canary}{suffix}"
    path.write_text(body.replace("{c}", canary), encoding="utf-8")
    session = Session([str(root)], key=SessionKey.generate(), isolate=isolate)
    stored = session.sanitize(path.name)
    assert isinstance(stored, Sanitized)
    tokens = TOKEN_RE.findall(str(stored.view))
    assert len(tokens) >= 2
    tok, second = tokens[0], tokens[1]
    outputs: list[str] = [stored.json, str(stored.view), str(stored.ref)]
    errors: list[str] = []

    def refuse(ref: str, bad_old: str, bad_new: str) -> None:
        try:
            session.edit(ref, bad_old, bad_new)
        except AsfbError as error:
            errors.extend([str(error), repr(error), repr(error.args)])
        else:
            raise AssertionError("the edit was expected to be refused")

    def fill(text: str) -> str:
        return text.replace("{t}", tok).replace("{u}", second)

    edited = session.edit(str(stored.ref), fill(old), fill(new))
    outputs.extend([edited.json, str(edited.view), str(edited.ref)])
    assert canary not in str(edited.view)

    refuse(str(stored.ref), fill(old), fill(new))  # stale revision
    refuse(str(edited.ref), canary, tok)  # literal non-public word in old
    refuse(str(edited.ref), tok, canary)  # literal non-public word in new
    refuse(str(edited.ref), fill(missing), tok)  # no match any more
    refuse(str(edited.ref), "t_0123456789abcdef", tok)  # unknown token

    captured = capfd.readouterr()
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []
