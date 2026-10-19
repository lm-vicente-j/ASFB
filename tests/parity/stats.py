from __future__ import annotations

from math import comb
from typing import Any


def _get(r: Any, key: str) -> Any:
    # Accepts ArmResult instances or their asdict() form.
    return r[key] if isinstance(r, dict) else getattr(r, key)


def pass_rate(results: list[Any]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if _get(r, "passed")) / len(results)


def mcnemar_exact(pairs: list[tuple[bool, bool]]) -> float:
    """Two-sided exact McNemar test on (baseline_passed, asfb_passed) pairs."""
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if y and not x)
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def token_totals(results: list[Any]) -> dict[str, int]:
    inp = sum(_get(r, "input_tokens") for r in results)
    out = sum(_get(r, "output_tokens") for r in results)
    return {"input": inp, "output": out, "total": inp + out}


def overhead(base_total: int, asfb_total: int) -> float | None:
    if base_total == 0:
        return None
    return (asfb_total - base_total) / base_total


def summarize(runs: list[Any]) -> dict[str, Any]:
    base = [r for r in runs if _get(r, "arm") == "baseline"]
    asfb = [r for r in runs if _get(r, "arm") == "asfb"]
    base_rate = pass_rate(base)
    base_tokens = token_totals(base)
    summary: dict[str, Any] = {
        "n": len(base),
        "base_passed": sum(1 for r in base if _get(r, "passed")),
        "base_rate": base_rate,
        "base_tokens": base_tokens,
        "asfb_passed": None,
        "asfb_rate": None,
        "delta_pp": None,
        "p_value": None,
        "leaks": None,
        "asfb_tokens": None,
        "token_overhead": None,
    }
    if not asfb:
        return summary

    base_by_key = {(_get(r, "task_id"), _get(r, "sample")): bool(_get(r, "passed")) for r in base}
    pairs: list[tuple[bool, bool]] = []
    for r in asfb:
        key = (_get(r, "task_id"), _get(r, "sample"))
        if key in base_by_key:
            pairs.append((base_by_key[key], bool(_get(r, "passed"))))

    asfb_rate = pass_rate(asfb)
    asfb_tokens = token_totals(asfb)
    summary.update(
        asfb_passed=sum(1 for r in asfb if _get(r, "passed")),
        asfb_rate=asfb_rate,
        delta_pp=(asfb_rate - base_rate) * 100,
        p_value=mcnemar_exact(pairs),
        leaks=sum(1 for r in asfb if _get(r, "leaked") is True),
        asfb_tokens=asfb_tokens,
        token_overhead=overhead(base_tokens["total"], asfb_tokens["total"]),
    )
    return summary
