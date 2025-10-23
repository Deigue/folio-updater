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
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

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

    if not src_dir.exists():
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


def profile_test_suite() -> None:
    """Profile the entire test suite to identify bottlenecks."""
    logger.info("%s", "=" * 100)
    logger.info("PROFILING TEST SUITE")
    logger.info("%s\n", "=" * 100)
    original_level = logger.level

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

    # Flush all pytest outputs before profiling results
    profiler.disable()
    sys.stdout.flush()
    sys.stderr.flush()
    with suppress(ValueError):
        for handler in logging.root.handlers:
            handler.flush()

    _restore_logger_after_pytest(original_level)
    # Print only application code: top 30 by cumulative time
    logger.info("%s", "=" * 80)
    logger.info("APPLICATION CODE - TOP 30 BY CUMULATIVE TIME")
    logger.info("%s", "=" * 80)
    cumulative_output = _get_filtered_stats_output(
        profiler,
        _is_app_func,
        "cumulative",
        30,
    )
    logger.info("\n%s", cumulative_output)

    # Print top 30 by total time
    logger.info("%s", "=" * 80)
    logger.info("APPLICATION CODE - TOP 30 BY TOTAL TIME")
    logger.info("%s", "=" * 80)
    tottime_output = _get_filtered_stats_output(
        profiler,
        _is_app_func,
        "tottime",
        30,
    )
    logger.info("\n%s", tottime_output)


def _is_app_func(func: tuple[Any, ...]) -> bool:
    """Return True for functions from our app or tests.

    Accept functions whose filename resolves under the repository `src/`
    or `tests/` directories.
    """
    try:
        filename = func[0] or ""
    except (TypeError, IndexError):
        return False

    if not filename:
        return False

    # Normalize path for matching
    filename_norm = filename.replace("\\", "/").lower()

    try:
        file_path = Path(filename).resolve()
        repo_root = Path(__file__).parent.parent.resolve()
        src_dir = (repo_root / "src").resolve()
        tests_dir = (repo_root / "tests").resolve()

        # Check whether the file path is under src/ or tests/
        if src_dir in file_path.parents or tests_dir in file_path.parents:
            return True
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("Path resolution failed for %r: %s", filename, exc)

    # Fallback indicators (package names or subpackages)
    app_indicators = (
        "/src/",
        "/tests/",
        "folio_updater",
        "preparers",
        "formatters",
        "transformers",
        "/app/",
        "/services/",
        "/db/",
        "/exporters/",
    )

    return any(ind in filename_norm for ind in app_indicators)


def _get_filtered_stats_output(
    prof: cProfile.Profile,
    predicate: Callable[[tuple[Any, ...]], bool],
    sort_key: str,
    limit: int = 30,
) -> str:
    """Return filtered stats output as a string.

    Args:
        prof: The cProfile.Profile object
        predicate: Function to filter stats entries
        sort_key: Key to sort stats by (e.g., 'cumulative', 'tottime')
        limit: Maximum number of entries to include

    Returns:
        String containing the formatted stats output
    """
    base = pstats.Stats(prof)
    filtered = {k: v for k, v in getattr(base, "stats", {}).items() if predicate(k)}
    buf = io.StringIO()
    new_stats = pstats.Stats(prof, stream=buf)
    cast("Any", new_stats).stats = filtered
    new_stats.sort_stats(sort_key)
    new_stats.print_stats(limit)

    output = buf.getvalue()
    buf.close()
    return output


def _restore_logger_after_pytest(original_level: int) -> None:
    """Restore logger state after pytest has potentially modified it.

    Args:
        original_level: Original logging level to restore
    """
    logger.handlers.clear()
    logging.basicConfig(
        level=original_level,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )
    logger.setLevel(original_level)


def profile_imports() -> None:
    """Profile import times to identify slow imports.

    Dynamically discovers all modules imported by test files and profiles them.
    Uses fresh Python subprocess to avoid import caching.

    """
    logger.info("%s", "=" * 80)
    logger.info("MODULE IMPORT TIMES (Fresh imports, no cache)")
    logger.info("%s\n", "=" * 80)

    # Dynamically discover imports from all test files
    import_tests = sorted(_discover_test_imports())

    if not import_tests:
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

    logger.info("%s", "=" * 80)
    logger.info("Total modules: %d", len(results))
    logger.info("Total import time: %.3fms", total_time)
    logger.info("Slow imports (>%dms): %d", SLOW_IMPORT_THRESHOLD_MS, slow_count)
    logger.info("%s", "=" * 80)


def run_quick_test_suite() -> float:
    """Run test suite and return execution time in seconds."""
    logger.info("%s", "=" * 100)
    logger.info("QUICK TEST SUITE RUN")
    logger.info("%s\n", "=" * 100)
    original_level = logger.level

    start_time = time.perf_counter()
    exit_code = pytest.main(
        [
            "tests",
            "-v",
            "--log-cli-level=ERROR",
            "-q",  # Quiet mode
        ],
    )

    elapsed = time.perf_counter() - start_time

    _restore_logger_after_pytest(original_level)
    logger.info("%s", "=" * 100)
    logger.info("Test Suite Completed in %.2f seconds", elapsed)
    logger.info("Exit Code: %d", exit_code)
    logger.info("%s", "=" * 100)

    return elapsed


if __name__ == "__main__":
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
        print(
            "Running quick test suite. "
            "Use --profile or --imports for detailed analysis.",
        )
        run_quick_test_suite()
