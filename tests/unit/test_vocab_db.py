"""The db.* vocabularies: their terms, their metadata and their place in the egress registry."""

import json
from importlib import resources
from typing import Any

import pytest

from asfb.vocab import ALL_IDS, DB_IDS, _build, load, registry


def _raw(vocab_id: str) -> dict[str, Any]:
    text = (resources.files("asfb.vocab") / "data" / f"{vocab_id}.json").read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def test_db_ids_are_registered() -> None:
    assert set(DB_IDS) <= set(ALL_IDS)
    assert set(DB_IDS) <= set(registry())


def test_declared_types_and_internal_names_are_present() -> None:
    assert "VARCHAR" in load("db.sqltypes").terms
    assert "sqlite_sequence" in load("db.sqlite").terms
    assert {"TEXT", "NUMERIC", "INTEGER", "REAL", "BLOB"} <= load("db.sqlite").terms


@pytest.mark.parametrize("vocab_id", DB_IDS)
def test_terms_are_sorted_unique_and_have_no_underscore_names(vocab_id: str) -> None:
    terms: list[str] = _raw(vocab_id)["terms"]
    assert terms == sorted(terms)
    assert len(set(terms)) == len(terms)
    assert not [term for term in terms if term.startswith("_")]


@pytest.mark.parametrize("vocab_id", DB_IDS)
def test_metadata_records_the_source_tool_and_version(vocab_id: str) -> None:
    raw = _raw(vocab_id)
    assert raw["source"] == {"tool": "SQLite", "version": "3.54.0"}
    assert raw["version"] == 1


@pytest.mark.parametrize("vocab_id", DB_IDS)
def test_checksum_matches_the_terms(vocab_id: str) -> None:
    raw = _raw(vocab_id)
    rebuilt = _build(vocab_id, json.dumps(raw))
    assert not isinstance(rebuilt, str)
    assert rebuilt.terms == frozenset(raw["terms"])


def test_membership_is_exact_string_equality() -> None:
    sqltypes = load("db.sqltypes")
    assert "varchar" not in sqltypes.terms
    assert "VARCHAR(255)" not in sqltypes.terms
    assert "sqlite_master" not in load("db.sqlite").terms
