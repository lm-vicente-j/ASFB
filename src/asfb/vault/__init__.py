"""In-memory vault (one per session) and the path checks behind every read the session makes."""

from asfb.vault.paths import check_roots, resolve_in_roots, walk_documents
from asfb.vault.store import MAX_DOCUMENTS, MAX_REVISIONS, MAX_SURFACES, MAX_TOKENS, DocRef, Vault

__all__ = [
    "MAX_DOCUMENTS",
    "MAX_REVISIONS",
    "MAX_SURFACES",
    "MAX_TOKENS",
    "DocRef",
    "Vault",
    "check_roots",
    "resolve_in_roots",
    "walk_documents",
]
