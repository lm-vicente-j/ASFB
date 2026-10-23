"""Network prefix for the test tool. On Linux, `unshare` puts the child in an empty network
namespace. Everywhere else there is no prefix, and the caller refuses to run without one.

The probe runs once per process and is cached, so a failing probe is not retried.
"""

import shutil
import subprocess
import sys
from functools import cache
from typing import Final

_PROBE_SECONDS: Final = 5.0
_UNSHARE_ARGS: Final = ("--user", "--map-root-user", "--net", "--")


@cache
def isolate_prefix() -> tuple[str, ...] | None:
    """The argv prefix that runs a command with no network, or None when that is unavailable."""
    found: tuple[str, ...] | None = None
    if sys.platform == "linux":
        found = _probe()
    return found


def _probe() -> tuple[str, ...] | None:
    unshare = shutil.which("unshare")
    true = shutil.which("true")
    if unshare is None or true is None:
        return None
    prefix = (unshare, *_UNSHARE_ARGS)
    try:
        probe = subprocess.run(  # noqa: S603
            [*prefix, true],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return prefix if probe.returncode == 0 else None
