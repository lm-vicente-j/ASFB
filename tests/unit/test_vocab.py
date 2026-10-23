import dataclasses
import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest

from asfb import vocab
from asfb.errors import AsfbError, ErrorCode
from asfb.vocab import ALL_IDS, HTML_IDS, VOID_ELEMENTS, FrozenVocabulary, html_registry, load

TERM_RE = re.compile(r"[A-Za-z0-9_.:/+-]{1,64}")
DATA_DIR = Path(vocab.__file__).parent / "data"
SAMPLE_ID = "test.sample"
VALID_SAMPLE = {"id": SAMPLE_ID, "version": 1, "terms": ["a", "b"]}


def _code_of(vocab_id: str) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        load(vocab_id)
    error = info.value
    assert error.args == (error.code.value,)
    assert error.__cause__ is None
    assert error.__context__ is None
    return error.code


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vocab, "_DATA_DIR", tmp_path)
    return tmp_path


def _write(directory: Path, text: str, vocab_id: str = SAMPLE_ID) -> None:
    (directory / f"{vocab_id}.json").write_text(text, encoding="utf-8")


def test_valid_sample_loads(data_dir: Path) -> None:
    _write(data_dir, json.dumps(VALID_SAMPLE))
    loaded = load(SAMPLE_ID)
    assert isinstance(loaded, FrozenVocabulary)
    assert loaded.id == SAMPLE_ID
    assert loaded.version == 1
    assert "a" in loaded
    assert "c" not in loaded


def test_loaded_vocabulary_is_immutable(data_dir: Path) -> None:
    _write(data_dir, json.dumps(VALID_SAMPLE))
    loaded = load(SAMPLE_ID)
    name = "version"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(loaded, name, 2)


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param({**VALID_SAMPLE, "id": "other.id"}, id="id-differs-from-filename"),
        pytest.param({**VALID_SAMPLE, "version": 0}, id="version-zero"),
        pytest.param({**VALID_SAMPLE, "version": 65536}, id="version-over-max"),
        pytest.param({**VALID_SAMPLE, "version": "1"}, id="version-string"),
        pytest.param({**VALID_SAMPLE, "version": True}, id="version-bool"),
        pytest.param({"id": SAMPLE_ID, "terms": ["a"]}, id="version-missing"),
        pytest.param({**VALID_SAMPLE, "terms": ["b", "a"]}, id="terms-unsorted"),
        pytest.param({**VALID_SAMPLE, "terms": ["a", "a"]}, id="terms-duplicate"),
        pytest.param({**VALID_SAMPLE, "terms": ["a b"]}, id="term-with-space"),
        pytest.param({**VALID_SAMPLE, "terms": ["a\n"]}, id="term-trailing-newline"),
        pytest.param({**VALID_SAMPLE, "terms": ["x" * 65]}, id="term-too-long"),
        pytest.param({**VALID_SAMPLE, "terms": [""]}, id="term-empty"),
        pytest.param({**VALID_SAMPLE, "terms": ["café"]}, id="term-non-ascii"),
        pytest.param({**VALID_SAMPLE, "terms": "a"}, id="terms-not-a-list"),
        pytest.param({**VALID_SAMPLE, "terms": [1]}, id="term-not-a-string"),
        pytest.param({**VALID_SAMPLE, "extra": 1}, id="unknown-key"),
        pytest.param(
            {**VALID_SAMPLE, "by_attribute": {"*": {"x": ["zz"]}}},
            id="by-attribute-value-not-in-terms",
        ),
        pytest.param(
            {**VALID_SAMPLE, "by_attribute": {"*": ["a"]}},
            id="by-attribute-wrong-shape",
        ),
    ],
)
def test_bad_data_is_refused(data_dir: Path, doc: dict[str, object]) -> None:
    _write(data_dir, json.dumps(doc))
    assert _code_of(SAMPLE_ID) is ErrorCode.E_VOCAB_DATA


@pytest.mark.parametrize(
    "text",
    ["{", "[]", "", "null", "﻿{}"],
    ids=["truncated-json", "list", "empty", "null", "bom-and-empty-object"],
)
def test_malformed_json_is_refused(data_dir: Path, text: str) -> None:
    _write(data_dir, text)
    assert _code_of(SAMPLE_ID) is ErrorCode.E_VOCAB_DATA


def test_missing_file_is_refused(data_dir: Path) -> None:
    assert _code_of("test.missing") is ErrorCode.E_VOCAB_DATA


@pytest.mark.parametrize("vocab_id", ["../escape", "Upper.case", "a" * 65, "", "a/b", "a..b"])
def test_invalid_ids_are_refused_before_any_file_access(data_dir: Path, vocab_id: str) -> None:
    assert _code_of(vocab_id) is ErrorCode.E_VOCAB_DATA


def test_good_by_attribute_loads(data_dir: Path) -> None:
    doc = {**VALID_SAMPLE, "by_attribute": {"*": {"a": ["a"]}, "div": {"b": ["b"]}}}
    _write(data_dir, json.dumps(doc))
    loaded = load(SAMPLE_ID)
    assert loaded.values_for("div", "b") == frozenset({"b"})
    assert loaded.values_for("p", "a") == frozenset({"a"})


