"""Canary planting, encoded-variant leak detection, the HTML pipeline and golden renumbering."""

import base64
import json
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from asfb import sanitize_html
from asfb.types.key import SessionKey

PLACEHOLDER: Final = "@@CANARY@@"
ENTITY_PLACEHOLDER: Final = "@@CANARY_ENTITIES@@"
CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "carriers.json"
HTML_CORPUS: Final = (
    Path(__file__).resolve().parents[1] / "corpus" / "canary" / "html_carriers.json"
)
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


def _entities(text: str) -> str:
    return "".join(f"&#{ord(char)};" for char in text)


def plant(template: str, canary: str) -> str:
    text = template.replace(ENTITY_PLACEHOLDER, _entities(canary))
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


def renumber(view: str) -> str:
    """Replace each distinct token by T1, T2, ... in order of first appearance."""
    numbers: dict[str, str] = {}

    def _number(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in numbers:
            numbers[token] = f"T{len(numbers) + 1}"
        return numbers[token]

    return _TOKEN.sub(_number, view)
