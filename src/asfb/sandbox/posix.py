"""Worker-side POSIX limits: rlimits, and PR_SET_NO_NEW_PRIVS on Linux.

Untested on a real POSIX host: the functions take injected resource and libc objects and are
tested with fakes only. No seccomp and no network namespace are applied. The worker calls
load() and install() before it reads the request, and fails closed if either raises.
"""

import os
import sys
from typing import Final, Protocol, cast

CPU_SECONDS: Final = 30
ADDRESS_BYTES: Final = 1024 * 1024 * 1024
PR_SET_NO_NEW_PRIVS: Final = 38


class ResourceModule(Protocol):
    RLIMIT_CPU: int
    RLIMIT_AS: int
    RLIMIT_FSIZE: int
    RLIMIT_CORE: int
    RLIMIT_NPROC: int

    def setrlimit(self, resource: int, limits: tuple[int, int], /) -> None: ...


class LibcLike(Protocol):
    def prctl(self, option: int, arg2: int, arg3: int, arg4: int, arg5: int, /) -> int: ...


def apply_rlimits(
    resource: ResourceModule, *, cpu_seconds: int = CPU_SECONDS, address_bytes: int = ADDRESS_BYTES
) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (address_bytes, address_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def set_no_new_privs(libc: LibcLike) -> None:
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError("prctl(PR_SET_NO_NEW_PRIVS) failed")


def install(
    resource: ResourceModule,
    libc: LibcLike | None,
    *,
    cpu_seconds: int = CPU_SECONDS,
    address_bytes: int = ADDRESS_BYTES,
) -> None:
    apply_rlimits(resource, cpu_seconds=cpu_seconds, address_bytes=address_bytes)
    if libc is not None:
        set_no_new_privs(libc)


def _posix_objects() -> tuple[ResourceModule, LibcLike | None]:
    import ctypes
    import resource

    libc: LibcLike | None = None
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
    return cast(ResourceModule, resource), libc


def load() -> tuple[ResourceModule, LibcLike | None] | None:
    """The real resource module and, on Linux, the C library. None on Windows."""
    if os.name == "nt":
        return None
    return _posix_objects()
