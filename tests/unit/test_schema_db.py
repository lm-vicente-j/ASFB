"""The v1 database model: its validators, its committed JSON Schema, the gate and the view."""

import json
from importlib import resources

import pytest
from pydantic import ValidationError

from asfb.egress import Emitted, db_units, emit, render, render_diff
from asfb.errors import EgressError
from asfb.ingest import db as ingest_db
from asfb.sanitize.db import censor_db_with_pairs
from asfb.schema import DB_RESOURCE
from asfb.schema.db import DbColumn, DbTable, DbV1
from asfb.schema.doc import Profile, VocabRef
from asfb.types.docid import DocId
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.values import Bucket, EnumValue
from asfb.vocab import registry
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))
TOKEN = Token("t_0123456789abcdef")
REFS = (VocabRef(id="db.sqlite", version=1), VocabRef(id="db.sqltypes", version=1))


def _column(**overrides: object) -> dict[str, object]:
    column: dict[str, object] = {
        "name": TOKEN,
        "declared": EnumValue(vocab="db.sqltypes", term="TEXT"),
        "affinity": EnumValue(vocab="db.sqlite", term="TEXT"),
        "notnull": False,
        "pk": 0,
        "default": False,
        "hidden": "normal",
        "foreign_keys": (),
    }
    column.update(overrides)
    return column


def _model(**overrides: object) -> DbV1:
    body: dict[str, object] = {
        "profile": Profile.STRICT,
        "vocab": REFS,
        "wal_present": False,
        "page_size": Bucket.log2(4096),
        "tables": (),
        "views": (),
        "triggers": (),
    }
    body.update(overrides)
    return DbV1.model_validate(body)


def _emitted_model() -> DbV1:
    model, _pairs = censor_db_with_pairs(
        KEY, ingest_db.read_bytes(image(SAMPLE_SCHEMA)), Profile.STRICT
    )
    return model


def _emitted(source: bytes) -> Emitted:
    model, _pairs = censor_db_with_pairs(KEY, ingest_db.read_bytes(source), Profile.STRICT)
    return emit(model, vocab=registry())


def test_an_empty_database_model_is_valid() -> None:
    assert _model().schema_id == "asfb.db/1"


def test_the_two_vocabularies_must_be_declared_exactly() -> None:
    with pytest.raises(ValidationError):
        _model(vocab=(REFS[0],))
    with pytest.raises(ValidationError):
        _model(vocab=(REFS[1], REFS[0]))


def test_an_affinity_must_be_one_of_the_five() -> None:
    with pytest.raises(ValidationError):
        DbColumn.model_validate(_column(affinity=EnumValue(vocab="db.sqlite", term="VARCHAR")))


def test_an_affinity_from_another_vocabulary_is_refused() -> None:
    with pytest.raises(ValidationError):
        DbColumn.model_validate(_column(affinity=EnumValue(vocab="db.sqltypes", term="TEXT")))


def test_a_declared_type_is_a_sqltypes_term_or_a_token() -> None:
    DbColumn.model_validate(_column(declared=TOKEN))
    with pytest.raises(ValidationError):
        DbColumn.model_validate(_column(declared=EnumValue(vocab="db.sqlite", term="TEXT")))


def test_a_row_count_is_a_bucket_or_unknown() -> None:
    base: dict[str, object] = {
        "name": TOKEN,
        "without_rowid": False,
        "columns": (),
        "indexes": (),
    }
    DbTable.model_validate({**base, "rows": "unknown"})
    DbTable.model_validate({**base, "rows": Bucket.log2(5)})
    with pytest.raises(ValidationError):
        DbTable.model_validate({**base, "rows": 12})


def test_the_committed_schema_is_the_generated_schema() -> None:
    text = resources.files("asfb.schema").joinpath(DB_RESOURCE).read_text(encoding="utf-8")
    assert json.loads(text) == json.loads(json.dumps(DbV1.model_json_schema()))


def test_the_emitted_database_passes_the_gate_and_renders() -> None:
    emitted = _emitted(image(SAMPLE_SCHEMA))
    view = render(emitted)
    assert view.startswith("db page_size=b13 wal=no")
    assert "table t_" in view
    assert "clientes" not in view
    assert "email" not in view


def test_the_view_shows_the_structure_not_the_content() -> None:
    emitted = _emitted(image(SAMPLE_SCHEMA))
    view = str(render(emitted))
    assert "without_rowid" in view
    assert "unique" in view
    assert "fk=" in view
    assert "view t_" in view
    assert "trigger t_" in view
    assert "a@x" not in view


def test_a_tampered_emitted_database_is_refused_by_the_gate() -> None:
    emitted = _emitted(image(SAMPLE_SCHEMA))
    tampered = Emitted(emitted.replace('"term":"TEXT"', '"term":"NOT_A_TERM"', 1))
    with pytest.raises(EgressError):
        render(tampered)


def test_a_name_that_is_not_a_token_is_refused_by_the_gate() -> None:
    emitted = _emitted(image(SAMPLE_SCHEMA))
    tampered = Emitted(emitted.replace('"name":"t_', '"name":"plain_', 1))
    with pytest.raises(EgressError):
        render(tampered)


def test_schema_units_are_one_line_per_object() -> None:
    lines = db_units(_emitted(image(SAMPLE_SCHEMA)))
    assert lines[0].startswith("db page_size=b13")
    assert sum(line.startswith("table ") for line in lines) == len(_emitted_model().tables)
    assert sum(line.startswith("  column ") for line in lines) >= 5


def test_a_database_diffed_with_itself_has_no_hunks() -> None:
    from asfb.egress.gate import emit as emit_model
    from asfb.schema.diff import DiffV1, RefV1

    emitted = _emitted(image(SAMPLE_SCHEMA))
    diff = emit_model(
        DiffV1(
            profile=Profile.STRICT,
            vocab=(),
            units="schema",
            a=RefV1(doc=_doc_id(), revision=1),
            b=RefV1(doc=_doc_id(), revision=2),
            hunks=(),
        ),
        vocab=registry(),
    )
    view = render_diff(diff, emitted, emitted)
    assert "@@" not in view


def _doc_id() -> DocId:
    return DocId("d_0123456789abcdef")
