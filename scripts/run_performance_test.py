"""Performance analysis for the folio-updater test suite.

Gives a bird's-eye view of where test time actually goes, so optimisation
effort lands on real bottlenecks instead of plausible-looking ones.

Run with:
    uv run python scripts/run_performance_test.py            # full report
    uv run python scripts/run_performance_test.py --quick    # wall time only
    uv run python scripts/run_performance_test.py --imports  # import cost
    uv run python scripts/run_performance_test.py --profile  # hot functions
    uv run python scripts/run_performance_test.py --workers  # xdist scaling

Methodology notes (keep mind while reading output):

* Per-test timings are collected from a SERIAL run (``-n 0``). Under xdist the
  wall clock overlaps, so per-test attribution is only meaningful serially.
* Hot functions are ranked by ``tottime``, not ``cumtime``. ``cumtime`` folds
  the body of a ``with`` block into the ``@contextmanager`` generator that wraps
  it, which makes thin helpers like ``db.get_connection`` look responsible for
  seconds of work they never did.
* Always compare a candidate change against the NOISE FLOOR reported below.
  Run-to-run spread on this suite is a few hundred milliseconds; a "win" smaller
  than that is not a win.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.box import SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

REPO_ROOT = Path(__file__).parent.parent
SLOW_IMPORT_THRESHOLD_MS = 200
NOISE_RUNS = 3
TOP_SLOW_TESTS = 15
TOP_HOT_FUNCTIONS = 20

# Rich falls back to 80 columns when output is piped, which squeezes the numeric
# columns into ellipses. Give the tables room whether or not this is a terminal.
console = Console(width=max(shutil.get_terminal_size((120, 24)).columns, 110))

# Matches pytest --durations output, e.g. "0.07s call  tests/test_add.py::Test::test_x"
_DURATION_RE = re.compile(r"^([\d.]+)s\s+(call|setup|teardown)\s+(\S+)")
_SUMMARY_RE = re.compile(r"(\d+) passed")
_WALL_RE = re.compile(r"in ([\d.]+)s")


@dataclass
class FileStats:
    """Aggregated timings for a single test file."""

    name: str
    total: float = 0.0
    call: float = 0.0
    setup: float = 0.0
    teardown: float = 0.0
    tests: int = 0

    @property
    def per_test(self) -> float:
        """Average seconds per test in this file."""
        return self.total / self.tests if self.tests else 0.0


@dataclass
class SuiteRun:
    """Result of one instrumented pytest run."""

    wall: float
    passed: int
    files: dict[str, FileStats] = field(default_factory=dict)
    slowest: list[tuple[float, str, str]] = field(default_factory=list)

    @property
    def accounted(self) -> float:
        """Total time attributed to individual test phases."""
        return sum(f.total for f in self.files.values())


def _pytest_cmd(*extra: str) -> list[str]:
    """Build a pytest command with reporting flags that are easy to parse."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "--color=no",
        "-p",
        "no:cacheprovider",
        *extra,
    ]


def _run(cmd: list[str]) -> tuple[str, float]:
    """Run a command from the repo root, returning combined output and elapsed time."""
    start = time.perf_counter()
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr, time.perf_counter() - start


def _parse_run(output: str, wall: float) -> SuiteRun:
    """Parse a --durations=0 pytest run into per-file aggregates."""
    files: dict[str, FileStats] = {}
    slowest: list[tuple[float, str, str]] = []

    for raw in output.splitlines():
        match = _DURATION_RE.match(raw.strip())
        if not match:
            continue
        seconds, phase, node_id = float(match[1]), match[2], match[3]
        file_name = node_id.split("::")[0]
        stats = files.setdefault(file_name, FileStats(name=file_name))
        stats.total += seconds
        setattr(stats, phase, getattr(stats, phase) + seconds)
        if phase == "call":
            stats.tests += 1
            slowest.append((seconds, node_id, phase))

    passed = 0
    if found := _SUMMARY_RE.search(output):
        passed = int(found[1])

    slowest.sort(reverse=True)
    return SuiteRun(wall=wall, passed=passed, files=files, slowest=slowest)


