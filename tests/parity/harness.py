from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tests.parity import adapter, models, stats
from tests.parity.adapter import Session
from tests.parity.models import Complete, ModelSpec

HERE = Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
RESULTS_DIR = HERE / "results"

SYSTEM_PROMPT = (
    "You are a careful software engineer. You receive source files and a task. "
    "Some identifiers may be opaque tokens such as t_4a7d91c2; treat them as ordinary names "
    "and keep them exactly as they are. Reply with the complete updated content of the file "
    "named in the task, in a single ```python code block, and nothing else."
)

_PY_BLOCK = re.compile(r"```python[^\n]*\n(.*?)```", re.DOTALL)
_ANY_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


@dataclass
class Task:
    id: str
    kind: str
    target: str
    instruction: str
    canaries: list[str]
    dir: Path

    @property
    def checks_path(self) -> Path:
        return self.dir / "checks.py"

    def sources(self) -> dict[str, str]:
        files = sorted((self.dir / "src").glob("*.py"))
        return {p.name: p.read_text(encoding="utf-8") for p in files}

    def solution(self) -> str:
        return (self.dir / "solution" / self.target).read_text(encoding="utf-8")


def load_tasks() -> list[Task]:
    if not TASKS_DIR.is_dir():
        return []
    tasks: list[Task] = []
    for d in TASKS_DIR.iterdir():
        meta_path = d / "task.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tasks.append(
            Task(
                id=meta["id"],
                kind=meta["kind"],
                target=meta["target"],
                instruction=meta["instruction"],
                canaries=list(meta["canaries"]),
                dir=d,
            )
        )
    return sorted(tasks, key=lambda t: t.id)


def build_prompt(instruction: str, files: dict[str, str], target: str) -> str:
    parts = [instruction, f"File to return: {target}"]
    for name, text in files.items():
        parts.append(f"### {name}\n```python\n{text}\n```")
    return "\n\n".join(parts)


def extract_code(text: str) -> str | None:
    m = _PY_BLOCK.search(text) or _ANY_BLOCK.search(text)
    return m.group(1) if m else None


def run_checks(task: Task, target_code: str, timeout: int = 60) -> bool:
    # Executes model-generated code locally, in a throwaway directory outside the repo.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in task.sources().items():
            (root / name).write_text(text, encoding="utf-8")
        (root / task.target).write_text(target_code, encoding="utf-8")
        shutil.copyfile(task.checks_path, root / "checks.py")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "checks.py"],
                cwd=tmp,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False
    return proc.returncode == 0


@dataclass
class ArmResult:
    task_id: str
    arm: str  # "baseline" | "asfb"
    sample: int
    passed: bool
    input_tokens: int
    output_tokens: int
    leaked: bool | None
    error: str | None


def run_arm(
    task: Task,
    complete: Complete,
    arm: str,
    session: Session | None = None,
    sample: int = 0,
) -> ArmResult:
    if arm == "asfb":
        if session is None:
            raise ValueError("asfb arm needs a session")
        instruction = session.censor(task.instruction)
        files = {name: session.censor(text) for name, text in task.sources().items()}
    else:
        instruction, files = task.instruction, task.sources()
    prompt = build_prompt(instruction, files, task.target)
    leaked = None if arm == "baseline" else any(c in SYSTEM_PROMPT + prompt for c in task.canaries)

    completion = complete(SYSTEM_PROMPT, prompt)

    def result(passed: bool, error: str | None = None) -> ArmResult:
        return ArmResult(
            task_id=task.id,
            arm=arm,
            sample=sample,
            passed=passed,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            leaked=leaked,
            error=error,
        )

    if completion.stop_reason == "refusal":
        return result(False, "refusal")
    code = extract_code(completion.text)
    if code is None:
        return result(False, "no code block")
    if arm == "asfb":
        try:
            code = session.restore(code)  # type: ignore[union-attr]
        except Exception as exc:
            return result(False, f"restore: {exc}")
    return result(run_checks(task, code))


def run_model(
    spec: ModelSpec,
    samples: int,
    complete: Complete | None = None,
    session_factory: Callable[[], Session] = adapter.open_session,
) -> dict:
    if complete is None:
        complete = models.get_client(spec)

    asfb_unavailable: str | None = None
    try:
        session_factory()  # probe only
    except adapter.AsfbUnavailable as exc:
        asfb_unavailable = str(exc)

    tasks = load_tasks()
    runs: list[ArmResult] = []
    for task in tasks:
        for sample in range(samples):
            runs.append(run_arm(task, complete, "baseline", sample=sample))
            if asfb_unavailable is None:
                session = session_factory()
                runs.append(run_arm(task, complete, "asfb", session=session, sample=sample))

    return {
        "model_key": spec.key,
        "display": spec.display,
        "model_id": spec.model_id(),
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "samples": samples,
        "effort": models.EFFORT,
        "n_tasks": len(tasks),
        "asfb_unavailable": asfb_unavailable,
        "runs": [asdict(r) for r in runs],
        "summary": stats.summarize(runs),
    }


def save_results(data: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{data['model_key']}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_results() -> dict[str, dict]:
    if not RESULTS_DIR.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(RESULTS_DIR.glob("*.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out
