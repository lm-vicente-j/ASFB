import pytest

from asfb.normalize import split, value
from asfb.sanitize.tokens import token_for, tokenize
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted, unwrap

KEY = SessionKey(bytes(range(32)))
NFC_E = "\u00e9"
NFD_E = "e\u0301"
FORMAT_CONTROLS = [
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u2060",  # word joiner
    "\ufeff",  # BOM / zero width no-break space
    "\u00ad",  # soft hyphen
    "\u202e",  # right-to-left override
    "\u2066",  # left-to-right isolate
    "\u200e",  # left-to-right mark
]


def norm(text: str) -> str:
    return unwrap(value(Untrusted(text)))


@pytest.mark.parametrize("control", FORMAT_CONTROLS)
def test_format_controls_are_dropped(control: str) -> None:
    assert norm(f"send{control}-invoice") == "send-invoice"


@pytest.mark.parametrize("control", ["\x00", "\x07", "\x1b", "\x7f", "\x85"])
def test_other_control_characters_are_dropped(control: str) -> None:
    assert norm(f"a{control}b") == "ab"


def test_tab_lf_and_cr_are_kept_or_folded() -> None:
    assert norm("a\tb\nc") == "a\tb\nc"
    assert norm("a\rb") == "a\nb"


def test_crlf_and_cr_become_lf() -> None:
    assert norm("a\r\nb\rc\r\nd") == "a\nb\nc\nd"


def test_nfc_and_nfd_spellings_are_equal() -> None:
    assert norm(NFD_E) == norm(NFC_E) == NFC_E


def test_outer_whitespace_is_stripped_but_inner_kept() -> None:
    assert norm(" \t\n x  y \r\n ") == "x  y"


def test_strip_runs_after_controls_are_dropped() -> None:
    assert norm("\u200b x \u200b") == "x"


def test_non_ascii_space_is_not_stripped() -> None:
    assert norm("\u00a0x") == "\u00a0x"


def test_empty_and_control_only_input_become_empty() -> None:
    assert norm("") == ""
    assert norm("\u200b\x00 \t") == ""


@pytest.mark.parametrize(
    "text",
    ["", "plain", f"a{NFD_E}b", "x\r\ny", " \u200b pad \x07 ", "\u00a0\u202e z\u200d "],
)
def test_value_is_idempotent(text: str) -> None:
    once = value(Untrusted(text))
    assert unwrap(value(once)) == unwrap(once)


def _parts(text: str) -> tuple[str, ...]:
    return tuple(unwrap(part) for part in split(Untrusted(text)))


def test_split_on_ascii_whitespace() -> None:
    assert _parts("btn  btn-primary\tx\n") == ("btn", "btn-primary", "x")


@pytest.mark.parametrize(("text", "expected"), [("", ()), ("   ", ()), ("  a ", ("a",))])
def test_split_drops_empty_parts(text: str, expected: tuple[str, ...]) -> None:
    assert _parts(text) == expected


def test_split_does_not_split_on_non_ascii_space() -> None:
    assert _parts("a\u00a0b") == ("a\u00a0b",)


def _token(text: str) -> str:
    return token_for(KEY, Untrusted(text))


def test_token_for_is_tokenize_of_value() -> None:
    raw = "send-invoice"
    assert token_for(KEY, Untrusted(raw)) == tokenize(KEY, value(Untrusted(raw)))


def test_nfc_and_nfd_share_a_token() -> None:
    assert _token(NFD_E) == _token(NFC_E)


@pytest.mark.parametrize("control", FORMAT_CONTROLS)
def test_zero_width_variants_share_a_token(control: str) -> None:
    assert _token(f"send{control}-invoice") == _token("send-invoice")


def test_different_values_give_different_tokens() -> None:
    assert _token("send-invoice") != _token("invoice")