def measure_noise_floor(runs: int = NOISE_RUNS) -> tuple[float, float]:
    """Run the suite repeatedly to establish run-to-run variance.

    Returns:
        Tuple of (mean wall seconds, spread between fastest and slowest run).
    """
    timings: list[float] = []
    for index in range(runs):
        console.print(f"  [dim]noise run {index + 1}/{runs}...[/dim]")
        output, _ = _run(_pytest_cmd("-q", "--no-header"))
        if found := _WALL_RE.search(output):
            timings.append(float(found[1]))
    if not timings:
        return 0.0, 0.0
    return statistics.fmean(timings), max(timings) - min(timings)


def measure_fixed_overhead() -> tuple[float, int]:
    """Measure collection cost: interpreter start + imports + test discovery.

    Returns:
        Tuple of (seconds spent collecting, number of tests collected).
    """
    output, elapsed = _run(_pytest_cmd("--collect-only", "-q"))
    collected = 0
    if found := re.search(r"(\d+) tests? collected", output):
        collected = int(found[1])
    return elapsed, collected


def _render_files(run: SuiteRun) -> None:
    """Print the per-file cost table -- the primary bird's-eye view."""
    table = Table(
        title="WHERE THE TIME GOES (serial run, per file)",
        box=SIMPLE_HEAVY,
        title_style="bold cyan",
    )
    table.add_column("File", style="white", no_wrap=True)
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Tests", justify="right")
    table.add_column("Per test", justify="right")
    table.add_column("Call", justify="right", style="dim")
    table.add_column("Setup", justify="right", style="dim")
    table.add_column("Share", justify="right")

    total = run.accounted or 1.0
    suite_average = total / max(run.passed, 1)
    for stats in sorted(run.files.values(), key=lambda f: f.total, reverse=True):
        share = stats.total / total * 100
        # Flag files whose per-test cost is above the suite average: these are
        # where a single test buys the least coverage per second spent.
        name = stats.name.removeprefix("tests/")
        label = f"[yellow]{name}[/yellow]" if stats.per_test > suite_average else name
        table.add_row(
            label,
            f"{stats.total:.2f}s",
            str(stats.tests),
            f"{stats.per_test:.3f}s",
            f"{stats.call:.2f}s",
            f"{stats.setup:.2f}s",
            f"{share:4.1f}%",
        )
    console.print(table)


def _render_slowest(run: SuiteRun) -> None:
    """Print the slowest individual tests."""
    table = Table(
        title=f"SLOWEST {TOP_SLOW_TESTS} TESTS",
        box=SIMPLE_HEAVY,
        title_style="bold cyan",
    )
    table.add_column("Time", justify="right", style="bold")
    table.add_column("Test", overflow="fold")
    for seconds, node_id, _ in run.slowest[:TOP_SLOW_TESTS]:
        table.add_row(f"{seconds:.3f}s", node_id.removeprefix("tests/"))
    console.print(table)


def _render_phases(run: SuiteRun) -> None:
    """Print how time splits between fixture setup and test bodies."""
    call = sum(f.call for f in run.files.values())
    setup = sum(f.setup for f in run.files.values())
    teardown = sum(f.teardown for f in run.files.values())
    table = Table(title="PHASE SPLIT", box=SIMPLE_HEAVY, title_style="bold cyan")
    table.add_column("Phase")
    table.add_column("Time", justify="right", style="bold")
    table.add_column("Meaning", style="dim")
    table.add_row("call", f"{call:.2f}s", "test bodies (the work under test)")
    table.add_row("setup", f"{setup:.2f}s", "fixtures: temp dirs, mock data, config")
    table.add_row("teardown", f"{teardown:.2f}s", "cleanup")
    console.print(table)


def analyze_suite() -> SuiteRun:
    """Run the suite serially with full durations and print the breakdown.

    Returns:
        The parsed SuiteRun for reuse by other report sections.
    """
    console.print("\n[bold]Running suite serially with full timings...[/bold]")
    # --durations-min=0 is essential: pytest hides sub-5ms entries by default,
    # which silently drops whole files of fast tests from the breakdown.
    output, wall = _run(
        _pytest_cmd(
            "-q",
            "--no-header",
            "--durations=0",
            "--durations-min=0",
            "-n",
            "0",
        ),
    )
    run = _parse_run(output, wall)

    if not run.files:
        console.print("[red]No timing data parsed -- did the suite fail?[/red]")
        console.print(output[-2000:])
        return run

    unattributed = run.wall - run.accounted
    console.print(
        Panel(
            f"[bold]{run.passed}[/bold] tests | "
            f"wall [bold]{run.wall:.2f}s[/bold] | "
            f"attributed to tests [bold]{run.accounted:.2f}s[/bold] | "
            f"fixed overhead (startup, imports, collection) "
            f"[bold]{unattributed:.2f}s[/bold]",
            title="SERIAL SUITE",
            border_style="cyan",
        ),
    )
    _render_files(run)
    _render_phases(run)
    _render_slowest(run)
    return run


