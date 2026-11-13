"""Download command for the folio CLI.

Handles downloading statements from brokers like IBKR.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from services import BrokerError, BrokerRegistry, IbkrBroker
from term import (
    console_error,
    console_info,
    console_success,
    console_warning,
)
from term.progress import spinner
from ui.views.downloads import show_preview
from ui.vocabulary import SPINNER_IBKR, SPINNER_WEALTHSIMPLE

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.progress import Progress

    from config import Config

logger = logging.getLogger(__name__)

SUPPORTED_BROKERS = {"ibkr", "wealthsimple"}


def download_statements(
    broker: str = "ibkr",
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    credentials: bool = False,
    statement: bool = False,
    reference_code: str | None = None,
) -> None:
    """Download transactions from broker and save as CSV file.

    Args:
        broker: Broker to download from.
        from_date: From date in YYYY-MM-DD format, or None for the latest stored.
        to_date: To date in YYYY-MM-DD format, or None for today.
        credentials: Reset the broker's stored credentials instead.
        statement: Download the monthly statement of `from_date` (Wealthsimple).
        reference_code: Retry one earlier IBKR request by its reference code.

    Raises:
        typer.Exit: On an unusable request.
    """
    config = bootstrap.reload_config()
    if broker not in SUPPORTED_BROKERS:
        console_error(
            f"Broker '{broker}' not supported. Supported brokers: {SUPPORTED_BROKERS}",
        )
        raise typer.Exit(1)

    if credentials:  # pragma: no cover
        _handle_credentials(broker)
        return

    try:
        if broker == "wealthsimple" and statement and from_date:
            download_ws_statement(from_date)
        elif broker == "wealthsimple":
            download_wealthsimple(from_date, to_date)
        elif reference_code:
            _retry_ibkr(config, reference_code)
        else:
            download_ibkr(config, from_date, to_date)
    except BrokerError as e:
        console_error(f"{reference_code or e.broker.value.upper()}: {e}")
        raise typer.Exit(1) from None


def download_ibkr(
    config: Config,
    from_date: str | None,
    to_date: str | None,
) -> list[str]:
    """Download every configured IBKR flex query into the imports folder.

    Args:
        config: The application configuration.
        from_date: From date in YYYY-MM-DD format, or None for the overlap day.
        to_date: To date in YYYY-MM-DD format, or None for today.

    Returns:
        One line per query that failed, naming the query and the error. Empty
        when every query succeeded.
    """
    start, end = _parse_date(from_date), _parse_date(to_date)
    ibkr = BrokerRegistry.load().ibkr
    ibkr.sign_in()
    with spinner(SPINNER_IBKR) as progress:
        result = ibkr.download(start, end, _describer(progress))

    if result.files:
        console_success(f'Files saved to: "{config.imports_path}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No source files downloaded")
    return [f"{failure.part}: {failure.error}" for failure in result.failures]


def _retry_ibkr(config: Config, reference_code: str) -> None:
    """Fetch the statement of an earlier IBKR request by its reference code."""
    ibkr = IbkrBroker(config.ibkr)
    ibkr.sign_in()
    with spinner(SPINNER_IBKR) as progress:
        describe = _describer(progress)
        describe("Downloading by reference code...")
        ibkr.retry(reference_code, describe)


def _handle_credentials(broker: str) -> None:  # pragma: no cover
    """Reset the stored credentials of the specified broker.

    Args:
        broker: The broker identifier.
    """
    brokers = BrokerRegistry.load()
    if broker == "ibkr":
        brokers.ibkr.reset_credentials()
    elif broker == "wealthsimple":
        brokers.wealthsimple.reset_credentials()
    else:
        console_error(f"Credential management not supported for broker '{broker}'")
        raise typer.Exit(1)


def download_wealthsimple(
    from_date: str | None,
    to_date: str | None,
) -> None:
    """Download every Wealthsimple account's activities into the imports folder.

    Args:
        from_date: From date in YYYY-MM-DD format, or None for the overlap day.
        to_date: To date in YYYY-MM-DD format, or None for today.
    """
    start, end = _parse_date(from_date), _parse_date(to_date)
    wealthsimple = BrokerRegistry.load().wealthsimple
    wealthsimple.sign_in()
    with spinner(SPINNER_WEALTHSIMPLE) as progress:
        result = wealthsimple.download(start, end, _describer(progress))

    for preview in result.previews:
        show_preview(preview)
    if result.files:
        for path in result.files:
            console_success(f'File saved: "{path}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No source rows downloaded")


def download_ws_statement(from_date: str) -> list[Path]:
    """Download every Wealthsimple account's monthly statement for a month.

    Args:
        from_date: Any YYYY-MM-DD day of the month, such as '2024-05-01' for May 2024.

    Returns:
        The statement files written, one per account with transactions. Empty
        when the month has no statement yet.
    """
    wealthsimple = BrokerRegistry.load().wealthsimple
    wealthsimple.sign_in()
    with spinner(SPINNER_WEALTHSIMPLE) as progress:
        files = wealthsimple.monthly_statements(from_date[:7], _describer(progress))

    if files:
        console_success(f'Files saved to: "{get_config().statements_path}"')
        for path in files:
            console_info(f"  - {path.name}")
        console_info("To import these statement files, run:")
        console_info("  folio settle-info --import")
    else:
        console_warning("No statement rows downloaded")
    return files


def _describer(progress: Progress) -> Callable[[str], None]:
    """Start a spinner task, returning what sets the text it shows."""
    task = progress.add_task("Downloading...", total=None)

    def describe(text: str) -> None:
        progress.update(task, description=text)

    return describe


def _parse_date(text: str | None) -> date | None:
    """Read a YYYY-MM-DD date the user typed, or None when none was given.

    Raises:
        typer.Exit: When the date is not YYYY-MM-DD.
    """
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as e:
        console_error(f"Error: Invalid date format. Use YYYY-MM-DD: {e}")
        raise typer.Exit(1) from e
