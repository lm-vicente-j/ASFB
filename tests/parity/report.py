from __future__ import annotations

from pathlib import Path

from tests.parity import harness, models

README = Path(__file__).resolve().parents[2] / "README.md"
START = "<!-- asfb-results:start -->"
END = "<!-- asfb-results:end -->"

HEADER = (
    "| Model | Model id | Runs | Pass rate without ASFB | Pass rate with ASFB | Δ (pp) | "
    "McNemar p | Canary leaks | Tokens without ASFB | Tokens with ASFB | Token overhead | Run date |"
)
SEPARATOR = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def _model_id_cell(mid: str | None) -> str:
    return f"`{mid}`" if mid else "not public"


def _row(spec: models.ModelSpec, results: dict[str, dict]) -> str:
    data = results.get(spec.key)
    if data is None:
        return f"| {spec.display} | {_model_id_cell(spec.model_id())} | — | — | — | — | — | — | — | — | — | not run |"

    s = data["summary"]
    runs = f"{data['n_tasks']} × {data['samples']}"
    without = f"{s['base_rate'] * 100:.1f}% ({s['base_passed']}/{s['n']})"
    tokens_without = f"{s['base_tokens']['total']:,}"
    id_cell = _model_id_cell(data.get("model_id"))

    if s["asfb_rate"] is None:
        return (
            f"| {data['display']} | {id_cell} | {runs} | {without} | pending | pending | pending | pending "
            f"| {tokens_without} | pending | pending | {data['run_date']} |"
        )

    with_asfb = f"{s['asfb_rate'] * 100:.1f}% ({s['asfb_passed']}/{s['n']})"
    overhead = "n/a" if s["token_overhead"] is None else f"{s['token_overhead'] * 100:+.1f}%"
    return (
        f"| {data['display']} | {id_cell} | {runs} | {without} | {with_asfb} "
        f"| {s['delta_pp']:+.1f} | {s['p_value']:.2f} | {s['leaks']} "
        f"| {tokens_without} | {s['asfb_tokens']['total']:,} | {overhead} | {data['run_date']} |"
    )


def render(results: dict[str, dict], eol: str = "\n") -> str:
    rows = [_row(spec, results) for spec in models.MODELS]
    return eol.join([HEADER, SEPARATOR, *rows])


def main() -> None:
    # newline="" keeps the README's existing line endings byte for byte.
    with README.open(encoding="utf-8", newline="") as f:
        text = f.read()
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"{README}: missing {START} / {END} markers")

    eol = "\r\n" if "\r\n" in text else "\n"
    body = render(harness.load_results(), eol)
    new_text = text[: start + len(START)] + eol + body + eol + text[end:]
    README.write_text(new_text, encoding="utf-8", newline="")
    print(f"updated {README}")


if __name__ == "__main__":
    main()