def compare_workers(counts: tuple[int, ...] = (0, 2, 4, 8)) -> None:
    """Compare wall time across xdist worker counts.

    More workers is not automatically faster: each worker is a fresh process
    that re-imports pandas and rebuilds session-scoped fixtures, so past a
    point the startup tax exceeds the parallelism gain.

    Args:
        counts: Worker counts to try. 0 means serial (no xdist).
    """
    table = Table(
        title="XDIST WORKER SCALING",
        box=SIMPLE_HEAVY,
        title_style="bold cyan",
    )
    table.add_column("Workers")
    table.add_column("Wall", justify="right", style="bold")
    table.add_column("vs serial", justify="right")

    baseline: float | None = None
    for count in counts:
        label = "serial" if count == 0 else f"-n {count}"
        console.print(f"  [dim]timing {label}...[/dim]")
        output, _ = _run(_pytest_cmd("-q", "--no-header", "-n", str(count)))
        found = _WALL_RE.search(output)
        if not found:
            table.add_row(label, "[red]failed[/red]", "-")
            continue
        wall = float(found[1])
        if baseline is None:
            baseline = wall
        delta = f"{(1 - wall / baseline) * 100:+.0f}%" if baseline else "-"
        table.add_row(label, f"{wall:.2f}s", delta)
    console.print(table)


def profile_hot_functions() -> None:
    """Profile the suite and rank application functions by self time."""
    import cProfile  # noqa: PLC0415
    import pstats  # noqa: PLC0415

    import pytest  # noqa: PLC0415

    console.print("\n[bold]Profiling suite (this runs tests in-process)...[/bold]")
    profiler = cProfile.Profile()
    profiler.enable()
    pytest.main(["tests", "-q", "--no-header", "-p", "no:cacheprovider", "-n", "0"])
    profiler.disable()
    sys.stdout.flush()

    stats = pstats.Stats(profiler)
    src_dir = (REPO_ROOT / "src").resolve()
    tests_dir = (REPO_ROOT / "tests").resolve()

    rows: list[tuple[float, float, int, str]] = []
    for func, entry in stats.stats.items():  # ty: ignore[unresolved-attribute]
        filename = func[0] or ""
        try:
            resolved = Path(filename).resolve()
        except (OSError, ValueError):
            continue
        if src_dir not in resolved.parents and tests_dir not in resolved.parents:
            continue
        calls, _, tottime, cumtime = entry[0], entry[1], entry[2], entry[3]
        rows.append((tottime, cumtime, calls, f"{resolved.name}:{func[1]}({func[2]})"))

    rows.sort(reverse=True)
    table = Table(
        title=f"HOTTEST {TOP_HOT_FUNCTIONS} APPLICATION FUNCTIONS (by self time)",
        box=SIMPLE_HEAVY,
        title_style="bold cyan",
    )
    table.add_column("Self", justify="right", style="bold")
    table.add_column("Cumulative", justify="right", style="dim")
    table.add_column("Calls", justify="right")
    table.add_column("Function")
    for tottime, cumtime, calls, name in rows[:TOP_HOT_FUNCTIONS]:
        table.add_row(f"{tottime:.3f}s", f"{cumtime:.3f}s", str(calls), name)
    console.print(table)
    console.print(
        "[dim]Self time is the honest ranking. Cumulative time is shown only for "
        "context: it double-counts nested calls and inflates @contextmanager "
        "helpers with the body of every 'with' block they wrap.[/dim]",
    )


def _get_src_module_names() -> set[str]:
    """Discover all top-level module names under the src directory.

    Returns:
        Set of module names (e.g., {'app', 'db', 'cli', ...}).
    """
    src_dir = REPO_ROOT / "src"
    if not src_dir.exists():
        return set()
    return {
        item.name
        for item in src_dir.iterdir()
        if item.is_dir()
        and not item.name.startswith("_")
        and item.name != "__pycache__"
    }


