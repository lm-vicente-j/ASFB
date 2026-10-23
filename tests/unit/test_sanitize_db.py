"""Database censoring: names, declared types, affinities, row buckets and the pairs minted."""

import re

import pytest

from asfb.ingest import db as ingest_db
from asfb.sanitize.db import affinity_of, censor_db_with_pairs, with_wal
from asfb.sanitize.tokens import token_for
from asfb.schema import db as schema
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")


def _model(script: str = SAMPLE_SCHEMA) -> schema.DbV1:
    model, _pairs = censor_db_with_pairs(KEY, ingest_db.read_bytes(image(script)), Profile.STRICT)
    return model


def _table(model: schema.DbV1, token: Token) -> schema.DbTable:
    (found,) = [table for table in model.tables if table.name == token]
    return found


def _token(text: str) -> Token:
    return token_for(KEY, Untrusted(text))


def test_a_user_table_is_a_token_and_its_rows_are_a_bucket() -> None:
    model = _model()
    clientes = _table(model, _token("clientes"))
    assert clientes.rows != "unknown"
    assert clientes.rows.index == 2  # 2 rows: bit_length 2


def test_a_virtual_table_has_an_unknown_count_and_no_columns() -> None:
    virtual = _table(_model(), _token("busqueda"))
    assert virtual.rows == "unknown"
    assert virtual.columns == ()


def test_without_rowid_and_wal_flags() -> None:
    model = _model()
    assert _table(model, _token("sin_fila")).without_rowid is True
    assert _table(model, _token("clientes")).without_rowid is False
    assert model.wal_present is False
    assert with_wal(model, True).wal_present is True
    assert model.wal_present is False


def test_page_size_is_a_bucket_of_the_page_size() -> None:
    model = _model()
    assert model.page_size.index == 13  # 4096 has 13 bits


def test_declared_types_public_base_or_token() -> None:
    columns = {column.name: column for column in _table(_model(), _token("clientes")).columns}
    assert columns[_token("id")].declared == EnumValue(vocab="db.sqltypes", term="INTEGER")
    email = columns[_token("email")].declared
    assert email == EnumValue(vocab="db.sqltypes", term="VARCHAR")  # the parenthesis is dropped
    assert columns[_token("ref")].declared == EnumValue(vocab="db.sqltypes", term="INT")


def test_an_unknown_declared_type_is_tokenized_without_its_parameters() -> None:
    model = _model("CREATE TABLE t(a GEOMETRYX(10, 2));")
    column = _table(model, _token("t")).columns[0]
    assert column.declared == _token("GEOMETRYX")


def test_a_column_without_declared_type_has_none() -> None:
    model = _model("CREATE TABLE t(a);")
    assert _table(model, _token("t")).columns[0].declared is None


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (None, "BLOB"),
        ("", "BLOB"),
        ("BLOB", "BLOB"),
        ("INT", "INTEGER"),
        ("int8", "INTEGER"),
        ("POINT", "INTEGER"),
        ("CHARINT", "INTEGER"),
        ("VARCHAR(255)", "TEXT"),
        ("CLOB", "TEXT"),
        ("DOUBLE PRECISION", "REAL"),
        ("FLOAT", "REAL"),
        ("DECIMAL(10,5)", "NUMERIC"),
        ("BOOLEAN", "NUMERIC"),
        ("GEOMETRYX", "NUMERIC"),
    ],
)
def test_affinity_follows_the_five_sqlite_rules(declared: str | None, expected: str) -> None:
    assert affinity_of(declared) == expected


def test_affinity_is_always_public() -> None:
    column = _table(_model(), _token("clientes")).columns[0]
    assert column.affinity == EnumValue(vocab="db.sqlite", term="INTEGER")


