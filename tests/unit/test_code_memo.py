"""The memo and the batch validation change how fast a Python source is censored, never the output.

With the memo off (the same code path without the per-document memo), the model, the pairs, the
spans, the emitted JSON and the censored view are equal, for both profiles and with a file name."""

from typing import Literal

import pytest

from asfb.api import sanitize_python
from asfb.egress import gate
from asfb.ingest.code import read_text
from asfb.sanitize import code as code_module
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey

KEY = SessionKey(bytes(range(32)))
SOURCE = '''\
"""Doc."""
import os


class Box:
    count = 0  # the counter

    def put(self, item, *, size=10):
        label = f"{item}:{size!r}" + "x" * 3
        if item is None or size >= 0b11:
            return [label, f"{self.count}", label]
        for index in range(size):
            self.count += index ** 2
        return os.path.join(label, b'raw' rb"\\n", label)
'''


def _outputs(
    monkeypatch: pytest.MonkeyPatch, memo: bool, profile: Literal["strict", "diffable"]
) -> tuple[object, ...]:
    monkeypatch.setattr(gate, "_MEMOIZE", memo)
    monkeypatch.setattr(code_module, "_MEMOIZE", memo)
    doc = sanitize_python(SOURCE, profile=profile, key=KEY)
    census = code_module.censor_code(KEY, read_text(SOURCE), Profile(profile), "box.py")
    return (
        doc.json,
        doc.views.censored(),
        census.model.model_dump_json(),
        census.pairs.items(),
        census.spans,
    )


@pytest.mark.parametrize("profile", ["strict", "diffable"])
def test_memo_on_and_off_give_the_same_model_pairs_spans_and_text(
    monkeypatch: pytest.MonkeyPatch, profile: Literal["strict", "diffable"]
) -> None:
    with_memo = _outputs(monkeypatch, True, profile)
    without_memo = _outputs(monkeypatch, False, profile)
    assert with_memo == without_memo
