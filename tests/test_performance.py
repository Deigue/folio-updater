"""Performance profiling tests for the entire test suite."""

from __future__ import annotations

import ast
import cProfile
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
    test_dir = Path(__file__).parent
    src_modules = _get_src_module_names()
    modules = set()

    for test_file in test_dir.glob("test_*.py"):
        if test_file.name == "test_performance.py":
            continue  # Skip ourselves

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


@pytest.mark.skip(reason="Performance profiling - run manually when needed")
def test_profile_test_suite() -> None:  # pragma: no cover
    """Profile the entire test suite to identify bottlenecks.

    Run with:
      uv run python -m pytest tests/test_performance.py::test_profile_test_suite -v -s
    """
    profiler = cProfile.Profile()
    profiler.enable()

    # Run pytest programmatically
    pytest.main(
        [
            "tests",
            "-v",
            "--ignore=tests/test_performance.py",  # Don't profile ourselves
            "--log-cli-level=ERROR",
        ],
    )

    profiler.disable()

    # Use print_stats with a filter for our code
    logger.info("\n%s", "=" * 100)
    logger.info("TOP 50 PROJECT FUNCTIONS BY CUMULATIVE TIME")
    logger.info("%s", "=" * 100)

    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")

    # Filter to project files only
    stats.print_stats("folio-updater", 50)

    logger.info("\n%s", "=" * 100)
    logger.info("TOP 30 PROJECT FUNCTIONS BY TOTAL TIME (self time)")
    logger.info("%s", "=" * 100)

    stats.sort_stats("tottime")
    stats.print_stats("folio-updater", 30)


def test_profile_imports() -> None:
    """Profile import times to identify slow imports.

    Dynamically discovers all modules imported by test files and profiles them.
    Uses fresh Python subprocess to avoid import caching.

    Run with:
      uv run python -m pytest tests/test_performance.py::test_profile_imports -v -s
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


if __name__ == "__main__":  # pragma: no cover
    # Allow running this directly
    pytest.main([__file__, "-v", "-s"])
