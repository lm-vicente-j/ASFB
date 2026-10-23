"""The vault: caps, revisions, document ids, pair accumulation, translation and redaction."""

import copy
import pickle
import sys
from pathlib import Path

import pytest

from asfb.egress import CensoredView, Emitted
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.html import SurfacePairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.vault import store
from asfb.vault.store import DocRef, Vault, doc_id

KEY = SessionKey(bytes(range(32)))
OTHER = SessionKey(bytes(range(1, 33)))
EMITTED = Emitted('{"schema_id":"asfb.doc/1"}')
VIEW = CensoredView("")
DIGEST_A = bytes(32)
DIGEST_B = bytes([1]) * 32


def _tok(text: str) -> Token:
    return token_for(KEY, Untrusted(text))


def _doc_pairs(*surfaces: str) -> SurfacePairs:
    return SurfacePairs(tuple((_tok(surface), surface) for surface in surfaces))


def _put(vault: Vault, path: str, digest: bytes = DIGEST_A) -> DocRef:
    return vault.put_document(path, Profile.STRICT, digest, EMITTED, VIEW)


def test_surfaces_accumulate_and_known_tokens_answer() -> None:
    vault = Vault(KEY)
    vault.record(_doc_pairs("Alice", "Alice"))
    vault.record(SurfacePairs(((_tok("Alice"), "alice"),)))
    token = _tok("Alice")
    assert vault.known(token) is True
    assert vault.surfaces(token) == frozenset({"Alice", "alice"})
    assert vault.known(_tok("nobody")) is False
    assert vault.surfaces(_tok("nobody")) == frozenset()


def test_stale_surfaces_stay_known_after_an_edit() -> None:
    vault = Vault(KEY)
    vault.record(_doc_pairs("old value"))
    vault.record(_doc_pairs("new value"))
    assert vault.surfaces(_tok("old value")) == frozenset({"old value"})


def test_token_cap_gives_vault_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_TOKENS", 2)
    vault = Vault(KEY)
    vault.record(_doc_pairs("one", "two"))
    with pytest.raises(AsfbError) as info:
        vault.record(_doc_pairs("three"))
    assert info.value.code is ErrorCode.E_VAULT_FULL
    assert vault.known(_tok("three")) is False


def test_failed_record_applies_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_TOKENS", 2)
    vault = Vault(KEY)
    vault.record(_doc_pairs("one"))
    with pytest.raises(AsfbError):
        vault.record(_doc_pairs("two", "three"))
    assert vault.known(_tok("two")) is False


def test_surface_cap_gives_vault_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_SURFACES", 2)
    vault = Vault(KEY)
    token = _tok("base")
    vault.record(SurfacePairs(((token, "a"), (token, "b"))))
    with pytest.raises(AsfbError) as info:
        vault.record(SurfacePairs(((token, "c"),)))
    assert info.value.code is ErrorCode.E_VAULT_FULL
    assert vault.surfaces(token) == frozenset({"a", "b"})