def test_by_attribute_element_entry_wins_over_star() -> None:
    table = FrozenVocabulary(
        id="test.table",
        version=1,
        terms=frozenset({"text", "ltr", "email"}),
        by_attribute=MappingProxyType(
            {
                "*": MappingProxyType({"type": frozenset({"text"}), "dir": frozenset({"ltr"})}),
                "input": MappingProxyType({"type": frozenset({"email"})}),
            }
        ),
    )
    assert table.values_for("input", "type") == frozenset({"email"})
    assert table.values_for("div", "type") == frozenset({"text"})
    assert table.values_for("input", "dir") == frozenset({"ltr"})
    assert table.values_for("div", "missing") is None


@pytest.mark.parametrize("vocab_id", HTML_IDS)
def test_shipped_files_match_format(vocab_id: str) -> None:
    doc = json.loads((DATA_DIR / f"{vocab_id}.json").read_text(encoding="utf-8"))
    assert set(doc) <= {"id", "version", "terms", "by_attribute"}
    assert {"id", "version", "terms"} <= set(doc)
    assert doc["id"] == vocab_id
    assert doc["version"] == 1
    terms = doc["terms"]
    assert terms == sorted(set(terms))
    assert all(TERM_RE.fullmatch(term) for term in terms)


def test_shipped_data_directory_holds_exactly_the_shipped_files() -> None:
    names = {path.name for path in DATA_DIR.glob("*.json")}
    assert names == {f"{vocab_id}.json" for vocab_id in ALL_IDS}


@pytest.mark.parametrize("vocab_id", HTML_IDS)
def test_shipped_vocabularies_load(vocab_id: str) -> None:
    loaded = load(vocab_id)
    assert loaded.id == vocab_id
    assert loaded.version == 1
    assert len(loaded.terms) > 0


@pytest.mark.parametrize(
    ("vocab_id", "term"),
    [
        ("css.bootstrap", "btn"),
        ("css.bootstrap", "btn-primary"),
        ("css.bootstrap", "row"),
        ("css.bootstrap", "active"),
        ("css.bootstrap", "text-bg-secondary"),
        ("css.bootstrap", "carousel"),
        ("html.elements", "div"),
        ("html.elements", "form"),
        ("html.elements", "a"),
        ("html.elements", "script"),
        ("html.attributes", "href"),
        ("html.attributes", "class"),
        ("html.attributes", "aria-label"),
        ("html.attributes", "onclick"),
        ("url.schemes", "https"),
        ("html.attributes", "onunload"),
    ],
)
def test_required_samples_are_present(vocab_id: str, term: str) -> None:
    assert term in load(vocab_id)


def test_review_additions_are_enumerated_per_attribute() -> None:
    values = load("html.values")
    fetchpriority = values.values_for("img", "fetchpriority")
    assert fetchpriority is not None
    assert "high" in fetchpriority
    formmethod = values.values_for("input", "formmethod")
    assert formmethod is not None
    assert "post" in formmethod


def test_email_is_an_enumerated_input_type() -> None:
    values = load("html.values")
    assert "email" in values
    allowed = values.values_for("input", "type")
    assert allowed is not None
    assert "email" in allowed


@pytest.mark.parametrize("term", ["send-invoice", "invoice", "onsearch"])
def test_forbidden_samples_are_absent_from_every_vocabulary(term: str) -> None:
    for loaded in html_registry().values():
        assert term not in loaded


def test_html_registry_holds_the_five_vocabularies() -> None:
    assert set(html_registry()) == set(HTML_IDS)


def test_html_registry_is_cached() -> None:
    assert html_registry() is html_registry()


def test_void_elements_are_the_whatwg_list() -> None:
    assert VOID_ELEMENTS == frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "track",
            "wbr",
        }
    )


def test_html_values_map_input_type_and_button_type() -> None:
    values = load("html.values")
    assert values.values_for("button", "type") == frozenset({"button", "reset", "submit"})
    assert values.values_for("ol", "type") is not None
    assert values.values_for("p", "type") is None


@pytest.mark.parametrize(
    "term",
    ["t_0123456789abcdef", "d_0123456789abcdef", "d_fedcba9876543210"],
)
def test_terms_shaped_like_tokens_or_doc_ids_are_refused(data_dir: Path, term: str) -> None:
    # The sandbox client rewrites every string of this shape; a vocabulary term must never be one.
    _write(data_dir, json.dumps({"id": SAMPLE_ID, "version": 1, "terms": ["a", term]}))
    assert _code_of(SAMPLE_ID) is ErrorCode.E_VOCAB_DATA


def test_token_shaped_value_in_by_attribute_is_refused(data_dir: Path) -> None:
    doc = {
        **VALID_SAMPLE,
        "terms": ["a", "t_0123456789abcdef"],
        "by_attribute": {"*": {"x": ["t_0123456789abcdef"]}},
    }
    _write(data_dir, json.dumps(doc))
    assert _code_of(SAMPLE_ID) is ErrorCode.E_VOCAB_DATA


def test_near_token_shapes_are_still_valid_terms(data_dir: Path) -> None:
    # One hex digit short, and upper-case hex: not the token shape, so allowed.
    terms = ["t_0123456789ABCDEF", "t_0123456789abcde"]
    _write(data_dir, json.dumps({"id": SAMPLE_ID, "version": 1, "terms": terms}))
    loaded = load(SAMPLE_ID)
    assert loaded.terms == frozenset(terms)
