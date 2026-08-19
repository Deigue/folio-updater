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

from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from cli.commands.common import ensure_fx_coverage
from engine.cache import build, load_or_build
from engine.checks import CHECK_SLUGS, UnknownCheckError, run_checks, validate_slugs
from ui import console_error
from ui.views.checks import emit_json, print_report
from utils.constants import CheckStatus

if TYPE_CHECKING:
    from engine.checks import CheckResult


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
        emit_json(results)
    else:
        print_report(results, only=only)

    if any(result.status is CheckStatus.FAIL for result in results):
        raise typer.Exit(1)


__all__ = ["CHECK_SLUGS", "run_folio_checks"]
