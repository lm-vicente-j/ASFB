"""URL censoring: known schemes give a Url of EnumValue, Tokens and flags; the rest is one Token.

Only the shapes in the censoring table are split. A delimiter that urlsplit would drop (an empty
query or fragment, a dropped "//") and TAB, LF or CR (urlsplit removes them) send the whole value
to one Token, so no character is lost without a token.
"""

from collections.abc import Callable
from typing import Final
from urllib.parse import urlsplit

from asfb.sanitize.tokens import token_for
from asfb.schema.html import Url
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue
from asfb.vocab import Vocabulary

URL_SCHEMES: Final = "url.schemes"
SEGMENTED_SCHEMES: Final = frozenset({"http", "https", "ws", "wss", "ftp", "file"})
MAX_PATH: Final = 256
_LOSSY: Final = ("\t", "\n", "\r")

Recorder = Callable[[Token, str], None]


def _mint(key: SessionKey, text: str, record: Recorder | None) -> Token:
    """Mint a token for one piece and report the (token, piece) pair to the recorder."""
    token = token_for(key, Untrusted(text))
    if record is not None:
        record(token, text)
    return token


def _split(key: SessionKey, text: str, schemes: Vocabulary, record: Recorder | None) -> Url | None:
    """The Url for a known shape, or None when the whole value must be one Token."""
    if any(char in text for char in _LOSSY):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    scheme = parts.scheme
    if scheme and scheme not in schemes:
        return None
    term = EnumValue(vocab=URL_SCHEMES, term=scheme) if scheme else None
    remainder = text[len(scheme) + 1 :] if scheme else text
    if scheme and scheme not in SEGMENTED_SCHEMES:
        path = (_mint(key, remainder, record),) if remainder else ()
        return Url(scheme=term, path=path)
    if remainder.startswith("//") and not parts.netloc:
        return None
    if "?" in text.partition("#")[0] and not parts.query:
        return None
    if "#" in text and not parts.fragment:
        return None
    rooted = parts.path.startswith("/")
    body = parts.path[1:] if rooted else parts.path
    trailing = body.endswith("/")
    body = body[:-1] if trailing else body
    segments = body.split("/") if body else []
    if len(segments) > MAX_PATH:
        return None
    return Url(
        scheme=term,
        authority=_mint(key, parts.netloc, record) if parts.netloc else None,
        rooted=rooted,
        path=tuple(_mint(key, segment, record) for segment in segments),
        trailing_slash=trailing,
        query=_mint(key, parts.query, record) if parts.query else None,
        fragment=_mint(key, parts.fragment, record) if parts.fragment else None,
    )


def censor_url(
    key: SessionKey, u: Untrusted[str], schemes: Vocabulary, /, record: Recorder | None = None
) -> Url | Token:
    """Censor a normalized URL value (the output of normalize.value) into a Url or one Token.

    Every token minted for a piece of a Url is reported to record. The whole-value Token of the
    fallback is not reported here; the caller reports it with its own surface.
    """
    url = _split(key, unwrap(u), schemes, record)
    if url is None:
        return token_for(key, u)
    return url
