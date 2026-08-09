"""Root pytest configuration.

Only decides how many xdist workers to use. Fixtures live in tests/conftest.py;
this file sits at the rootdir so the choice applies to every entry point (VSCode
Test Explorer, bare pytest) without any of them passing flags.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

# Workers to use when the whole suite is run. Deliberately not "auto": every
# worker is a fresh process that re-imports pandas and rebuilds the session
# mock-data cache, so the startup tax grows with the worker count. Measured on a
# 20-core machine: serial ~7.9s, -n 4 ~4.3s, -n 8 ~5.2s, -n auto (20) ~6.8s.
FULL_SUITE_WORKERS = 4

# How many explicitly named targets it takes before a run counts as "the whole
# suite" rather than a selection. (~VSCode's Test Explorer lists)
#
# A threshold near the suite size keeps the win and avoids the cases where
# a large-but-fast selection (92 quick tests: 2.26s serial vs 2.94s parallel)
# would lose. Keep this comfortably below the suite total so it still fires if a
# few tests are removed, and well above any selection you would click by hand.
FULL_RUN_MIN_TARGETS = 120


def pytest_configure(config: pytest.Config) -> None:
    """Parallelise full-suite runs only, leaving targeted runs serial.

    Workers are a bad trade for the run-one-test loop: starting four processes
    costs far more than the tests save.

    Set FOLIO_PYTEST_WORKERS_DEBUG=1 to print how pytest was invoked and which
    way this decision went.

    The three options set here are what xdist's own -n handling sets; it does
    that in pytest_cmdline_main, which has already run by the time a conftest
    gets a say, so setting numprocesses alone would be silently ignored.
    """
    # Inside a worker process xdist has already set everything up.
    if hasattr(config, "workerinput"):
        return
    # An explicit -n (including -n 0) always wins.
    # `-n 0` is falsy but still a deliberate choice.
    if config.getoption("numprocesses", None) is not None:
        return
    # Workers break interactive debugging, and xdist rejects the combination.
    if config.getoption("usepdb", default=False):
        return
    # pytest-cov has already chosen a serial controller by this point; switching
    # to distributed behind its back crashes it ("'Central' object has no
    # attribute 'configure_node'"). Coverage runs pass -n explicitly instead --
    # see the "coverage" task in .vscode/tasks.json.
    if config.getoption("cov_source", None):
        return
    # config.args holds the resolved targets.
    targets = [arg for arg in config.args if "::" in arg or arg.endswith(".py")]
    parallel = not targets or len(targets) >= FULL_RUN_MIN_TARGETS
    if os.environ.get("FOLIO_PYTEST_WORKERS_DEBUG"):
        # Set this to see how a given entry point
        # actually invokes pytest, and which way the decision went.
        sys.stderr.write(
            f"[workers] targets={len(targets)} "
            f"workers={FULL_SUITE_WORKERS if parallel else 0} "
            f"first={targets[0] if targets else '<none>'}\n",
        )
    if not parallel:
        return

    config.option.numprocesses = FULL_SUITE_WORKERS
    config.option.dist = "load"
    config.option.tx = ["popen"] * FULL_SUITE_WORKERS
