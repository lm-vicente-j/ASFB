"""Main-side Windows Job Object for the isolated worker, through ctypes. Imported only on win32.

The child is created suspended, so it runs only after it is in the job: the job is created right
after Popen, the child is assigned, then resumed. Closing the job kills every process still in it,
which covers the venv launcher and the interpreter it starts. Untested on other operating systems.
"""

import ctypes
from ctypes import wintypes
from typing import Final, final

CREATE_SUSPENDED: Final = 0x00000004

_EXTENDED_LIMIT_INFORMATION: Final = 9
_LIMIT_JOB_TIME: Final = 0x00000004
_LIMIT_ACTIVE_PROCESS: Final = 0x00000008
_LIMIT_PROCESS_MEMORY: Final = 0x00000100
_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: Final = 0x00000400
_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_PROCESS_TERMINATE: Final = 0x0001
_PROCESS_SET_QUOTA: Final = 0x0100
_PROCESS_SUSPEND_RESUME: Final = 0x0800
_HUNDRED_NANOSECONDS: Final = 10_000_000


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# typeshed has no ctypes.WinDLL off Windows; the lookup is the same call, typed as Any there.
_WinDLL = getattr(ctypes, "WinDLL")  # noqa: B009
_KERNEL32 = _WinDLL("kernel32", use_last_error=True)
_NTDLL = _WinDLL("ntdll")
_KERNEL32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_KERNEL32.CreateJobObjectW.restype = ctypes.c_void_p
_KERNEL32.SetInformationJobObject.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_KERNEL32.SetInformationJobObject.restype = wintypes.BOOL
_KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_KERNEL32.OpenProcess.restype = ctypes.c_void_p
_KERNEL32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_KERNEL32.AssignProcessToJobObject.restype = wintypes.BOOL
_KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
_KERNEL32.CloseHandle.restype = wintypes.BOOL
_NTDLL.NtResumeProcess.argtypes = [ctypes.c_void_p]
_NTDLL.NtResumeProcess.restype = ctypes.c_long


@final
class Job:
    """A kill-on-close job handle. close() is idempotent."""

    __slots__ = ("_handle",)

    def __init__(self, handle: int, /) -> None:
        self._handle = handle

    @property
    def handle(self) -> int:
        return self._handle

    def close(self) -> None:
        if self._handle:
            _KERNEL32.CloseHandle(self._handle)
            self._handle = 0


def _create(*, memory_bytes: int, cpu_seconds: int, processes: int) -> Job | None:
    handle = _KERNEL32.CreateJobObjectW(None, None)
    if not handle:
        return None
    job = Job(handle)
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = (
        _LIMIT_JOB_TIME
        | _LIMIT_ACTIVE_PROCESS
        | _LIMIT_PROCESS_MEMORY
        | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        | _LIMIT_KILL_ON_JOB_CLOSE
    )
    info.BasicLimitInformation.PerJobUserTimeLimit = cpu_seconds * _HUNDRED_NANOSECONDS
    info.BasicLimitInformation.ActiveProcessLimit = processes
    info.ProcessMemoryLimit = memory_bytes
    if not _KERNEL32.SetInformationJobObject(
        handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        job.close()
        return None
    return job


def _place(job: Job, pid: int) -> bool:
    process = _KERNEL32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SUSPEND_RESUME, False, pid
    )
    if not process:
        return False
    try:
        if not _KERNEL32.AssignProcessToJobObject(job.handle, process):
            return False
        return bool(_NTDLL.NtResumeProcess(process) >= 0)
    finally:
        _KERNEL32.CloseHandle(process)


def attach(pid: int, *, memory_bytes: int, cpu_seconds: int, processes: int) -> Job | None:
    """Create a job with the limits, put the suspended child in it and resume the child.

    Returns None when any step fails; the caller then kills the child.
    """
    job = _create(memory_bytes=memory_bytes, cpu_seconds=cpu_seconds, processes=processes)
    if job is None:
        return None
    if _place(job, pid):
        return job
    job.close()
    return None
