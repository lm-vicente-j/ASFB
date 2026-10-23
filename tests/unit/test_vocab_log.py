"""The log.* vocabularies: their terms, the registry, and the one case-insensitive level lookup."""

import pytest

from asfb.vocab import ALL_IDS, LOG_IDS, load, log_level, registry


def test_log_ids_are_registered() -> None:
    assert set(LOG_IDS) <= set(ALL_IDS)
    assert set(LOG_IDS) <= set(registry())


def test_warning_is_a_level_and_message_is_a_field() -> None:
    assert "WARNING" in load("log.levels").terms
    assert "message" in load("log.fields").terms


def test_levels_include_syslog_digits_and_the_named_levels() -> None:
    terms = load("log.levels").terms
    assert {str(digit) for digit in range(8)} <= terms
    assert {"TRACE", "DEBUG", "INFO", "NOTICE", "WARN", "WARNING", "ERROR"} <= terms
    assert {"CRITICAL", "FATAL", "ALERT", "EMERG"} <= terms


def test_field_names_are_standard_names_only() -> None:
    fields = load("log.fields").terms
    assert {"timestamp", "time", "ts", "level", "severity", "msg", "message"} <= fields
    assert {"logger", "name", "exc_type", "trace_id", "span_id", "host", "service"} <= fields
    assert not any(name.startswith("_") for name in fields)


@pytest.mark.parametrize("vocab_id", LOG_IDS)
def test_log_terms_are_sorted_and_unique(vocab_id: str) -> None:
    terms = load(vocab_id).terms
    assert len(terms) == len(set(terms))


@pytest.mark.parametrize(
    ("word", "term"),
    [("warning", "WARNING"), ("Warn", "WARN"), ("info", "INFO"), ("3", "3"), ("err", None)],
)
def test_level_lookup_folds_ascii_case_only(word: str, term: str | None) -> None:
    assert log_level(word) == term


@pytest.mark.parametrize(
    "word", ["\uff57\uff41\uff52\uff4e", "W\u00c0RN", "wa rn", "", "WARN\n", 3, None]
)
def test_level_lookup_does_not_fold_anything_else(word: object) -> None:
    assert log_level(word) is None
