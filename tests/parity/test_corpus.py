from __future__ import annotations

import hashlib
import json

import pytest

from tests.parity import harness
from tests.parity.harness import ArmResult, Task, build_prompt, extract_code, load_tasks, run_arm, run_checks
from tests.parity.models import MODELS, Completion
from tests.parity.stats import mcnemar_exact, overhead

TASKS = load_tasks()
REQUIRED_FIELDS = ("id", "kind", "target", "instruction", "canaries")


def _token(canary: str) -> str:
    return "t_" + hashlib.sha256(canary.encode()).hexdigest()[:8]


class FakeSession:
    """Deterministic stand-in for asfb: canary -> t_<sha256[:8]>."""

    def __init__(self, canaries: list[str]) -> None:
        self.forward = {c: _token(c) for c in canaries}
        self.reverse = {t: c for c, t in self.forward.items()}

    def censor(self, text: str) -> str:
        # Longest first, so an overlapping canary cannot leave a fragment behind.
        for c in sorted(self.forward, key=len, reverse=True):
            text = text.replace(c, self.forward[c])
        return text

    def restore(self, text: str) -> str:
        for t, c in self.reverse.items():
            text = text.replace(t, c)
        return text


class IdentitySession:
    def censor(self, text: str) -> str:
        return text

    def restore(self, text: str) -> str:
        return text


def _first_task() -> Task:
    if not TASKS:
        pytest.skip("task corpus is empty")
    return TASKS[0]


def test_corpus_not_empty() -> None:
    assert TASKS, "no tasks under tests/parity/tasks"


def test_task_ids_unique() -> None:
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids)), f"duplicate task ids: {sorted(ids)}"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task_metadata(task: Task) -> None:
    meta = json.loads((task.dir / "task.json").read_text(encoding="utf-8"))
    for field in REQUIRED_FIELDS:
        assert field in meta, f"task.json missing {field!r}"
    assert task.target in task.sources(), f"target {task.target} not in src/"
    assert (task.dir / "solution" / task.target).is_file(), f"no solution/{task.target}"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_original_target_fails(task: Task) -> None:
    original = task.sources()[task.target]
    assert run_checks(task, original) is False


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_solution_passes(task: Task) -> None:
    assert run_checks(task, task.solution()) is True


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_canaries_present_in_sources(task: Task) -> None:
    sources = task.sources()
    for c in task.canaries:
        assert any(c in text for text in sources.values()), f"canary {c!r} not in any src file"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_no_canary_in_checks(task: Task) -> None:
    checks = task.checks_path.read_text(encoding="utf-8")
    for c in task.canaries:
        assert c not in checks, f"canary {c!r} leaked into checks.py"


def test_extract_code_python_block() -> None:
    assert extract_code("Here:\n```python\nx = 1\n```\n") == "x = 1\n"


def test_extract_code_bare_block() -> None:
    assert extract_code("```\ny = 2\n```") == "y = 2\n"


def test_extract_code_none() -> None:
    assert extract_code("no code here") is None


def test_mcnemar_exact_one_sided_discordance() -> None:
    pairs = [(False, True)] * 5
    assert mcnemar_exact(pairs) == pytest.approx(0.0625)


def test_mcnemar_exact_no_discordant() -> None:
    assert mcnemar_exact([(True, True), (False, False)]) == 1.0


def test_mcnemar_exact_symmetric() -> None:
    a = [(True, False)] * 2 + [(False, True)] * 5
    b = [(True, False)] * 5 + [(False, True)] * 2
    assert mcnemar_exact(a) == pytest.approx(mcnemar_exact(b))


def test_overhead() -> None:
    assert overhead(100, 120) == pytest.approx(0.2)
    assert overhead(0, 5) is None


def test_asfb_arm_with_fake_session_passes_without_leak() -> None:
    task = _first_task()
    session = FakeSession(task.canaries)

    def complete(system: str, user: str) -> Completion:
        text = f"```python\n{session.censor(task.solution())}\n```"
        return Completion(text=text, input_tokens=100, output_tokens=50)

    result = run_arm(task, complete, "asfb", session)
    assert result.passed is True, result.error
    assert result.leaked is False
    assert result.input_tokens == 100 and result.output_tokens == 50


def test_asfb_arm_with_identity_session_leaks() -> None:
    task = _first_task()

    def complete(system: str, user: str) -> Completion:
        return Completion(text=f"```python\n{task.solution()}\n```", input_tokens=100, output_tokens=50)

    result = run_arm(task, complete, "asfb", IdentitySession())
    assert result.leaked is True


def test_run_model_with_fakes_on_corpus() -> None:
    tasks = load_tasks()
    if not tasks:
        pytest.skip("task corpus is empty")
    all_canaries = sorted({c for t in tasks for c in t.canaries})

    # Map each exact prompt to its answer: the plain solution for baseline,
    # the censored solution for asfb. Both use the same deterministic tokens.
    session = FakeSession(all_canaries)
    answers: dict[str, str] = {}
    for t in tasks:
        files = t.sources()
        answers[build_prompt(t.instruction, files, t.target)] = t.solution()
        censored_files = {name: session.censor(text) for name, text in files.items()}
        censored_prompt = build_prompt(session.censor(t.instruction), censored_files, t.target)
        answers[censored_prompt] = session.censor(t.solution())

    def complete(system: str, user: str) -> Completion:
        return Completion(text=f"```python\n{answers[user]}\n```", input_tokens=100, output_tokens=50)

    data = harness.run_model(
        MODELS[0],
        1,
        complete=complete,
        session_factory=lambda: FakeSession(all_canaries),
    )
    s = data["summary"]
    assert data["asfb_unavailable"] is None
    assert s["base_rate"] == 1.0
    assert s["asfb_rate"] == 1.0
    assert s["leaks"] == 0
    assert s["token_overhead"] == 0.0