def _discover_test_imports() -> set[str]:
    """Discover src/ module imports used across all test files.

    Returns:
        Set of unique module names imported by the test suite.
    """
    src_modules = _get_src_module_names()
    modules: set[str] = set()
    for test_file in (REPO_ROOT / "tests").glob("test_*.py"):
        try:
            tree = ast.parse(test_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.level == 0
                and node.module.split(".")[0] in src_modules
            ):
                modules.add(node.module)
    return modules


def profile_imports() -> None:
    """Report import cost for every src module the tests pull in.

    Import time is paid once per process -- which means once per xdist worker,
    so it is the tax that limits useful parallelism.
    """
    console.print("\n[bold]Measuring module import times...[/bold]")
    results: list[tuple[str, float]] = []
    for module_name in sorted(_discover_test_imports()):
        code = (
            "import sys, time; sys.path.insert(0, 'src'); "
            "t = time.perf_counter(); "
            f"__import__({module_name!r}); "
            "print(time.perf_counter() - t)"
        )
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            results.append((module_name, float(proc.stdout.strip()) * 1000))

    results.sort(key=lambda item: item[1], reverse=True)
    table = Table(
        title="MODULE IMPORT COST (fresh interpreter, cold cache)",
        box=SIMPLE_HEAVY,
        title_style="bold cyan",
    )
    table.add_column("Module")
    table.add_column("Import", justify="right", style="bold")
    table.add_column("", justify="left")
    for module_name, duration in results:
        flag = "[yellow]SLOW[/yellow]" if duration > SLOW_IMPORT_THRESHOLD_MS else ""
        table.add_row(module_name, f"{duration:8.1f}ms", flag)
    console.print(table)
    console.print(
        "[dim]Each figure includes shared dependencies (pandas, market "
        "calendars), so these do not sum to total startup.[/dim]",
    )


def quick_run() -> None:
    """Time a single suite run using the project's configured defaults."""
    console.print("\n[bold]Running suite with configured defaults...[/bold]")
    output, elapsed = _run(_pytest_cmd("-q", "--no-header"))
    passed = found[1] if (found := _SUMMARY_RE.search(output)) else "?"
    console.print(
        Panel(
            f"[bold]{passed}[/bold] tests in [bold]{elapsed:.2f}s[/bold]",
            title="QUICK RUN",
            border_style="green",
        ),
    )


def full_report() -> None:
    """Run every analysis section and print an actionable summary."""
    console.rule("[bold]FOLIO TEST SUITE PERFORMANCE[/bold]")

    overhead, collected = measure_fixed_overhead()
    console.print(
        f"\nCollection (interpreter + imports + discovery): "
        f"[bold]{overhead:.2f}s[/bold] for [bold]{collected}[/bold] tests. "
        "This is the floor every run pays, and every xdist worker pays it again.",
    )

    run = analyze_suite()
    if not run.files:
        return

    console.print("\n[bold]Establishing noise floor...[/bold]")
    mean, spread = measure_noise_floor()
    console.print(
        Panel(
            f"mean [bold]{mean:.2f}s[/bold] across {NOISE_RUNS} runs, "
            f"spread [bold]{spread:.2f}s[/bold]\n"
            "[dim]Treat any improvement smaller than the spread as noise.[/dim]",
            title="NOISE FLOOR (configured defaults)",
            border_style="magenta",
        ),
    )
    compare_workers()


def main() -> None:
    """Parse arguments and dispatch to the requested analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Time one run only")
    parser.add_argument("--imports", action="store_true", help="Module import cost")
    parser.add_argument("--profile", action="store_true", help="Hot function profile")
    parser.add_argument("--workers", action="store_true", help="xdist worker scaling")
    parser.add_argument("--suite", action="store_true", help="Per-file breakdown only")
    args = parser.parse_args()

    if args.quick:
        quick_run()
    elif args.imports:
        profile_imports()
    elif args.profile:
        profile_hot_functions()
    elif args.workers:
        compare_workers()
    elif args.suite:
        analyze_suite()
    else:
        full_report()


if __name__ == "__main__":
    main()
