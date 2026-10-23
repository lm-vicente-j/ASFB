"""Command line: sanitize, keygen, serve (MCP over stdio) and agents. Only static codes on stderr.

Every subcommand builds a Session with isolate=True, except `sanitize --no-isolate`, which is for
diagnosis. Errors print the code alone and exit 1; wrong usage prints E_USAGE and exits 2 (the
argparse messages, which can repeat arguments, are replaced). Output goes to stdout. The module
imports nothing from vault, sanitize, ingest, normalize or types.untrusted.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Final, Literal, NoReturn

from asfb.agents import claude
from asfb.errors import AsfbError, ErrorCode
from asfb.session import Listing, Session
from asfb.types.key import SessionKey

_PROFILES: Final = ("strict", "diffable")
_NETWORK: Final = ("isolate", "host")
_EXIT_ERROR: Final = 1
_EXIT_USAGE: Final = 2


class _Usage(Exception):
    """Internal: the arguments do not parse. Carries no argument text."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _Usage


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="asfb")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    sanitize_cmd = commands.add_parser("sanitize")
    sanitize_cmd.add_argument("target")
    _session_options(sanitize_cmd)
    sanitize_cmd.add_argument("--json", action="store_true")
    sanitize_cmd.add_argument("--no-isolate", action="store_true")

    keygen_cmd = commands.add_parser("keygen")
    keygen_cmd.add_argument("path")

    serve_cmd = commands.add_parser("serve")
    _session_options(serve_cmd, roots_required=True)

    agents_cmd = commands.add_parser("agents")
    agent_kinds = agents_cmd.add_subparsers(dest="agent", required=True, parser_class=_Parser)
    claude_cmd = agent_kinds.add_parser("claude")
    _agent_options(claude_cmd)
    mcp_cmd = agent_kinds.add_parser("mcp-config")
    _agent_options(mcp_cmd)
    mcp_cmd.add_argument("--profile", choices=_PROFILES, default="strict")
    mcp_cmd.add_argument("--run-network", choices=_NETWORK, default="isolate")
    return parser


def _session_options(command: argparse.ArgumentParser, *, roots_required: bool = False) -> None:
    command.add_argument("--root", action="append", required=roots_required)
    command.add_argument("--profile", choices=_PROFILES, default="strict")
    command.add_argument("--key-file")
    command.add_argument("--python")
    command.add_argument("--run-network", choices=_NETWORK, default="isolate")
    command.add_argument("--run-timeout", type=int, default=300)


def _agent_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--root", action="append", required=True)
    command.add_argument("--key-file")
    command.add_argument("--python")


def _under(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        return False


def _check_key_file(roots: Sequence[str], key_file: str) -> None:
    """A key file inside a root would be readable by the agent: refused."""
    key = os.path.normcase(os.path.realpath(key_file))
    for root in roots:
        base = os.path.normcase(os.path.realpath(root))
        if key == base or _under(key, base):
            raise AsfbError(ErrorCode.E_KEY_FILE)


def _session(
    roots: Sequence[str],
    *,
    profile: str,
    key_file: str | None,
    python: str | None,
    run_network: Literal["isolate", "host"],
    run_timeout: int,
    isolate: bool = True,
) -> Session:
    key = None
    if key_file is not None:
        _check_key_file(roots, key_file)
        key = SessionKey.from_file(key_file)
    return Session(
        roots,
        profile=profile,
        key=key,
        python=python,
        run_timeout=run_timeout,
        isolate=isolate,
        run_network=run_network,
    )


def _write(text: str) -> None:
    sys.stdout.write(text + "\n")


def _sanitize_target(args: argparse.Namespace) -> tuple[list[str], str]:
    """Without --root, the root is the directory itself or the file's directory."""
    if args.root:
        return list(args.root), args.target
    if os.path.isdir(args.target):
        return [args.target], "."
    directory, name = os.path.split(os.path.abspath(args.target))
    return [directory], name


def _sanitize(args: argparse.Namespace) -> int:
    roots, target = _sanitize_target(args)
    session = _session(
        roots,
        profile=args.profile,
        key_file=args.key_file,
        python=args.python,
        run_network=args.run_network,
        run_timeout=args.run_timeout,
        isolate=not args.no_isolate,
    )
    result = session.sanitize(target)
    _write(result.json if args.json else result.view)
    if isinstance(result, Listing):
        # In JSON mode stdout must stay one document, so the failures go to stderr.
        for failure in result.failed:
            line = f"{failure.doc} {failure.code.value}"
            if args.json:
                sys.stderr.write(line + "\n")
            else:
                _write(line)
    return 0


def _keygen(args: argparse.Namespace) -> int:
    SessionKey.create_file(args.path)
    return 0


def _serve(args: argparse.Namespace) -> int:
    session = _session(
        args.root,
        profile=args.profile,
        key_file=args.key_file,
        python=args.python,
        run_network=args.run_network,
        run_timeout=args.run_timeout,
    )
    from asfb.mcp.server import serve

    return serve(session, sys.stdin.buffer, sys.stdout.buffer)


def _protected(args: argparse.Namespace) -> list[str]:
    paths = list(args.root)
    if args.key_file is not None:
        paths.append(args.key_file)
    return paths


def _agents_claude(args: argparse.Namespace) -> int:
    settings = claude.settings(_protected(args), python=args.python or sys.executable)
    _write(json.dumps(settings, indent=2, ensure_ascii=False))
    return 0


def _agents_mcp_config(args: argparse.Namespace) -> int:
    server_args = ["--profile", args.profile, "--run-network", args.run_network]
    for root in args.root:
        server_args.extend(["--root", root])
    if args.key_file is not None:
        server_args.extend(["--key-file", args.key_file])
    config = claude.mcp_config(server_args, python=args.python or sys.executable)
    _write(json.dumps(config, indent=2, ensure_ascii=False))
    return 0


_HANDLERS = {
    "sanitize": _sanitize,
    "keygen": _keygen,
    "serve": _serve,
}
_AGENT_HANDLERS = {"claude": _agents_claude, "mcp-config": _agents_mcp_config}


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "agents":
        return _AGENT_HANDLERS[args.agent](args)
    return _HANDLERS[args.command](args)


def _fail(code: ErrorCode, status: int) -> int:
    sys.stderr.write(code.value + "\n")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command. Returns the exit status: 0 ok, 1 error (code on stderr), 2 usage."""
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except _Usage:
        return _fail(ErrorCode.E_USAGE, _EXIT_USAGE)
    code = ErrorCode.E_INTERNAL
    try:
        return _dispatch(args)
    except AsfbError as error:
        code = error.code
    except Exception:  # noqa: BLE001
        code = ErrorCode.E_INTERNAL
    return _fail(code, _EXIT_ERROR)