def test_internal_sqlite_names_are_public_and_user_names_are_not() -> None:
    model = _model(
        "CREATE TABLE t(a INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO t VALUES (1);"
        'CREATE TABLE "TEXT"(a);'
    )
    internal = [table for table in model.tables if isinstance(table.name, EnumValue)]
    assert [table.name for table in internal] == [
        EnumValue(vocab="db.sqlite", term="sqlite_sequence")
    ]
    assert all(isinstance(table.name, Token) for table in model.tables if table not in internal)


def test_a_column_named_like_an_internal_word_is_still_a_token() -> None:
    model = _model("CREATE TABLE t(sqlite_sequence INTEGER);")
    assert _table(model, _token("t")).columns[0].name == _token("sqlite_sequence")


def test_foreign_keys_name_the_target_table_and_column() -> None:
    model = _model()
    ref = {column.name: column for column in _table(model, _token("clientes")).columns}[
        _token("ref")
    ]
    (link,) = ref.foreign_keys
    assert link.table == _token("clientes")
    assert link.column == _token("id")


def test_indexes_views_and_triggers_carry_names_only() -> None:
    model = _model()
    clientes = _table(model, _token("clientes"))
    (index,) = [index for index in clientes.indexes if index.name == _token("idx_email")]
    assert index.unique is False
    assert index.columns == (_token("email"),)
    assert model.views == (_token("vista"),)
    (trigger,) = model.triggers
    assert trigger.name == _token("tr")
    assert trigger.table == _token("clientes")


def test_generated_and_defaulted_columns_flag_only_their_presence() -> None:
    model = _model(
        "CREATE TABLE g(a INT DEFAULT 7, b INT GENERATED ALWAYS AS (a * 2) VIRTUAL,"
        " c INT GENERATED ALWAYS AS (a + 1) STORED);"
    )
    columns = {column.name: column for column in _table(model, _token("g")).columns}
    assert columns[_token("a")].default is True
    assert columns[_token("b")].hidden == "generated_virtual"
    assert columns[_token("c")].hidden == "generated_stored"
    assert columns[_token("b")].default is False


def test_the_sql_text_of_views_triggers_and_checks_is_never_minted_or_emitted() -> None:
    secret = "zz_confidential_text"  # noqa: S105 - a fixture value, not a credential
    model = _model(
        f"CREATE TABLE t(a TEXT CHECK (a <> '{secret}') DEFAULT '{secret}');"
        f"CREATE VIEW v AS SELECT '{secret}' AS x;"
        f"CREATE TRIGGER g AFTER INSERT ON t BEGIN SELECT '{secret}'; END;"
    )
    text = model.model_dump_json()
    assert secret not in text
    assert "CHECK" not in text
    assert "SELECT" not in text
    assert "CREATE" not in text


def test_names_and_declared_types_have_pairs_that_recompute_under_the_key() -> None:
    _model_value, pairs = censor_db_with_pairs(
        KEY, ingest_db.read_bytes(image(SAMPLE_SCHEMA)), Profile.STRICT
    )
    items = pairs.items()
    assert items
    for token, surface in items:
        assert token_for(KEY, Untrusted(surface)) == token
    surfaces = {surface for _token_value, surface in items}
    assert {"clientes", "email", "idx_email", "tr", "vista", "sin_fila"} <= surfaces


def test_a_hostile_name_keeps_its_raw_surface() -> None:
    name = 'x"; DROP TABLE t; --'
    script = 'CREATE TABLE "x""; DROP TABLE t; --"(a);'
    model = _model(script)
    _model_value, pairs = censor_db_with_pairs(
        KEY, ingest_db.read_bytes(image(script)), Profile.STRICT
    )
    assert name in {surface for _token_value, surface in pairs.items()}
    assert len(model.tables) == 1


def test_only_tokens_and_vocabulary_words_appear_in_the_model() -> None:
    model = _model()
    text = model.model_dump_json()
    for word in ("clientes", "email", "nombre", "busqueda", "texto", "vista", "tr", "idx_email"):
        assert f'"{word}"' not in text
    assert TOKEN_RE.search(text)
