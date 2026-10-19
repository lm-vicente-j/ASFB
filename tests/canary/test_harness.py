import pytest

from asfb.types.key import SessionKey
from tests.canary.harness import (
    CORPUS,
    PLACEHOLDER,
    Carrier,
    find_leaks,
    load_carriers,
    m0_pipeline,
    new_canary,
    plant,
    variants,
)

CARRIERS = load_carriers()


def _identity(text: str, key: SessionKey) -> str:
    return text


def test_corpus_has_unique_ids_and_placeholders() -> None:
    ids = [carrier.id for carrier in CARRIERS]
    assert len(ids) >= 20
    assert len(set(ids)) == len(ids)
    assert all(PLACEHOLDER in carrier.template for carrier in CARRIERS)


def test_corpus_is_plain_ascii() -> None:
    assert CORPUS.read_bytes().isascii()


def test_variants_cover_all_encodings() -> None:
    canary = new_canary()
    forms = variants(canary)
    assert forms[0] == canary
    assert forms[1] == canary.upper()
    assert forms[2] == canary.encode().hex()
    assert forms[3] == canary.encode().hex().upper()
    assert len(set(forms)) == len(forms)


@pytest.mark.parametrize("carrier", CARRIERS, ids=lambda carrier: carrier.id)
def test_identity_pipeline_is_flagged_for_every_variant(carrier: Carrier) -> None:
    canary = new_canary()
    key = SessionKey.generate()
    for form in variants(canary):
        out = _identity(plant(carrier.template, form), key)
        assert find_leaks([out], [canary]), f"variant {form!r} not flagged"


def test_bytes_blob_is_flagged() -> None:
    canary = new_canary()
    assert find_leaks([plant("x @@CANARY@@", canary).encode("utf-8")], [canary]) == [canary]


def test_false_positive_check_no_canary_no_leak() -> None:
    canary = new_canary()
    out = m0_pipeline("nothing secret here", SessionKey.generate())
    assert find_leaks([out, "", b""], [canary]) == []


def test_planted_canary_is_detected_in_clean_output() -> None:
    canary = new_canary()
    out = m0_pipeline("nothing secret here", SessionKey.generate())
    assert find_leaks([out, plant("leak: @@CANARY@@", canary)], [canary]) == [canary]
