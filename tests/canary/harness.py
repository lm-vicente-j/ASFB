"""Canary planting, encoded-variant leak detection, the HTML and text pipelines, and renumbering."""

import base64
import json
import re
import secrets
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from asfb import sanitize_html, sanitize_markdown, sanitize_python, sanitize_text
from asfb.api import sanitize as sanitize_file
from asfb.egress import emit, render
from asfb.ingest.db import read_bytes as read_db_bytes
from asfb.sanitize.db import censor_db_with_pairs
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.vocab import registry
from tests.sqlite_images import image as sqlite_image

PLACEHOLDER: Final = "@@CANARY@@"
ENTITY_PLACEHOLDER: Final = "@@CANARY_ENTITIES@@"
CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "carriers.json"
HTML_CORPUS: Final = (
    Path(__file__).resolve().parents[1] / "corpus" / "canary" / "html_carriers.json"
)
TEXT_CORPUS: Final = (
    Path(__file__).resolve().parents[1] / "corpus" / "canary" / "text_carriers.json"
)
MD_CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "md_carriers.json"
CODE_CORPUS: Final = (
    Path(__file__).resolve().parents[1] / "corpus" / "canary" / "code_carriers.json"
)
LOG_CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "log_carriers.json"
DB_CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "db_carriers.json"
PAD_PLACEHOLDER: Final = "@@PAD100K@@"  # replaced by 100 000 ASCII letters, for a very long line
_TOKEN: Final = re.compile(r"t_[0-9a-f]{16}")


@dataclass(frozen=True, slots=True)
class Carrier:
    id: str
    template: str


def new_canary() -> str:
    return "cnry_" + secrets.token_hex(8)


def _load(path: Path) -> list[Carrier]:
    entries: list[dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    return [Carrier(id=entry["id"], template=entry["template"]) for entry in entries]


def load_carriers() -> list[Carrier]:
    return _load(CORPUS)


def load_html_carriers() -> list[Carrier]:
    return _load(HTML_CORPUS)


def load_text_carriers() -> list[Carrier]:
    return _load(TEXT_CORPUS)


def load_md_carriers() -> list[Carrier]:
    return _load(MD_CORPUS)


def load_code_carriers() -> list[Carrier]:
    return _load(CODE_CORPUS)


def load_log_carriers() -> list[Carrier]:
    return _load(LOG_CORPUS)


def load_db_carriers() -> list[Carrier]:
    """Database carriers: each template is an SQL script that creates the planted object."""
    return _load(DB_CORPUS)


def log_format(carrier_id: str) -> Literal["jsonl", "text"]:
    """Carrier ids that start with jsonl- are JSON Lines; every other carrier is plain text."""
    return "jsonl" if carrier_id.startswith("jsonl-") else "text"


def _entities(text: str) -> str:
    return "".join(f"&#{ord(char)};" for char in text)


def plant(template: str, canary: str) -> str:
    text = template.replace(PAD_PLACEHOLDER, "a" * 100_000)
    text = text.replace(ENTITY_PLACEHOLDER, _entities(canary))
    return text.replace(PLACEHOLDER, canary)


def variants(canary: str) -> list[str]:
    data = canary.encode("utf-8")
    hex_lower = data.hex()
    return [
        canary,
        canary.upper(),
        hex_lower,
        hex_lower.upper(),
        base64.b64encode(data).decode("ascii"),
    ]


def find_leaks(blobs: Iterable[str | bytes], canaries: Iterable[str]) -> list[str]:
    needles = sorted({form for canary in canaries for form in variants(canary)})
    leaks: list[str] = []
    for blob in blobs:
        data = blob.encode("utf-8", "surrogatepass") if isinstance(blob, str) else blob
        for needle in needles:
            if needle.encode("ascii") in data and needle not in leaks:
                leaks.append(needle)
    return leaks


def html_pipeline(html: str, key: SessionKey) -> list[str]:
    doc = sanitize_html(html, key=key)
    return [doc.json, str(doc.views.censored())]


def isolated_pipeline(html: str, key: SessionKey) -> list[str]:
    """The html_pipeline outputs, with the parse and sanitize step run in a worker process."""
    doc = sanitize_html(html, key=key, isolate=True)
    return [doc.json, str(doc.views.censored())]


def renumber(view: str) -> str:
    """Replace each distinct token by T1, T2, ... in order of first appearance."""
    numbers: dict[str, str] = {}

    def _number(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in numbers:
            numbers[token] = f"T{len(numbers) + 1}"
        return numbers[token]

    return _TOKEN.sub(_number, view)


def text_pipeline(text: str, key: SessionKey) -> list[str]:
    doc = sanitize_text(text, key=key)
    return [doc.json, str(doc.views.censored())]


def isolated_text_pipeline(text: str, key: SessionKey) -> list[str]:
    """The text_pipeline outputs, with the split and sanitize step run in a worker process."""
    doc = sanitize_text(text, key=key, isolate=True)
    return [doc.json, str(doc.views.censored())]


def md_pipeline(text: str, key: SessionKey) -> list[str]:
    doc = sanitize_markdown(text, key=key)
    return [doc.json, str(doc.views.censored())]


def isolated_md_pipeline(text: str, key: SessionKey) -> list[str]:
    """The md_pipeline outputs, with the scan and sanitize step run in a worker process."""
    doc = sanitize_markdown(text, key=key, isolate=True)
    return [doc.json, str(doc.views.censored())]


def _log_outputs(text: str, key: SessionKey, fmt: str, isolate: bool) -> list[str]:
    """The public path for a log file: the file is written to a temporary directory by suffix."""
    name = "events.jsonl" if fmt == "jsonl" else "app.log"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(text.encode("utf-8"))
        doc = sanitize_file(str(path), key=key, isolate=isolate)
    return [doc.json, str(doc.views.censored())]


def log_pipeline(text: str, key: SessionKey, fmt: str) -> list[str]:
    """A log file censored in process through api.sanitize: the outputs a caller can see."""
    return _log_outputs(text, key, fmt, isolate=False)


def isolated_log_pipeline(text: str, key: SessionKey, fmt: str) -> list[str]:
    """The log_pipeline outputs, with the parse and sanitize step run in a worker process."""
    return _log_outputs(text, key, fmt, isolate=True)


def code_pipeline(text: str, key: SessionKey) -> list[str]:
    doc = sanitize_python(text, key=key)
    return [doc.json, str(doc.views.censored())]


def isolated_code_pipeline(text: str, key: SessionKey) -> list[str]:
    """The code_pipeline outputs, with the lex and sanitize step run in a worker process."""
    doc = sanitize_python(text, key=key, isolate=True)
    return [doc.json, str(doc.views.censored())]


def db_image(template: str, canary: str) -> bytes:
    """The database image that a planted SQL script creates (built in memory, never on disk)."""
    return sqlite_image(plant(template, canary))


def db_pipeline(image_bytes: bytes, key: SessionKey) -> list[str]:
    """The in-process database path: ingest, censor and emit, then render. Outputs only."""
    model, _pairs = censor_db_with_pairs(key, read_db_bytes(image_bytes), Profile.STRICT)
    emitted = emit(model, vocab=registry())
    return [emitted, str(render(emitted))]


def db_file_pipeline(image_bytes: bytes, key: SessionKey) -> list[str]:
    """The public path for a SQLite file: written to a temporary .db file, censored by api.sanitize
    (always in a worker). Only the outputs a caller can see are returned."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "app.db"
        path.write_bytes(image_bytes)
        doc = sanitize_file(str(path), key=key)
    return [doc.json, str(doc.views.censored())]
