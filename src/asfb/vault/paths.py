"""Path checks for everything the session reads: roots, realpath containment and the directory walk.

Containment is checked on the realpath (junctions and symlinks resolved first), compared with
commonpath after normcase. Relative candidates are refused. Inner helpers return codes; the
caller raises AsfbError outside any except block.
"""

import os
from collections.abc import Sequence
from typing import Final

from asfb.errors import ErrorCode

MAX_DEPTH: Final = 32
MAX_FILES: Final = 10_000
DOCUMENT_SUFFIXES: Final = frozenset(
    {
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".markdown",
        ".py",
        ".pyi",
        ".log",
        ".jsonl",
        ".ndjson",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)


def check_roots(roots: Sequence[str]) -> tuple[str, ...] | ErrorCode:
    """The realpath of each root. Every root must exist and be a directory."""
    if not roots:
        return ErrorCode.E_ROOT_INVALID
    real: list[str] = []
    for root in roots:
        if type(root) is not str or not root:
            return ErrorCode.E_ROOT_INVALID
        try:
            resolved = os.path.realpath(root)
            if not os.path.isdir(resolved):
                return ErrorCode.E_ROOT_INVALID
        except (OSError, ValueError):
            return ErrorCode.E_ROOT_INVALID
        real.append(resolved)
    return tuple(real)


def _contains(base: str, target: str) -> bool:
    base_key = os.path.normcase(base)
    try:
        return os.path.commonpath([base_key, os.path.normcase(target)]) == base_key
    except ValueError:
        return False


def containing_root(roots: Sequence[str], resolved: str) -> str | None:
    """The first root (as realpath) that contains the resolved path, or None."""
    checked = check_roots(roots)
    if isinstance(checked, ErrorCode):
        return None
    for root in checked:
        if _contains(root, resolved):
            return root
    return None


def resolve_in_roots(roots: Sequence[str], candidate: str) -> str | ErrorCode:
    """The realpath of an absolute candidate, if it lies inside a root. Otherwise a code."""
    checked = check_roots(roots)
    if isinstance(checked, ErrorCode):
        return checked
    if type(candidate) is not str or not os.path.isabs(candidate):
        return ErrorCode.E_PATH_OUTSIDE_ROOT
    try:
        resolved = os.path.realpath(candidate)
    except (OSError, ValueError):
        return ErrorCode.E_PATH_OUTSIDE_ROOT
    if containing_root(checked, resolved) is None:
        return ErrorCode.E_PATH_OUTSIDE_ROOT
    return resolved


def walk_documents(
    directory: str, *, max_depth: int = MAX_DEPTH, max_files: int = MAX_FILES
) -> list[str] | ErrorCode:
    """Every .html or .htm entry below directory (no symlinked or junctioned directories).

    Depth counts directory levels below the start: a directory deeper than max_depth gives
    E_LIST_DEPTH, more than max_files documents give E_LIST_LIMIT. Iterative, no recursion.
    """
    found: list[str] = []
    pending: list[tuple[str, int]] = [(directory, 0)]
    while pending:
        current, depth = pending.pop()
        try:
            with os.scandir(current) as handle:
                entries = list(handle)
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if entry.is_junction() or entry.is_symlink():
                        continue
                    if depth + 1 > max_depth:
                        return ErrorCode.E_LIST_DEPTH
                    pending.append((entry.path, depth + 1))
                elif os.path.splitext(entry.name)[1].lower() in DOCUMENT_SUFFIXES:
                    found.append(entry.path)
                    if len(found) > max_files:
                        return ErrorCode.E_LIST_LIMIT
        except OSError:
            return ErrorCode.E_SOURCE_READ
    return found
