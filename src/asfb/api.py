"""Public entry points. Each builds a Doc from a path or a string through the single pipeline.

The inner pipeline returns a Doc or an ErrorCode. The public functions raise AsfbError outside
any except block, so no traceback or context carries input text.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from asfb import ingest
from asfb.egress import CensoredView, Emitted, emit, render
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.html import censor_document
from asfb.schema.doc import DocV1, Profile
from asfb.types.key import SessionKey
from asfb.vocab import html_registry

_PROFILES: Final = {member.value: member for member in Profile}


@dataclass(frozen=True, slots=True, eq=False)
class Views:
    """Rendered forms of a document. Holds censored text only."""

    _censored: CensoredView

    def censored(self) -> CensoredView:
        return self._censored


@dataclass(frozen=True, slots=True, eq=False)
class Doc:
    """A censored document. Holds no original text and no key."""

    json: Emitted
    model: DocV1
    profile: Profile
    views: Views


def _profile(value: object) -> Profile | ErrorCode:
    if isinstance(value, Profile):
        return value
    if type(value) is not str or value not in _PROFILES:
        return ErrorCode.E_PROFILE
    return _PROFILES[value]


def _session(profile: Profile, key: object) -> SessionKey | ErrorCode:
    if key is None:
        if profile is Profile.DIFFABLE:
            return ErrorCode.E_PROFILE_KEY
        return SessionKey.generate()
    if type(key) is not SessionKey:
        return ErrorCode.E_PROFILE_KEY
    return key


def _build(nodes: tuple[ingest.Node, ...], profile: Profile, key: SessionKey) -> Doc:
    model = censor_document(key, nodes, profile)
    emitted = emit(model, vocab=html_registry())
    checked = DocV1.model_validate_json(emitted, strict=True)
    return Doc(json=emitted, model=checked, profile=profile, views=Views(render(emitted)))


def _run(
    read: Callable[[], tuple[ingest.Node, ...]], profile: object, key: object
) -> Doc | ErrorCode:
    try:
        chosen = _profile(profile)
        if isinstance(chosen, ErrorCode):
            return chosen
        session = _session(chosen, key)
        if isinstance(session, ErrorCode):
            return session
        return _build(read(), chosen, session)
    except AsfbError as error:
        return error.code
    except Exception:  # noqa: BLE001
        return ErrorCode.E_INTERNAL


def sanitize(
    source: str | os.PathLike[str],
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
) -> Doc:
    """Censor an .html or .htm file. Raises AsfbError with a static code."""
    outcome = _run(lambda: ingest.read_path(source), profile, key)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def sanitize_html(
    html: str,
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
) -> Doc:
    """Censor an HTML string. Raises AsfbError with a static code."""
    outcome = _run(lambda: ingest.read_text(html), profile, key)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
