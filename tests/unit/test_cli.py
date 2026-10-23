"""The command line through main(): outputs, exit codes, key files and static error codes."""

import json
from pathlib import Path

import pytest

from asfb.cli import main


def _site(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "page.html").write_text("<p>Hola Ana</p>", encoding="utf-8")
    return root


def test_sanitize_file_prints_the_view(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = _site(tmp_path / "site") / "page.html"
    assert main(["sanitize", str(page)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("<p>t_")
    assert out.endswith("</p>\n")


def test_sanitize_json_is_one_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    page = _site(tmp_path / "site") / "page.html"
    assert main(["sanitize", str(page), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_id"] == "asfb.doc/1"


def test_sanitize_directory_lists_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site(tmp_path / "site")
    assert main(["sanitize", str(site)]) == 0
    assert capsys.readouterr().out.startswith("d_")


def test_keygen_creates_once_and_refuses_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "k.txt"
    assert main(["keygen", str(key)]) == 0
    assert capsys.readouterr().out == ""
    assert main(["keygen", str(key)]) == 1
    assert capsys.readouterr().err == "E_KEY_FILE\n"


def test_key_file_inside_a_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site(tmp_path / "site")
    key = site / "k.txt"
    assert main(["keygen", str(key)]) == 0
    assert main(["sanitize", str(site), "--key-file", str(key)]) == 1
    assert capsys.readouterr().err == "E_KEY_FILE\n"


def test_diffable_profile_needs_a_key_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site(tmp_path / "site")
    keys = tmp_path / "keys"
    keys.mkdir()
    key = keys / "k.txt"
    assert main(["keygen", str(key)]) == 0
    assert main(["sanitize", str(site), "--profile", "diffable", "--key-file", str(key)]) == 0
    capsys.readouterr()
    assert main(["sanitize", str(site), "--profile", "diffable"]) == 1
    assert capsys.readouterr().err == "E_PROFILE_KEY\n"


def test_usage_errors_print_only_e_usage_and_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert capsys.readouterr().err == "E_USAGE\n"
    assert main(["sanitize", "x", "--profile", "other"]) == 2
    assert capsys.readouterr().err == "E_USAGE\n"


def test_errors_print_only_the_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sanitize", str(tmp_path / "missing.html")]) == 1
    captured = capsys.readouterr()
    assert captured.err == "E_SOURCE_READ\n"
    assert captured.out == ""


def test_agents_claude_prints_settings_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site(tmp_path / "site")
    assert main(["agents", "claude", "--root", str(site)]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert settings["permissions"]["deny"]
    assert sorted(p.name for p in site.iterdir()) == ["page.html"]


def test_agents_mcp_config_names_the_serve_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site(tmp_path / "site")
    assert main(["agents", "mcp-config", "--root", str(site)]) == 0
    config = json.loads(capsys.readouterr().out)
    assert config["mcpServers"]["asfb"]["args"][:3] == ["-m", "asfb.cli", "serve"]
