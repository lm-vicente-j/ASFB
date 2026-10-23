"""Session is exported lazily: importing asfb does not load the session."""

import subprocess
import sys


def test_importing_asfb_does_not_load_session_but_the_export_works() -> None:
    code = (
        "import sys, asfb\n"
        "assert 'asfb.session' not in sys.modules\n"
        "assert asfb.Session.__name__ == 'Session'\n"
        "assert 'Session' in asfb.__all__\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