def test_document_cap_gives_vault_full(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(store, "MAX_DOCUMENTS", 2)
    vault = Vault(KEY)
    _put(vault, str(tmp_path / "a.html"))
    _put(vault, str(tmp_path / "b.html"))
    with pytest.raises(AsfbError) as info:
        _put(vault, str(tmp_path / "c.html"))
    assert info.value.code is ErrorCode.E_VAULT_FULL


def test_same_digest_returns_latest_revision(tmp_path: Path) -> None:
    vault = Vault(KEY)
    path = str(tmp_path / "a.html")
    first = _put(vault, path, DIGEST_A)
    again = _put(vault, path, DIGEST_A)
    assert first == DocRef(first.doc, 1)
    assert again == first
    assert vault.get(first.doc).ref == first


def test_changed_digest_creates_next_revision(tmp_path: Path) -> None:
    vault = Vault(KEY)
    path = str(tmp_path / "a.html")
    first = _put(vault, path, DIGEST_A)
    second = _put(vault, path, DIGEST_B)
    assert second == DocRef(first.doc, 2)
    assert str(second) == f"{first.doc}@2"
    assert vault.get(first.doc).ref == second
    assert vault.get(first.doc, 1).ref == first
    assert vault.get(first.doc, 1).digest == DIGEST_A

    latest = vault.find(first.doc)
    assert latest is not None
    assert latest.digest == DIGEST_B


def test_revision_cap_gives_vault_full(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(store, "MAX_REVISIONS", 2)
    vault = Vault(KEY)
    path = str(tmp_path / "a.html")
    _put(vault, path, DIGEST_A)
    _put(vault, path, DIGEST_B)
    with pytest.raises(AsfbError) as info:
        _put(vault, path, bytes([2]) * 32)
    assert info.value.code is ErrorCode.E_VAULT_FULL


@pytest.mark.parametrize("revision", [0, 2])
def test_unknown_revision_gives_doc_unknown(tmp_path: Path, revision: int) -> None:
    vault = Vault(KEY)
    ref = _put(vault, str(tmp_path / "a.html"))
    with pytest.raises(AsfbError) as info:
        vault.get(ref.doc, revision)
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN


def test_unknown_document_gives_doc_unknown() -> None:
    vault = Vault(KEY)
    with pytest.raises(AsfbError) as info:
        vault.get(doc_id(KEY, "/nowhere/x.html"))
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN
    assert vault.find(doc_id(KEY, "/nowhere/x.html")) is None


def test_doc_ids_are_stable_per_file_and_key(tmp_path: Path) -> None:
    path = str(tmp_path / "a.html")
    assert doc_id(KEY, path) == doc_id(KEY, path)
    assert doc_id(KEY, path) != doc_id(OTHER, path)
    assert doc_id(KEY, path) != doc_id(KEY, str(tmp_path / "b.html"))
    assert Vault(KEY).doc_id(path) == doc_id(KEY, path)


def test_doc_id_shape_and_path_hiding(tmp_path: Path) -> None:
    name = "secret-customer-name"
    ident = doc_id(KEY, str(tmp_path / f"{name}.html"))
    assert len(ident) == 18
    assert ident.startswith("d_")
    assert name not in ident
    assert str(tmp_path) not in ident


@pytest.mark.skipif(sys.platform != "win32", reason="Windows casing")
def test_doc_id_ignores_case_on_windows(tmp_path: Path) -> None:
    assert doc_id(KEY, str(tmp_path / "A.html")) == doc_id(KEY, str(tmp_path / "a.html").upper())


def test_mint_path_round_trips_through_translation() -> None:
    vault = Vault(KEY)
    tokens = vault.mint_path(["reports", "2026 q1"])
    assert len(tokens) == 2
    assert vault.translate_arg(tokens[0]) == "reports"
    assert vault.translate_arg(tokens[1]) == "2026 q1"


def test_translate_arg_refuses_unknown_ambiguous_and_non_tokens() -> None:
    vault = Vault(KEY)
    vault.record(_doc_pairs("x"))
    vault.record(SurfacePairs(((_tok("x"), "y"),)))
    for text in (_tok("never seen"), "plain", "t_0123456789ABCDEF"):
        with pytest.raises(AsfbError) as info:
            vault.translate_arg(text)
        assert info.value.code is ErrorCode.E_PATH_UNKNOWN_TOKEN
    with pytest.raises(AsfbError) as info:
        vault.translate_arg(_tok("never seen"), unknown=ErrorCode.E_DOC_UNKNOWN)
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN
    with pytest.raises(AsfbError) as info:
        vault.translate_arg(_tok("x"))
    assert info.value.code is ErrorCode.E_TOKEN_AMBIGUOUS


def test_document_scope_wins_over_the_session_scope(tmp_path: Path) -> None:
    vault = Vault(KEY)
    first = _put(vault, str(tmp_path / "a.html"))
    vault.record(SurfacePairs(((_tok("x"), "x"),)), doc=first.doc)
    vault.record(SurfacePairs(((_tok("x"), "y"),)))
    assert vault.surfaces(_tok("x")) == frozenset({"x", "y"})
    assert vault.surfaces_for(_tok("x"), doc=first.doc) == frozenset({"x"})
    assert vault.translate_arg(_tok("x"), doc=first.doc) == "x"
    with pytest.raises(AsfbError) as info:
        vault.translate_arg(_tok("x"))
    assert info.value.code is ErrorCode.E_TOKEN_AMBIGUOUS


def test_document_scope_falls_back_to_the_session_for_other_tokens(tmp_path: Path) -> None:
    vault = Vault(KEY)
    ref = _put(vault, str(tmp_path / "a.html"))
    vault.record(SurfacePairs(((_tok("only elsewhere"), "elsewhere"),)))
    assert vault.surfaces_for(_tok("only elsewhere"), doc=ref.doc) == frozenset({"elsewhere"})
    assert vault.surfaces_for(_tok("nobody"), doc=ref.doc) == frozenset()


def test_recording_a_document_replaces_its_own_table(tmp_path: Path) -> None:
    vault = Vault(KEY)
    ref = _put(vault, str(tmp_path / "a.html"))
    token = _tok("x")
    vault.record(SurfacePairs(((token, "x1"), (token, "x2"))), doc=ref.doc)
    assert vault.surfaces_for(token, doc=ref.doc) == frozenset({"x1", "x2"})
    vault.record(SurfacePairs(((token, "x3"),)), doc=ref.doc)
    assert vault.surfaces_for(token, doc=ref.doc) == frozenset({"x3"})
    assert vault.surfaces(token) == frozenset({"x1", "x2", "x3"})


def test_content_surfaces_do_not_reach_path_translation() -> None:
    vault = Vault(KEY)
    vault.record(SurfacePairs(((_tok("reports"), "reports"),)))
    assert vault.path_surfaces(_tok("reports")) == frozenset()
    assert vault.translate_arg(_tok("reports")) == "reports"
    vault.mint_path(["reports"])
    assert vault.path_surfaces(_tok("reports")) == frozenset({"reports"})


def test_path_scope_keeps_only_minted_names() -> None:
    vault = Vault(KEY)
    (token,) = vault.mint_path(["Notes"])
    vault.record(SurfacePairs(((token, "content words"),)))
    assert vault.path_surfaces(token) == frozenset({"Notes"})
    assert vault.surfaces(token) == frozenset({"Notes", "content words"})


def test_translate_text_replaces_every_token_or_fails() -> None:
    vault = Vault(KEY)
    vault.record(_doc_pairs("Bob", "Carol"))
    text = f"hi {_tok('Bob')} and {_tok('Carol')}"
    assert vault.translate_text(text) == "hi Bob and Carol"
    with pytest.raises(AsfbError) as info:
        vault.translate_text(f"{_tok('Bob')} {_tok('Dave')}", unknown=ErrorCode.E_DOC_UNKNOWN)
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN


def test_vault_and_stored_views_are_redacted(tmp_path: Path) -> None:
    vault = Vault(KEY)
    vault.record(_doc_pairs("secret surface"))
    ref = _put(vault, str(tmp_path / "private.html"))
    stored = vault.get(ref.doc)
    for item in (repr(vault), repr(stored)):
        assert item.endswith("(<redacted>)")
        assert "secret" not in item
        assert "private" not in item
    assert repr(vault) == "Vault(<redacted>)"


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_vault_refuses_pickling(protocol: int) -> None:
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(Vault(KEY), protocol=protocol)


def test_vault_refuses_copying() -> None:
    vault = Vault(KEY)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(vault)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(vault)


def test_stored_refuses_pickling_and_copying(tmp_path: Path) -> None:
    vault = Vault(KEY)
    stored = vault.get(_put(vault, str(tmp_path / "a.html")).doc)
    with pytest.raises(TypeError):
        pickle.dumps(stored)
    with pytest.raises(TypeError):
        copy.copy(stored)
    with pytest.raises(TypeError):
        copy.deepcopy(stored)


def test_pairs_are_redacted_and_refuse_copying() -> None:
    pairs = _doc_pairs("hidden")
    assert "hidden" not in repr(pairs)
    with pytest.raises(TypeError):
        copy.copy(pairs)
    with pytest.raises(TypeError):
        pickle.dumps(pairs)
