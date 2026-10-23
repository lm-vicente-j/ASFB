"""The py.* vocabularies: content, metadata and the registry they join."""

import json
import keyword
from importlib import resources
from typing import Any

import pytest

from asfb.errors import ErrorCode
from asfb.vocab import ALL_IDS, PY_IDS, FrozenVocabulary, _build, py_registry, registry

NEW_IDS = ("py.keywords", "py.builtins", "py.stdlib")


def _raw(vocab_id: str) -> dict[str, Any]:
    text = (resources.files("asfb.vocab") / "data" / f"{vocab_id}.json").read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


@pytest.mark.parametrize("vocab_id", NEW_IDS)
def test_new_vocabularies_are_sorted_and_have_no_underscore_names(vocab_id: str) -> None:
    terms: list[str] = _raw(vocab_id)["terms"]
    assert terms == sorted(terms)
    assert len(set(terms)) == len(terms)
    assert not [term for term in terms if term.startswith("_")]


@pytest.mark.parametrize("vocab_id", NEW_IDS)
def test_new_vocabularies_record_their_source_tool_and_version(vocab_id: str) -> None:
    source = _raw(vocab_id)["source"]
    assert source["tool"] == "CPython"
    assert source["version"]


def test_keywords_are_the_keyword_and_soft_keyword_lists_without_underscore() -> None:
    expected = {word for word in [*keyword.kwlist, *keyword.softkwlist] if word != "_"}
    assert set(_raw("py.keywords")["terms"]) == expected
    assert {"def", "match", "case", "type", "None"} <= expected


def _frozen(vocab_id: str) -> FrozenVocabulary:
    entry = py_registry()[vocab_id]
    assert isinstance(entry, FrozenVocabulary)
    return entry


def test_def_len_and_the_path_join_lexemes_are_public() -> None:
    assert "def" in _frozen("py.keywords")
    assert "len" in _frozen("py.builtins")
    assert {"os", "path", "join"} <= _frozen("py.stdlib").terms


def test_builtins_hold_public_names_and_no_dunders() -> None:
    builtins = set(_raw("py.builtins")["terms"])
    assert {"print", "ValueError"} <= builtins
    assert "WARNING" not in builtins
    assert not [term for term in builtins if term.startswith("__")]


def test_stdlib_drops_modules_removed_by_python_3_13() -> None:
    stdlib = set(_raw("py.stdlib")["terms"])
    assert "telnetlib" not in stdlib
    assert "json" in stdlib


def test_membership_is_exact_string_equality() -> None:
    keywords = _frozen("py.keywords")
    assert "def" in keywords
    assert "DEF" not in keywords
    assert "d" + chr(0x0435) + "f" not in keywords  # Cyrillic e: no NFKC folding


def test_python_vocabularies_are_part_of_the_egress_registry() -> None:
    assert set(PY_IDS) <= set(ALL_IDS)
    assert set(PY_IDS) <= set(registry())


@pytest.mark.parametrize(
    "source",
    [
        {"tool": "CPython"},
        {"tool": "CPython", "version": "3.12", "extra": "x"},
        {"tool": "CPython", "version": 3},
        {"tool": "CPython", "version": "3 .12"},
        "CPython 3.12",
    ],
)
def test_malformed_source_metadata_is_refused(source: object) -> None:
    text = json.dumps({"id": "py.keywords", "version": 1, "terms": ["def"], "source": source})
    assert _build("py.keywords", text) is ErrorCode.E_VOCAB_DATA


def test_valid_source_metadata_loads() -> None:
    text = json.dumps(
        {
            "id": "py.keywords",
            "version": 1,
            "terms": ["def"],
            "source": {"tool": "CPython", "version": "3.12"},
        }
    )
    outcome = _build("py.keywords", text)
    assert isinstance(outcome, FrozenVocabulary)
    assert "def" in outcome
