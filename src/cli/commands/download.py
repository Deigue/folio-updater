"""The `folio download` command, and the download steps `folio update` shares."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from domain import BrokerId
from services import BrokerError, BrokerRegistry
from term import (
    console_error,
    console_info,
    console_success,
    console_warning,
)
from term.progress import spinner
from ui.views.downloads import show_download
from ui.vocabulary import SPINNER_BROKERS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.progress import Progress

    from services import Broker, DownloadResult, RetryingBroker, StatementBroker

logger = logging.getLogger(__name__)


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
        broker: Broker to download from, as `-b` names it.
        from_date: From date in YYYY-MM-DD format, or None for the latest stored.
        to_date: To date in YYYY-MM-DD format, or None for today.
        credentials: Reset the broker's stored credentials instead.
        statement: Download the monthly statement of `from_date` (Wealthsimple).
        reference_code: Retry one earlier IBKR request by its reference code.

    Raises:
        typer.Exit: On an unusable request.
    """
    bootstrap.reload_config()
    chosen = next((b for b in BrokerId if b.cli_name == broker), None)
    if chosen is None:
        console_error(
            f"Broker '{broker}' not supported. Supported brokers: "
            f"{', '.join(b.cli_name for b in BrokerId)}",
        )
        raise typer.Exit(1)

    brokers = BrokerRegistry.load()
    if credentials:
        brokers.get(chosen).reset_credentials()
        return

    try:
        if chosen is BrokerId.WS and statement and from_date:
            _download_and_list_statements(brokers.wealthsimple, from_date[:7])
        elif chosen is BrokerId.IBKR and reference_code:
            _retry(brokers.ibkr, reference_code)
        else:
            start, end = _parse_date(from_date), _parse_date(to_date)
            if download_from(brokers.get(chosen), start, end).files:
                console_info("To import these files, run:")
                console_info("  folio import")
    except BrokerError as e:
        console_error(f"{reference_code or e.broker.display_name}: {e}")
        raise typer.Exit(1) from None


def download_from(
    broker: Broker,
    start: date | None,
    end: date | None,
) -> DownloadResult:
    """Sign in, download under the broker's spinner, and show what came.

    The step `folio download` and `folio update` share; each adds its own advice.

    Raises:
        BrokerError: When nothing could be downloaded at all.
    """
    broker.sign_in()
    with spinner(SPINNER_BROKERS[broker.id]) as progress:
        result = broker.download(start, end, _describer(progress))
    show_download(result)
    return result


def download_monthly_statements(source: StatementBroker, month: str) -> list[Path]:
    """Sign in and download every account's monthly statement for a YYYY-MM month.

    Returns:
        The statement files written, empty when the month is not published yet.

    Raises:
        BrokerError: When the statements could not be downloaded.
    """
    source.sign_in()
    with spinner(SPINNER_BROKERS[source.id]) as progress:
        return source.monthly_statements(month, _describer(progress))


def _download_and_list_statements(source: StatementBroker, month: str) -> None:
    """Download a month's statements, and show where they went and how to import."""
    files = download_monthly_statements(source, month)
    if not files:
        console_warning("No statement rows downloaded")
        return
    console_success(f'Files saved to: "{get_config().statements_path}"')
    for path in files:
        console_info(f"  - {path.name}")
    console_info("To import these statement files, run:")
    console_info("  folio settle-info --import")


def _retry(broker: RetryingBroker, reference_code: str) -> None:
    """Download the statement of an earlier request again by its reference code."""
    broker.sign_in()
    with spinner(SPINNER_BROKERS[broker.id]) as progress:
        describe = _describer(progress)
        describe("Downloading by reference code...")
        result = broker.retry(reference_code, describe)
    show_download(result)


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
