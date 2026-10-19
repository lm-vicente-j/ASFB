"""URL censoring: known schemes give a Url of EnumValue, Tokens and flags; the rest is one Token.

Only the shapes in the censoring table are split. A delimiter that urlsplit would drop (an empty
query or fragment, a dropped "//") and TAB, LF or CR (urlsplit removes them) send the whole value
to one Token, so no character is lost without a token.
"""

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


def _split(key: SessionKey, text: str, schemes: Vocabulary) -> Url | None:
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
        path = (token_for(key, Untrusted(remainder)),) if remainder else ()
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
        authority=token_for(key, Untrusted(parts.netloc)) if parts.netloc else None,
        rooted=rooted,
        path=tuple(token_for(key, Untrusted(segment)) for segment in segments),
        trailing_slash=trailing,
        query=token_for(key, Untrusted(parts.query)) if parts.query else None,
        fragment=token_for(key, Untrusted(parts.fragment)) if parts.fragment else None,
    )


def censor_url(key: SessionKey, u: Untrusted[str], schemes: Vocabulary, /) -> Url | Token:
    """Censor a normalized URL value (the output of normalize.value) into a Url or one Token."""
    url = _split(key, unwrap(u), schemes)
    if url is None:
        return token_for(key, u)
    return url
