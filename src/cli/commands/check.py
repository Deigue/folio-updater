"""The `folio check` command.

Runs the health checks over the folio and reports what is wrong with it in
plain language. It is a **reporter, never a fixer**: it opens the folio, reads
it, and prints. Where a fix has an obvious command it names it, but the user
runs that themselves.

The replay is always freshly computed rather than read from the cost-base
cache, because a cached frame carries no diagnostics: the warnings live on
the replay result the cache deliberately discards.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from cli import console as console_module
from cli import (
    console_error,
    console_print,
    console_success,
    console_warning,
)
from cli.commands.common import ensure_fx_coverage
from cli.console import get_symbol
from engine.cache import build, load_or_build
from engine.checks import CHECK_SLUGS, UnknownCheckError, run_checks, validate_slugs
from utils.constants import CheckStatus

if TYPE_CHECKING:
    from engine.checks import CheckResult

_HEADING_WIDTH = 23
_MIN_WRAP = 40

_STYLES: dict[CheckStatus, tuple[str, str]] = {
    CheckStatus.OK: ("success", "green"),
    CheckStatus.WARN: ("warning", "yellow"),
    CheckStatus.FAIL: ("error", "red"),
}


def _heading(result: CheckResult) -> str:
    """Render one check's heading line: name, icon, and its one-line summary."""
    symbol, colour = _STYLES[result.status]
    return (
        f"[bold]{result.name:<{_HEADING_WIDTH}}[/bold]"
        f"{get_symbol(symbol)}[{colour}]{result.summary}[/{colour}]"
    )


def _print_findings(result: CheckResult) -> None:
    """Print a check's details, indented under its heading."""
    width = max((len(finding.subject) for finding in result.findings), default=0)
    available = console_module.console.width
    stacked = _HEADING_WIDTH + width + 2 + _MIN_WRAP > available
    indent = " " * (_HEADING_WIDTH + (2 if stacked else width + 2))
    room = max(available - len(indent), _MIN_WRAP)

    for finding in result.findings:
        padded = finding.subject if stacked else f"{finding.subject:<{width}}"
        subject = f"{' ' * _HEADING_WIDTH}[bold]{padded}[/bold]"
        if stacked:
            console_print(subject)
        pending = not stacked
        for line in finding.detail.splitlines() or [""]:
            for piece in textwrap.wrap(line, room) or [""]:
                if pending:
                    console_print(f"{subject}  {piece}")
                    pending = False
                else:
                    console_print(f"{indent}{piece}")


def _print_summary(results: list[CheckResult]) -> None:
    """Print the closing tally, and point at `--only` when there is detail."""
    failed = [item for item in results if item.status is CheckStatus.FAIL]
    warned = [item for item in results if item.status is CheckStatus.WARN]

    if not failed and not warned:
        console_success(f"All {len(results)} checks pass. Nothing looks wrong.")
        return

    parts = []
    if failed:
        parts.append(f"{len(failed)} check{'s' if len(failed) > 1 else ''} failed")
    if warned:
        parts.append(f"{len(warned)} warning{'s' if len(warned) > 1 else ''}")
    tail = (failed or warned)[0].slug
    console_print("")
    message = f"{', '.join(parts)}. Run `folio check --only {tail}` for detail."
    if failed:
        console_error(message)
    else:
        console_warning(message)


def _only(results: list[CheckResult], slug: str) -> list[CheckResult]:
    """Narrow the report to one check, or explain why it is not there.

    Args:
        results: Every check that ran.
        slug: The check `--only` asked for. Already known to be a real one.

    Returns:
        The single matching result.

    Raises:
        typer.Exit: With 1 when that check is turned off in config.
    """
    matched = [result for result in results if result.slug == slug]
    if not matched:
        console_error(
            f"The '{slug}' check is turned off in config.yaml, so it did not "
            f"run. Remove it from checks.disabled to see it.",
        )
        raise typer.Exit(1)
    return matched


def _print_report(results: list[CheckResult], only: str | None) -> None:
    """Print the report: one line per check, and detail under the ones that failed."""
    console_print("")
    for result in results:
        console_print(_heading(result))
        # Show succeeding checks only if `--only` is set.
        if result.findings and (
            result.status is not CheckStatus.OK or only is not None
        ):
            _print_findings(result)
    if only is None:
        _print_summary(results)


def _emit_json(results: list[CheckResult]) -> None:
    """Print the results as JSON, for a script that wants to read them.

    Printed straight with a simple `console.print` so it can be consumed.
    """
    payload = json.dumps(
        {
            "checks": [
                {**asdict(result), "status": str(result.status)} for result in results
            ],
            "failed": sum(1 for result in results if result.status is CheckStatus.FAIL),
            "warnings": sum(
                1 for result in results if result.status is CheckStatus.WARN
            ),
        },
        indent=2,
    )
    console_module.console.print(payload, markup=False, soft_wrap=True)


def run_folio_checks(only: str | None = None, *, as_json: bool = False) -> None:
    """Check the folio for issues.

    Args:
        only: Report only this check, showing its findings whether it passed
            or not.
        as_json: Emit machine-readable output instead of the report.

    Raises:
        typer.Exit: With 1 when any check failed, or when the request named a
            check that does not exist.
    """
    bootstrap.reload_config()

    if only is not None:
        try:
            validate_slugs([only])
        except UnknownCheckError as error:
            console_error(str(error))
            raise typer.Exit(1) from error

    ensure_fx_coverage()
    cached = load_or_build()
    result = cached.result or build().result
    if result is None:  # pragma: no cover - `build` always replays
        console_error("Could not replay the folio.")
        raise typer.Exit(1)

    try:
        results = run_checks(result, get_config())
    except UnknownCheckError as error:
        console_error(f"{error} Fix the `checks.disabled` list in config.yaml.")
        raise typer.Exit(1) from error

    if only is not None:
        results = _only(results, only)

    if as_json:
        _emit_json(results)
    else:
        _print_report(results, only=only)

    if any(result.status is CheckStatus.FAIL for result in results):
        raise typer.Exit(1)


__all__ = ["CHECK_SLUGS", "run_folio_checks"]
