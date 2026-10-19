import re
from typing import Any, Final, Literal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from asfb.egress.gate import emit
from asfb.errors import EgressError, ErrorCode
from asfb.sanitize.tokens import tokenize
from asfb.schema.doc import DocV1, Leaf, Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.types.values import Bucket, EnumValue
from tests.stubs import STUB_VOCAB

REGISTRY = {STUB_VOCAB.id: STUB_VOCAB}
STUB_REF = VocabRef(id=STUB_VOCAB.id, version=1)
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")
NO_SURROGATES: Final[tuple[Literal["Cs"]]] = ("Cs",)
PLAIN_TEXT = st.text(
    alphabet=st.characters(exclude_categories=NO_SURROGATES), min_size=12, max_size=40
)
TERMS = sorted(STUB_VOCAB.terms)
OTHER_LEAVES: list[st.SearchStrategy[Leaf]] = [
    st.sampled_from([EnumValue(vocab=STUB_VOCAB.id, term=term) for term in TERMS]),
    st.integers(min_value=0, max_value=2**64 - 1).map(Bucket.log2),
    st.booleans(),
    st.integers(min_value=0, max_value=2**31 - 1),
]


@st.composite
def documents(draw: st.DrawFn) -> tuple[DocV1, list[str]]:
    key = SessionKey(draw(st.binary(min_size=32, max_size=32)))
    texts = draw(st.lists(PLAIN_TEXT, max_size=6))
    leaves: list[Leaf] = [tokenize(key, Untrusted(text)) for text in texts]
    leaves.extend(draw(st.lists(st.one_of(*OTHER_LEAVES), max_size=6)))
    items = tuple(draw(st.permutations(leaves)))
    doc = DocV1(profile=draw(st.sampled_from(Profile)), vocab=(STUB_REF,), items=items)
    return doc, texts


@given(documents())
def test_valid_documents_emit_and_round_trip(sample: tuple[DocV1, list[str]]) -> None:
    doc, texts = sample
    out = emit(doc, vocab=REGISTRY)
    assert DocV1.model_validate_json(out, strict=True) == doc
    assert emit(doc, vocab=REGISTRY) == out
    for text in texts:
        assert text not in out


@given(st.text(alphabet=st.characters(exclude_categories=NO_SURROGATES), min_size=1, max_size=20))
def test_raw_string_leaves_are_refused(value: str) -> None:
    assume(TOKEN_RE.fullmatch(value) is None)
    raw_items: tuple[Any, ...] = (value,)
    doc = DocV1.model_construct(profile=Profile.STRICT, vocab=(STUB_REF,), items=raw_items)
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    assert info.value.code is ErrorCode.E_EGRESS_SCHEMA
    assert info.value.args == (ErrorCode.E_EGRESS_SCHEMA.value,)
