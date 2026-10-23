"""The v1 markdown model: closed shapes, discriminators, table rectangularity, limits, and the
committed JSON Schema."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from asfb.schema import MD_RESOURCE
from asfb.schema.doc import Profile
from asfb.schema.md import MAX_BLOCKS, MAX_INLINES, MdV1
from asfb.vocab import FrozenVocabulary, md_registry

SAMPLE = "t_0123456789abcdef"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "src" / "asfb" / "schema" / MD_RESOURCE
VOCAB = (
    {"id": "md.languages", "version": 1},
    {"id": "url.schemes", "version": 1},
)


def _doc(blocks: tuple[object, ...] = ()) -> dict[str, object]:
    return {"profile": Profile.STRICT, "vocab": VOCAB, "blocks": blocks}


def _para(text: str = SAMPLE) -> dict[str, object]:
    return {
        "kind": "paragraph",
        "quote": 0,
        "lines": (({"kind": "text", "text": text},),),
    }


def test_valid_document_is_accepted() -> None:
    model = MdV1.model_validate(_doc((_para(),)), strict=True)
    assert model.schema_id == "asfb.md/1"


def test_extra_fields_are_refused() -> None:
    block = {**_para(), "extra": 1}
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((block,)), strict=True)


def test_unknown_block_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc(({"kind": "image", "quote": 0},)), strict=True)


def test_inline_discriminator_selects_the_closed_shape() -> None:
    with pytest.raises(ValidationError):
        MdV1.model_validate(
            _doc(
                (
                    {
                        "kind": "paragraph",
                        "quote": 0,
                        "lines": (({"kind": "emphasis", "text": SAMPLE},),),
                    },
                )
            ),
            strict=True,
        )


def test_heading_level_is_one_to_six() -> None:
    heading = {"kind": "heading", "quote": 0, "level": 7, "inlines": ()}
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((heading,)), strict=True)


def test_quote_depth_is_capped_at_sixteen() -> None:
    rule = {"kind": "rule", "quote": 17}
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((rule,)), strict=True)


def test_table_with_uneven_columns_is_refused() -> None:
    table = {
        "kind": "table",
        "quote": 0,
        "align": ("none", "left"),
        "header": (({"kind": "text", "text": SAMPLE},),),
        "rows": (),
    }
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((table,)), strict=True)


def test_table_row_with_wrong_width_is_refused() -> None:
    cell = (({"kind": "text", "text": SAMPLE},),)
    table = {
        "kind": "table",
        "quote": 0,
        "align": ("none", "left"),
        "header": (cell[0], cell[0]),
        "rows": (((cell[0],)),),
    }
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((table,)), strict=True)


def test_table_of_matching_widths_is_accepted() -> None:
    cell = ({"kind": "text", "text": SAMPLE},)
    table = {
        "kind": "table",
        "quote": 0,
        "align": ("center",),
        "header": (cell,),
        "rows": ((cell,),),
    }
    assert MdV1.model_validate(_doc((table,)), strict=True).blocks


def test_code_language_is_an_enum_value_or_a_token() -> None:
    fence = {
        "kind": "codeblock",
        "quote": 0,
        "fence": "backtick",
        "lang": {"vocab": "md.languages", "term": "python"},
        "text": SAMPLE,
    }
    assert MdV1.model_validate(_doc((fence,)), strict=True).blocks


def test_block_count_is_capped() -> None:
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((_para(),) * (MAX_BLOCKS + 1)), strict=True)


def test_inline_count_is_capped() -> None:
    inline = {"kind": "text", "text": SAMPLE}
    block = {"kind": "paragraph", "quote": 0, "lines": ((inline,) * (MAX_INLINES + 1),)}
    with pytest.raises(ValidationError):
        MdV1.model_validate(_doc((block,)), strict=True)


def test_md_languages_vocabulary_is_sorted_v1() -> None:
    entry = md_registry()["md.languages"]
    assert isinstance(entry, FrozenVocabulary)
    assert entry.version == 1
    assert "python" in entry.terms
    assert "bash" in entry.terms


def test_committed_schema_equals_the_model() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == MdV1.model_json_schema()
