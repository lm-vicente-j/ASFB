"""Path checks: roots, realpath containment (junctions, symlinks, case, dotdot) and walks."""

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from asfb.errors import ErrorCode
from asfb.vault import paths

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only")


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    (root / "a.html").write_text("<p>a</p>", encoding="utf-8")
    (root / "sub" / "b.htm").write_text("<p>b</p>", encoding="utf-8")
    (outside / "secret.html").write_text("<p>s</p>", encoding="utf-8")
    return {"root": root, "outside": outside, "tmp": tmp_path}


def _roots(layout: dict[str, Path]) -> list[str]:
    return [str(layout["root"])]


def test_inside_path_resolves_to_its_realpath(layout: dict[str, Path]) -> None:
    resolved = paths.resolve_in_roots(_roots(layout), str(layout["root"] / "a.html"))
    assert resolved == os.path.realpath(layout["root"] / "a.html")


def test_root_itself_is_inside(layout: dict[str, Path]) -> None:
    resolved = paths.resolve_in_roots(_roots(layout), str(layout["root"]))
    assert resolved == os.path.realpath(layout["root"])


def test_dotdot_escape_is_refused(layout: dict[str, Path]) -> None:
    candidate = str(layout["root"] / ".." / "outside" / "secret.html")
    assert paths.resolve_in_roots(_roots(layout), candidate) == ErrorCode.E_PATH_OUTSIDE_ROOT


def test_absolute_path_outside_is_refused(layout: dict[str, Path]) -> None:
    candidate = str(layout["outside"] / "secret.html")
    assert paths.resolve_in_roots(_roots(layout), candidate) == ErrorCode.E_PATH_OUTSIDE_ROOT


@pytest.mark.parametrize("candidate", ["a.html", "sub/b.htm", "", "."])
def test_relative_candidates_are_refused(layout: dict[str, Path], candidate: str) -> None:
    assert paths.resolve_in_roots(_roots(layout), candidate) == ErrorCode.E_PATH_OUTSIDE_ROOT


def test_non_string_candidate_is_refused(layout: dict[str, Path]) -> None:
    bad: Any = layout["root"] / "a.html"
    assert paths.resolve_in_roots(_roots(layout), bad) == ErrorCode.E_PATH_OUTSIDE_ROOT


def test_prefix_sibling_is_not_inside(layout: dict[str, Path]) -> None:
    sibling = layout["tmp"] / "root-evil"
    sibling.mkdir()
    (sibling / "x.html").write_text("<p>x</p>", encoding="utf-8")
    candidate = str(sibling / "x.html")
    assert paths.resolve_in_roots(_roots(layout), candidate) == ErrorCode.E_PATH_OUTSIDE_ROOT


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths are Windows behaviour")
def test_case_difference_still_inside_on_windows(layout: dict[str, Path]) -> None:
    flipped = str(layout["root"] / "a.html").swapcase()
    resolved = paths.resolve_in_roots(_roots(layout), flipped)
    assert isinstance(resolved, str)
    assert os.path.normcase(resolved) == os.path.normcase(
        os.path.realpath(layout["root"] / "a.html")
    )


@pytest.mark.parametrize(
    "roots",
    [[], [""], ["does/not/exist"]],
    ids=["empty", "blank", "missing"],
)
def test_invalid_roots_are_refused(roots: list[str]) -> None:
    assert paths.check_roots(roots) == ErrorCode.E_ROOT_INVALID
    assert paths.resolve_in_roots(roots, "x.html") == ErrorCode.E_ROOT_INVALID


def test_file_as_root_is_refused(layout: dict[str, Path]) -> None:
    assert paths.check_roots([str(layout["root"] / "a.html")]) == ErrorCode.E_ROOT_INVALID


def test_check_roots_returns_realpaths(layout: dict[str, Path]) -> None:
    assert paths.check_roots(_roots(layout)) == (os.path.realpath(layout["root"]),)


def test_containing_root_picks_the_matching_root(layout: dict[str, Path]) -> None:
    roots = [str(layout["outside"]), str(layout["root"])]
    assert paths.containing_root(roots, os.path.realpath(layout["root"] / "sub")) == (
        os.path.realpath(layout["root"])
    )
    assert paths.containing_root(roots, os.path.realpath(layout["tmp"])) is None


@WINDOWS_ONLY
def test_junction_pointing_outside_is_refused(layout: dict[str, Path]) -> None:
    import _winapi

    create_junction = getattr(_winapi, "CreateJunction")  # noqa: B009
    link = layout["root"] / "link"
    create_junction(str(layout["outside"]), str(link))
    candidate = str(link / "secret.html")
    assert paths.resolve_in_roots(_roots(layout), candidate) == ErrorCode.E_PATH_OUTSIDE_ROOT


def test_symlink_to_outside_file_is_refused(layout: dict[str, Path]) -> None:
    link = layout["root"] / "linked.html"
    try:
        os.symlink(layout["outside"] / "secret.html", link)
    except OSError:
        pytest.skip("symlinks cannot be created on this machine")
    assert paths.resolve_in_roots(_roots(layout), str(link)) == ErrorCode.E_PATH_OUTSIDE_ROOT


def test_walk_lists_only_documents_below_the_directory(layout: dict[str, Path]) -> None:
    (layout["root"] / "notes.pdf").write_text("x", encoding="utf-8")
    found = paths.walk_documents(str(layout["root"]))
    assert isinstance(found, list)
    assert sorted(os.path.basename(item) for item in found) == ["a.html", "b.htm"]


def test_walk_depth_limit_gives_list_depth(layout: dict[str, Path]) -> None:
    deep = layout["root"] / "sub" / "deeper" / "deepest"
    deep.mkdir(parents=True)
    assert paths.walk_documents(str(layout["root"]), max_depth=1) == ErrorCode.E_LIST_DEPTH
    assert paths.walk_documents(str(layout["root"]), max_depth=2) == ErrorCode.E_LIST_DEPTH
    found = paths.walk_documents(str(layout["root"]), max_depth=3)
    assert isinstance(found, list)


def test_walk_count_limit_gives_list_limit(layout: dict[str, Path]) -> None:
    assert paths.walk_documents(str(layout["root"]), max_files=1) == ErrorCode.E_LIST_LIMIT
    found = paths.walk_documents(str(layout["root"]), max_files=2)
    assert isinstance(found, list)
    assert len(found) == 2


def test_walk_does_not_follow_symlinked_directories(layout: dict[str, Path]) -> None:
    try:
        os.symlink(layout["outside"], layout["root"] / "escape", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created on this machine")
    found = paths.walk_documents(str(layout["root"]))
    assert isinstance(found, list)
    assert not [item for item in found if "secret" in item]


@WINDOWS_ONLY
def test_walk_does_not_follow_junctions(layout: dict[str, Path]) -> None:
    import _winapi

    create_junction = getattr(_winapi, "CreateJunction")  # noqa: B009
    create_junction(str(layout["outside"]), str(layout["root"] / "jump"))
    found = paths.walk_documents(str(layout["root"]))
    assert isinstance(found, list)
    assert not [item for item in found if "secret" in item]


def test_walk_of_missing_directory_gives_source_read(layout: dict[str, Path]) -> None:
    missing = str(layout["root"] / "absent")
    assert paths.walk_documents(missing) == ErrorCode.E_SOURCE_READ
