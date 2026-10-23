"""Generate the py.* vocabularies from one pinned CPython 3.12 patch release.

Run it with that interpreter only; it refuses any other version:

    <python 3.12.N> tools/gen_py_vocab.py          write src/asfb/vocab/data/py.*.json
    <python 3.12.N> tools/gen_py_vocab.py --check  compare with the committed files, write nothing

Each file records the exact CPython version, the vocabulary version and the sha256 of its terms
(canonical JSON, ASCII, no spaces). The terms are sorted, public (no leading underscore) and
matched by TERM_RE. Names that exist only in other releases, or were removed, are never listed:
the pinned interpreter defines the list and EXCLUDED names any leftovers of earlier releases.
Change a vocabulary's entry in VOCAB_VERSIONS, and only then, when its terms change: a published
version is never edited in place.
"""

import argparse
import builtins
import hashlib
import importlib
import json
import keyword
import pathlib
import re
import sys
from collections.abc import Iterable

# The patch release that defines the lists. None until someone installs it and pins it:
# uv python install 3.12.N, then set N here. The generator refuses to run while this is None.
PINNED_PATCH: int | None = 15
OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "asfb" / "vocab" / "data"
# Per vocabulary. py.keywords and py.builtins keep version 1 (their terms are unchanged by the move
# from CPython 3.14 to 3.12); py.stdlib and py.exceptions lost terms, so they become version 2.
VOCAB_VERSIONS: dict[str, int] = {
    "py.keywords": 1,
    "py.builtins": 1,
    "py.stdlib": 2,
    "py.exceptions": 2,
}
TERM_RE = re.compile(r"[A-Za-z0-9_.:/+-]{1,64}")
# Modules that CPython 3.12 still ships but a later release removes, or that 3.12 already removed.
REMOVED = frozenset(
    {
        "aifc", "asynchat", "asyncore", "audioop", "cgi", "cgitb", "chunk", "crypt",
        "distutils", "imghdr", "imp", "lib2to3", "mailcap", "msilib", "nis", "nntplib",
        "ossaudiodev", "pipes", "smtpd", "sndhdr", "spwd", "sunau", "telnetlib", "uu",
        "xdrlib",
    }
)  # fmt: skip
# Names that only a later release adds. A 3.12 interpreter never lists them; the filter is kept
# so that a wrong pin cannot put them in.
ADDED_LATER = frozenset({"PythonFinalizationError", "annotationlib", "compression"})
CURATED = (
    "os",
    "os.path",
    "sys",
    "re",
    "json",
    "pathlib",
    "typing",
    "collections",
    "itertools",
    "functools",
    "datetime",
    "logging",
    "subprocess",
    "math",
    "random",
    "time",
    "shutil",
    "tempfile",
    "argparse",
    "dataclasses",
    "enum",
    "abc",
    "asyncio",
    "unittest",
    "csv",
    "io",
    "string",
    "textwrap",
    "hashlib",
    "base64",
    "uuid",
    "urllib.parse",
    "threading",
    "queue",
    "copy",
    "pickle",
    "glob",
    "traceback",
    "contextlib",
    "inspect",
    "pprint",
    "decimal",
    "sqlite3",
    "zipfile",
    "warnings",
)


def public(names: Iterable[str]) -> set[str]:
    return {
        name
        for name in names
        if TERM_RE.fullmatch(name) and not name.startswith("_") and name not in ADDED_LATER
    }


def digest(terms: list[str]) -> str:
    """The sha256 of the canonical JSON of the terms: the same bytes on every platform."""
    canonical = json.dumps(terms, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def build(version: str) -> dict[str, dict[str, object]]:
    """The four vocabularies as documents, for the interpreter that is running."""
    keywords = public([*keyword.kwlist, *keyword.softkwlist])
    names = public(dir(builtins))
    exceptions = {
        name
        for name in names
        if isinstance(getattr(builtins, name, None), type)
        and issubclass(getattr(builtins, name), BaseException)
    }  # pytest and other test frameworks define exceptions too; only builtins count
    stdlib = public(sys.stdlib_module_names)
    for module_name in CURATED:
        stdlib.update(public(dir(importlib.import_module(module_name))))
    stdlib -= REMOVED
    documents: dict[str, dict[str, object]] = {}
    for vocab_id, terms in (
        ("py.keywords", keywords),
        ("py.builtins", names),
        ("py.stdlib", stdlib),
        ("py.exceptions", exceptions),
    ):
        ordered = sorted(terms)
        documents[vocab_id] = {
            "id": vocab_id,
            "version": VOCAB_VERSIONS[vocab_id],
            "source": {"tool": "CPython", "version": version},
            "sha256": digest(ordered),
            "terms": ordered,
        }
    return documents


def render(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    args = parser.parse_args()
    if PINNED_PATCH is None:
        sys.stdout.write("gen_py_vocab: no CPython 3.12 patch is pinned (PINNED_PATCH is None)\n")
        return 2
    running = sys.version_info[:3]
    if running != (3, 12, PINNED_PATCH):
        sys.stdout.write(f"gen_py_vocab: needs CPython 3.12.{PINNED_PATCH}, running {running}\n")
        return 2
    version = f"3.12.{PINNED_PATCH}"
    stale = []
    for vocab_id, document in build(version).items():
        path = OUT / f"{vocab_id}.json"
        text = render(document)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(vocab_id)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        sys.stdout.write(
            "gen_py_vocab: differs from the committed files: " + ", ".join(stale) + "\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
