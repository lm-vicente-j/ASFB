"""Claude Code settings, the rule-path form, the hook's decisions and the hook's exit codes."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from asfb.agents import claude, hook


def _settings(root: Path, key: Path) -> dict[str, object]:
    return claude.settings([str(root), str(key)], python=sys.executable)


def test_settings_deny_read_and_edit_for_roots_and_key_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    key = root.parent / "key.txt"
    key.write_text("k", encoding="utf-8")
    deny = _settings(root, key)["permissions"]
    assert isinstance(deny, dict)
    rules = deny["deny"]
    assert isinstance(rules, list)
    assert f"Read({claude.rule_path(str(root))}/**)" in rules
    assert f"Edit({claude.rule_path(str(root))}/**)" in rules
    assert f"Read({claude.rule_path(str(key))})" in rules
    assert f"Edit({claude.rule_path(str(key))})" in rules


def test_settings_carry_one_pretooluse_hook_for_all_tools(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    hooks = _settings(root, root / "k")["hooks"]
    assert isinstance(hooks, dict)
    entry = hooks["PreToolUse"][0]
    assert entry["matcher"] == "*"
    command = entry["hooks"][0]["command"]
    assert "asfb.agents.hook" in command
    assert "--protected" in command


@pytest.mark.parametrize(
    ("real", "expected"),
    [("C:\\a\\b", "//c/a/b"), ("D:\\", "//d/"), ("\\\\srv\\share\\x", "//srv/share/x")],
)
def test_windows_rule_form(real: str, expected: str) -> None:
    assert claude._rule_from(real, windows=True) == expected  # noqa: SLF001


def test_posix_rule_form_drops_only_the_root_slash() -> None:
    assert claude._rule_from("/home/u/x", windows=False) == "//home/u/x"  # noqa: SLF001


def _event(tool: str, tool_input: dict[str, object], cwd: str) -> dict[str, object]:
    return {"tool_name": tool, "tool_input": tool_input, "cwd": cwd}


@pytest.fixture
def protected(tmp_path: Path) -> list[str]:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    return [str(root)]


def test_file_tools_are_denied_inside_and_allowed_outside(
    tmp_path: Path, protected: list[str]
) -> None:
    root = Path(protected[0])
    assert hook.decide(_event("Read", {"file_path": str(root / "x")}, str(tmp_path)), protected)
    assert hook.decide(_event("Read", {"file_path": str(root)}, str(tmp_path)), protected)
    assert not hook.decide(
        _event("Read", {"file_path": str(tmp_path / "y")}, str(tmp_path)), protected
    )


def test_relative_path_is_resolved_against_cwd(tmp_path: Path, protected: list[str]) -> None:
    root = Path(protected[0])
    assert hook.decide(_event("Read", {"file_path": "x"}, str(root / "sub")), protected)
    assert hook.decide(_event("Edit", {"file_path": "../x"}, str(root / "sub")), protected)


def test_search_without_path_uses_cwd_and_refuses_an_ancestor(
    tmp_path: Path, protected: list[str]
) -> None:
    assert hook.decide(_event("Grep", {"pattern": "x"}, str(tmp_path)), protected)
    assert not hook.decide(
        _event("Glob", {"pattern": "*.py", "path": str(tmp_path / "z")}, str(tmp_path)), protected
    )


def test_search_pattern_naming_the_root_is_denied(tmp_path: Path, protected: list[str]) -> None:
    assert hook.decide(_event("Grep", {"pattern": protected[0]}, str(tmp_path)), protected)


def test_shell_with_cwd_inside_or_naming_the_root_is_denied(
    tmp_path: Path, protected: list[str]
) -> None:
    root = Path(protected[0])
    assert hook.decide(_event("Bash", {"command": "ls"}, str(root / "sub")), protected)
    assert hook.decide(_event("Bash", {"command": f"cat {root}/x"}, str(tmp_path)), protected)
    assert not hook.decide(_event("Bash", {"command": "ls -la"}, str(tmp_path / "w")), protected)


def test_invalid_input_is_denied(tmp_path: Path, protected: list[str]) -> None:
    assert hook.decide("not an object", protected)
    assert hook.decide(_event("Read", {"file_path": 5}, str(tmp_path)), protected)
    assert hook.decide(_event("Read", {}, str(tmp_path)), protected)
    assert hook.decide(_event("Bash", {"command": None}, str(tmp_path)), protected)
    assert hook.decide(_event("Read", {"file_path": "/x"}, "relative"), protected)
    assert hook.decide(_event("Read", {"file_path": "/x"}, str(tmp_path)), [])


def test_other_tools_are_allowed(tmp_path: Path, protected: list[str]) -> None:
    assert not hook.decide(_event("mcp__asfb__sanitize", {"target": "."}, str(tmp_path)), protected)
    assert not hook.decide(_event("WebFetch", {"url": "https://x.test"}, str(tmp_path)), protected)


def test_main_exit_codes_and_static_stderr(
    tmp_path: Path,
    protected: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps(_event("Read", {"file_path": protected[0] + "/x"}, str(tmp_path)))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload.encode())))
    assert hook.main(["--protected", protected[0]]) == 2
    assert capsys.readouterr().err == "asfb: blocked\n"
    allowed = json.dumps(_event("Read", {"file_path": "/etc/hostname"}, str(tmp_path)))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(allowed.encode())))
    assert hook.main(["--protected", protected[0]]) == 0


@pytest.mark.parametrize("payload", [b"{not json", b'{"tool_name":"Read","tool_name":"Bash"}', b""])
def test_hook_blocks_unreadable_input_as_a_subprocess(payload: bytes, tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-m", "asfb.agents.hook", "--protected", str(tmp_path)],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr == b"asfb: blocked\n"
