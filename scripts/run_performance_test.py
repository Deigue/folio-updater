"""Performance profiling script for folio-updater.

This script can be run manually to profile imports and test suite execution.
Run with: uv run python scripts/run_performance_test.py
"""

from __future__ import annotations

import ast
import cProfile
import io
import logging
import pstats
import sys
import time
from pathlib import Path

import pytest

SLOW_IMPORT_THRESHOLD_MS = 200
MAX_FUNCTION_DISPLAY_LENGTH = 58

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_src_module_names() -> set[str]:
    """Discover all top-level module names under src directory.

    Returns:
        Set of module names (e.g., {'app', 'db', 'cli', ...}).
    """
    src_dir = Path(__file__).parent.parent / "src"
    modules = set()

    if not src_dir.exists():  # pragma: no cover
        return modules

    for item in src_dir.iterdir():
        if (
            item.is_dir()
            and not item.name.startswith("_")
            and item.name != "__pycache__"
        ):
            modules.add(item.name)

    return modules


def _discover_test_imports() -> set[str]:
    """Discover all module imports from test files dynamically.

    Only includes imports from src/ modules (not tests.utils or external packages).

    Returns:
        Set of unique module names imported across all test files.
    """
    test_dir = Path(__file__).parent.parent / "tests"
    src_modules = _get_src_module_names()
    modules = set()

    for test_file in test_dir.glob("test_*.py"):
        try:
            content = test_file.read_text(encoding="utf-8")
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    # Skip relative imports (e.g., from .utils import ...)
                    if node.level > 0:
                        continue

                    # Check if this is a src module import
                    root_module = node.module.split(".")[0]
                    if root_module in src_modules:
                        modules.add(node.module)
        except (SyntaxError, UnicodeDecodeError):
            # Skip files that can't be parsed
            pass

    return modules


def profile_test_suite() -> None:  # pragma: no cover
    """Profile the entire test suite to identify bottlenecks."""
    logger.info("\n%s", "=" * 100)
    logger.info("PROFILING TEST SUITE")
    logger.info("%s\n", "=" * 100)

    profiler = cProfile.Profile()
    profiler.enable()

    # Run pytest programmatically
    pytest.main(
        [
            "tests",
            "-v",
            "--log-cli-level=ERROR",
        ],
    )

    profiler.disable()

    # * Cumulative time: for finding where total time accumulates.
    # * Total time: isolates time spent in each function itself without subcalls.
    logger.info("\n%s", "=" * 100)
    logger.info("TOP 80 FUNCTIONS BY CUMULATIVE TIME (unfiltered)")
    logger.info("%s", "=" * 100)

    # TODO(deigue): #33 Filter out test framework functions (pytest, unittest, etc.)
    buf = io.StringIO()
    stats_cum = pstats.Stats(profiler, stream=buf)
    stats_cum.sort_stats("cumulative")
    stats_cum.print_stats(80)
    contents = buf.getvalue()
    # Flush to logger immediately under header
    logger.info("\n%s", contents)

    # Clear buffer for the next section
    buf.truncate(0)
    buf.seek(0)

    # Print total (self) time stats and log immediately
    logger.info("\n%s", "=" * 100)
    logger.info("TOP 80 FUNCTIONS BY TOTAL TIME (self time, unfiltered)")
    logger.info("%s", "=" * 100)

    stats_tot = pstats.Stats(profiler, stream=buf)
    stats_tot.sort_stats("tottime")
    stats_tot.print_stats(80)
    contents = buf.getvalue()
    logger.info("\n%s", contents)


def profile_imports() -> None:
    """Profile import times to identify slow imports.

    Dynamically discovers all modules imported by test files and profiles them.
    Uses fresh Python subprocess to avoid import caching.

    """
    logger.info("\n%s", "=" * 80)
    logger.info("MODULE IMPORT TIMES (Fresh imports, no cache)")
    logger.info("%s\n", "=" * 80)

    # Dynamically discover imports from all test files
    import_tests = sorted(_discover_test_imports())

    if not import_tests:  # pragma: no cover
        logger.info("No modules found to profile!")
        return

    results: list[tuple[str, float]] = []

    for module_name in import_tests:
        # Remove from sys.modules to force fresh import
        modules_to_remove = [k for k in sys.modules if k.startswith(module_name)]
        for mod in modules_to_remove:
            del sys.modules[mod]

        start = time.perf_counter()
        try:
            __import__(module_name)
            duration = (time.perf_counter() - start) * 1000
            results.append((module_name, duration))
        except ImportError as e:
            logger.info("%-40s FAILED: %s", module_name, e)

    # Sort by duration (slowest first) and print
    results.sort(key=lambda x: x[1], reverse=True)

    for module_name, duration in results:
        status = "⚠️ SLOW" if duration > SLOW_IMPORT_THRESHOLD_MS else "✅"
        logger.warning("%-40s %9.3fms  %s", module_name, duration, status)

    # Print summary
    total_time = sum(d for _, d in results)
    slow_count = sum(1 for _, d in results if d > SLOW_IMPORT_THRESHOLD_MS)

    logger.info("\n%s", "=" * 80)
    logger.info("Total modules: %d", len(results))
    logger.info("Total import time: %.3fms", total_time)
    logger.info("Slow imports (>%dms): %d", SLOW_IMPORT_THRESHOLD_MS, slow_count)
    logger.info("%s", "=" * 80)


def run_quick_test_suite() -> float:
    """Run test suite and return execution time in seconds."""
    logger.info("\n%s", "=" * 100)
    logger.info("QUICK TEST SUITE RUN")
    logger.info("%s\n", "=" * 100)

    start_time = time.perf_counter()

    # Run pytest
    exit_code = pytest.main(
        [
            "tests",
            "-v",
            "--ignore=tests/test_performance.py",
            "--log-cli-level=ERROR",
            "-q",  # Quiet mode
        ],
    )

    elapsed = time.perf_counter() - start_time

    logger.info("\n%s", "=" * 100)
    logger.info("Test Suite Completed in %.2f seconds", elapsed)
    logger.info("Exit Code: %d", exit_code)
    logger.info("%s", "=" * 100)

    return elapsed


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick test without profiling",
    )
    parser.add_argument(
        "--imports",
        action="store_true",
        help="Profile import times only",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Run full profiling of test suite",
    )

    args = parser.parse_args()

    if args.quick:
        run_quick_test_suite()
    elif args.imports:
        profile_imports()
    elif args.profile:
        profile_test_suite()
    else:
        # Default: run quick test suite
        print(
            "Running quick test suite. "
            "Use --profile or --imports for detailed analysis.",
        )
        run_quick_test_suite()
