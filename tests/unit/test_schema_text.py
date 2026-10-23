"""The v1 text model: closed shapes, limits, and the committed JSON Schema."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from asfb.schema import TEXT_RESOURCE
from asfb.schema.doc import Profile
from asfb.schema.text import MAX_INDENT, MAX_LINES, TextLine, TextV1

SAMPLE_SHAPE = "t_0123456789abcdef"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "src" / "asfb" / "schema" / TEXT_RESOURCE


def _doc(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": Profile.STRICT,
        "vocab": (),
        "eol": "lf",
        "paragraphs": ({"lines": ({"indent": 0, "text": SAMPLE_SHAPE},)},),
    }
    base.update(overrides)
    return base


def test_valid_document_is_accepted() -> None:
    model = TextV1.model_validate(_doc(), strict=True)
    assert model.schema_id == "asfb.text/1"
    assert model.profile is Profile.STRICT


def test_extra_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        TextV1.model_validate(_doc(extra=1), strict=True)


def test_vocabulary_must_be_empty() -> None:
    with pytest.raises(ValidationError):
        TextV1.model_validate(_doc(vocab=({"id": "url.schemes", "version": 1},)), strict=True)


@pytest.mark.parametrize("eol", ["lf", "crlf", "cr", "mixed", "none"])
def test_every_eol_kind_is_accepted(eol: str) -> None:
    assert TextV1.model_validate(_doc(eol=eol), strict=True).eol == eol


def test_unknown_eol_is_refused() -> None:
    with pytest.raises(ValidationError):
        TextV1.model_validate(_doc(eol="nl"), strict=True)


def test_a_paragraph_needs_a_line() -> None:
    with pytest.raises(ValidationError):
        TextV1.model_validate(_doc(paragraphs=({"lines": ()},)), strict=True)


def test_indent_range_is_closed() -> None:
    TextLine.model_validate({"indent": MAX_INDENT, "text": SAMPLE_SHAPE}, strict=True)
    with pytest.raises(ValidationError):
        TextLine.model_validate({"indent": MAX_INDENT + 1, "text": SAMPLE_SHAPE}, strict=True)
    with pytest.raises(ValidationError):
        TextLine.model_validate({"indent": -1, "text": SAMPLE_SHAPE}, strict=True)


def test_text_must_be_a_token_shape() -> None:
    with pytest.raises(ValidationError):
        TextLine.model_validate({"indent": 0, "text": "plain"}, strict=True)


def test_too_many_paragraphs_are_refused() -> None:
    one = {"lines": ({"indent": 0, "text": SAMPLE_SHAPE},)}
    with pytest.raises(ValidationError):
        TextV1.model_validate(_doc(paragraphs=(one,) * (MAX_LINES + 1)), strict=True)


def test_committed_schema_equals_the_model() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == TextV1.model_json_schema()
