import os

import pytest

collect_ignore_glob = ["parity/tasks/*", "parity/results/*"]

PARITY_ENV = "ASFB_RUN_PARITY"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "parity: calls paid model APIs; runs only with ASFB_RUN_PARITY=1"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(PARITY_ENV) == "1":
        return
    skip = pytest.mark.skip(reason=f"parity tests call paid APIs; set {PARITY_ENV}=1 to run")
    for item in items:
        if item.get_closest_marker("parity"):
            item.add_marker(skip)
