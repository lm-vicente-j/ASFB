"""Settings for Claude Code: deny rules and the PreToolUse hook that protect ASFB's own paths.

Pure functions that return dictionaries; the CLI prints them as JSON. Nothing is written to disk.
Rule paths use Claude Code's absolute form: a double slash, then the path without its root
(`/home/u/x` becomes `//home/u/x`, `C:\\x\\y` becomes `//c/x/y`).
"""

import ntpath
import os
import shlex
import subprocess
from collections.abc import Sequence
from typing import Final

HOOK_MODULE: Final = "asfb.agents.hook"


def _rule_from(real: str, *, windows: bool) -> str:
    """The rule form of an absolute, resolved path. Pure: takes the platform as an argument."""
    if not windows:
        return "//" + real.lstrip("/")
    drive, rest = ntpath.splitdrive(real)
    if drive.endswith(":"):
        name = drive[:-1].lower()
    else:
        name = drive.replace("\\", "/").strip("/")
    return "//" + name + rest.replace("\\", "/")


def rule_path(path: str, /) -> str:
    """The Claude Code rule form of a path, after resolving symlinks."""
    return _rule_from(os.path.realpath(path), windows=os.name == "nt")


def _deny_rules(protected: Sequence[str]) -> list[str]:
    rules: list[str] = []
    for path in protected:
        rule = rule_path(path)
        # A file rule names the file; anything else (a root, or a path that is not a file) gets
        # the whole subtree, which is the conservative choice.
        target = rule if os.path.isfile(path) else rule + "/**"
        rules.append(f"Read({target})")
        rules.append(f"Edit({target})")
    return rules


def hook_command(protected: Sequence[str], *, python: str) -> str:
    """The shell command for the hook, with every protected path as an argument."""
    argv = [python, "-m", HOOK_MODULE]
    for path in protected:
        argv.extend(["--protected", os.path.realpath(path)])
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def settings(protected: Sequence[str], *, python: str) -> dict[str, object]:
    """The settings object to merge into .claude/settings.json: deny rules and the hook."""
    return {
        "permissions": {"deny": _deny_rules(protected)},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": hook_command(protected, python=python)}
                    ],
                }
            ]
        },
    }


def mcp_config(server_args: Sequence[str], *, python: str) -> dict[str, object]:
    """The .mcp.json object that starts `asfb serve` with the given arguments."""
    return {
        "mcpServers": {
            "asfb": {
                "command": python,
                "args": ["-m", "asfb.cli", "serve", *server_args],
            }
        }
    }
