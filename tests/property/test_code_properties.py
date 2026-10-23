"""Any text gives a normalized source with ordered spans, or one static code. Nothing else."""

from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import code
from asfb.types.untrusted import unwrap

PIECES = st.sampled_from(
    [
        "def ",
        "f",
        "(",
        ")",
        ":",
        "\n",
        "    ",
        "\t",
        "\r",
        "\r\n",
        "'",
        '"""',
        "#",
        "f'{",
        "}'",
        "0",
        "1",
        "é",
        "​",
        "‮",
        "\x00",
        "\x0c",
        "\\",
        "match ",
        "# coding: latin-1",
        " ",
    ]
)


def _attempt(text: str) -> code.CodeSource | ErrorCode:
    try:
        return code.read_text(text)
    except AsfbError as error:
        return error.code


@settings(max_examples=300, deadline=None)
@given(st.lists(PIECES, max_size=40).map("".join))
def test_lexer_gives_a_source_or_one_static_code(text: str) -> None:
    outcome = _attempt(text)
    if isinstance(outcome, ErrorCode):
        assert outcome in ErrorCode.__members__.values()
        return
    source = outcome
    size = len(unwrap(source.text))
    starts = [token.start for token in source.tokens]
    assert starts == sorted(starts)
    assert all(0 <= token.start <= token.end <= size for token in source.tokens)
    assert source.dropped >= 0
    assert "\r" not in unwrap(source.text)


SECRET_NAMES = st.from_regex(r"zq[a-z]{6,10}", fullmatch=True)
STATEMENTS = st.sampled_from(
    [
        "{n} = {n}",
        "{n} = f'{{{n}}} tail'",
        "{n} = '{n} text'",
        "{n} = {n}({n}.{n})  # {n} note",
        "def {n}({n}):\n    return {n}",
        "class {n}:\n    {n} = 0\n",
        "if {n}:\n    {n} = 1\nelse:\n    {n} = {n}",
        "{n} = b'{n}'",
    ]
)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(STATEMENTS, SECRET_NAMES), min_size=1, max_size=6))
def test_emitted_source_never_carries_an_invented_name(
    statements: list[tuple[str, str]],
) -> None:
    from asfb.egress import emit
    from asfb.sanitize.code import censor_code
    from asfb.schema.doc import Profile
    from asfb.types.key import SessionKey
    from asfb.vocab import registry

    text = "".join(template.format(n=name) + "\n" for template, name in statements)
    try:
        source = code.read_text(text)
    except AsfbError:
        return
    census = censor_code(SessionKey.generate(), source, Profile.STRICT, None)
    emitted = emit(census.model, vocab=registry())
    for _template, name in statements:
        assert name not in emitted
