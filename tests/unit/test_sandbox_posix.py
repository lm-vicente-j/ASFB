"""POSIX limits through injected fakes. Untested on a real POSIX host; these run on any OS."""

import pytest

from asfb.sandbox import posix


class FakeResource:
    RLIMIT_CPU = 0
    RLIMIT_AS = 9
    RLIMIT_FSIZE = 1
    RLIMIT_CORE = 4
    RLIMIT_NPROC = 6

    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[int, int]]] = []

    def setrlimit(self, resource: int, limits: tuple[int, int], /) -> None:
        self.calls.append((resource, limits))


class FakeLibc:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[int, int, int, int, int]] = []

    def prctl(self, option: int, arg2: int, arg3: int, arg4: int, arg5: int, /) -> int:
        self.calls.append((option, arg2, arg3, arg4, arg5))
        return self.result


def test_rlimits_are_the_specified_values() -> None:
    fake = FakeResource()
    posix.apply_rlimits(fake)
    assert fake.calls == [
        (FakeResource.RLIMIT_CPU, (30, 30)),
        (FakeResource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024)),
        (FakeResource.RLIMIT_FSIZE, (0, 0)),
        (FakeResource.RLIMIT_CORE, (0, 0)),
        (FakeResource.RLIMIT_NPROC, (0, 0)),
    ]


def test_rlimits_take_lower_values_for_the_selftest() -> None:
    fake = FakeResource()
    posix.apply_rlimits(fake, cpu_seconds=1, address_bytes=128 * 1024 * 1024)
    assert (FakeResource.RLIMIT_CPU, (1, 1)) in fake.calls
    assert (FakeResource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024)) in fake.calls


def test_no_new_privs_is_set_with_the_prctl_arguments() -> None:
    libc = FakeLibc()
    posix.set_no_new_privs(libc)
    assert libc.calls == [(38, 1, 0, 0, 0)]


def test_failed_prctl_fails_closed() -> None:
    with pytest.raises(OSError, match="prctl"):
        posix.set_no_new_privs(FakeLibc(result=-1))


def test_install_applies_rlimits_then_prctl_on_linux() -> None:
    resource = FakeResource()
    libc = FakeLibc()
    posix.install(resource, libc)
    assert len(resource.calls) == 5
    assert libc.calls == [(38, 1, 0, 0, 0)]


def test_install_without_libc_skips_prctl() -> None:
    resource = FakeResource()
    posix.install(resource, None)
    assert len(resource.calls) == 5


def test_install_propagates_a_failed_prctl() -> None:
    with pytest.raises(OSError, match="prctl"):
        posix.install(FakeResource(), FakeLibc(result=-1))


def test_defaults_match_the_spec() -> None:
    assert posix.CPU_SECONDS == 30
    assert posix.ADDRESS_BYTES == 1024**3
    assert posix.PR_SET_NO_NEW_PRIVS == 38
