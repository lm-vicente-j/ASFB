from typing import Any

import pytest
from pydantic import ValidationError

from asfb.types.base import StrictModel
from asfb.types.values import Bucket, BucketScheme, EnumValue


class Probe(StrictModel):
    n: int
    s: str


@pytest.mark.parametrize(("value", "index"), [(0, 0), (1, 1), (2, 2), (255, 8), (256, 9)])
def test_log2_index(value: int, index: int) -> None:
    bucket = Bucket.log2(value)
    assert bucket.scheme is BucketScheme.LOG2
    assert bucket.index == index


def test_log2_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Bucket.log2(-1)


@pytest.mark.parametrize("index", [0, 64])
def test_bucket_index_bounds_inclusive(index: int) -> None:
    assert Bucket(scheme=BucketScheme.LOG2, index=index).index == index


@pytest.mark.parametrize("index", [-1, 65])
def test_bucket_index_out_of_bounds(index: int) -> None:
    with pytest.raises(ValidationError):
        Bucket.model_validate({"scheme": BucketScheme.LOG2, "index": index})


@pytest.mark.parametrize(
    ("vocab", "term"),
    [("test.stub", "btn-primary"), ("test.stub", "a" * 64), ("a" * 64, "!"), ("a1.b2.c3", "~")],
)
def test_enum_value_accepts_shape(vocab: str, term: str) -> None:
    value = EnumValue(vocab=vocab, term=term)
    assert value.vocab == vocab
    assert value.term == term


@pytest.mark.parametrize(
    ("vocab", "term"),
    [
        ("Test.stub", "btn"),
        ("test..stub", "btn"),
        ("1test", "btn"),
        ("a" * 65, "btn"),
        ("test.stub", ""),
        ("test.stub", "a b"),
        ("test.stub", "é"),
        ("test.stub", "a" * 65),
    ],
)
def test_enum_value_rejects_bad_shape(vocab: str, term: str) -> None:
    with pytest.raises(ValidationError):
        EnumValue(vocab=vocab, term=term)


def test_strict_model_forbids_extra_fields() -> None:
    payload: dict[str, Any] = {"n": 1, "s": "x", "extra": 2}
    with pytest.raises(ValidationError):
        Probe.model_validate(payload)


def test_strict_model_is_frozen() -> None:
    probe = Probe.model_validate({"n": 1, "s": "x"})
    name = "n"
    with pytest.raises(ValidationError):
        setattr(probe, name, 2)


@pytest.mark.parametrize(("field", "value"), [("n", "1"), ("n", 1.0), ("n", True), ("s", 1)])
def test_strict_model_rejects_coercion(field: str, value: object) -> None:
    payload: dict[str, Any] = {"n": 1, "s": "x", field: value}
    with pytest.raises(ValidationError):
        Probe.model_validate(payload)


def test_bucket_rejects_string_index() -> None:
    payload: dict[str, Any] = {"scheme": BucketScheme.LOG2, "index": "1"}
    with pytest.raises(ValidationError):
        Bucket.model_validate(payload)
