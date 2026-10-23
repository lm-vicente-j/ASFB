"""One vocabulary registry for every emitted schema, and the closed set of emittable models."""

import json
import typing
from importlib import resources

import pytest

from asfb.egress import gate
from asfb.egress.gate import Emittable, emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema import (
    CODE_RESOURCE,
    DB_RESOURCE,
    DIFF_RESOURCE,
    LIST_RESOURCE,
    LOG_RESOURCE,
    MD_RESOURCE,
    RUN_RESOURCE,
    SCHEMA_RESOURCE,
    TEXT_RESOURCE,
)
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.diff import DiffV1
from asfb.schema.doc import DocV1, Profile, SourceKind, VocabRef
from asfb.schema.list import ListV1
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.run import RunV1
from asfb.schema.text import TextV1
from asfb.vocab import ALL_IDS, DB_IDS, HTML_IDS, LOG_IDS, MD_IDS, PY_IDS, RUN_IDS, registry

RESOURCES: dict[type, str] = {
    DocV1: SCHEMA_RESOURCE,
    ListV1: LIST_RESOURCE,
    DiffV1: DIFF_RESOURCE,
    RunV1: RUN_RESOURCE,
    TextV1: TEXT_RESOURCE,
    MdV1: MD_RESOURCE,
    CodeV1: CODE_RESOURCE,
    LogV1: LOG_RESOURCE,
    DbV1: DB_RESOURCE,
}


def _committed(resource: str) -> object:
    return json.loads(resources.files("asfb.schema").joinpath(resource).read_text(encoding="utf-8"))


def test_registry_holds_every_vocabulary_id() -> None:
    assert set(registry()) == set(ALL_IDS)
    assert set(ALL_IDS) == (
        set(HTML_IDS) | set(RUN_IDS) | set(MD_IDS) | set(PY_IDS) | set(LOG_IDS) | set(DB_IDS)
    )


def test_registry_entries_carry_their_own_ids() -> None:
    for vocab_id, entry in registry().items():
        assert entry.id == vocab_id
        assert entry.version >= 1


def test_registry_is_cached() -> None:
    assert registry() is registry()


def test_emittable_union_is_exactly_the_registered_models() -> None:
    assert set(typing.get_args(Emittable)) == set(RESOURCES)
    assert set(gate._SPECS) == set(RESOURCES)  # noqa: SLF001


@pytest.mark.parametrize("model", list(RESOURCES), ids=lambda model: model.__name__)
def test_each_model_is_checked_against_its_committed_schema(model: type) -> None:
    spec = gate._SPECS[model]  # noqa: SLF001
    assert spec.model is model
    assert spec.schema == _committed(RESOURCES[model])


def test_document_and_listing_emit_through_the_one_registry() -> None:
    doc = DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())
    listing = ListV1(profile=Profile.STRICT, vocab=(), entries=())
    assert json.loads(emit(doc, vocab=registry()))["schema_id"] == "asfb.doc/1"
    assert json.loads(emit(listing, vocab=registry()))["schema_id"] == "asfb.list/1"


def test_emit_refuses_a_declared_vocabulary_missing_from_the_registry() -> None:
    doc = DocV1(
        profile=Profile.STRICT,
        source=SourceKind.HTML,
        vocab=(VocabRef(id="html.elements", version=1),),
        body=(),
    )
    with pytest.raises(EgressError) as info:
        emit(doc, vocab={})
    assert info.value.code is ErrorCode.E_EGRESS_VOCAB
