"""Download command for the folio CLI.

Handles downloading statements from brokers like IBKR.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from domain import TORONTO_TZ, BrokerId
from services import BrokerError, IbkrBroker, WealthsimpleService, resolve_window
from services.broker_files import ws_activities_file_name, ws_statement_file_name
from term import (
    console_error,
    console_info,
    console_success,
    console_warning,
)
from term.progress import spinner
from ui.views.downloads import show_activities_preview
from ui.vocabulary import SPINNER_IBKR, SPINNER_WEALTHSIMPLE

if TYPE_CHECKING:
    from pathlib import Path

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

    if broker == "wealthsimple":
        if statement and from_date:
            download_ws_statement(from_date)
        else:
            download_wealthsimple(from_date, to_date)

    if broker == "ibkr":
        download_ibkr(
            config=config,
            from_date=from_date,
            to_date=to_date,
            reference_code=reference_code,
        )


def download_ibkr(
    config: Config,
    from_date: str | None,
    to_date: str | None,
    reference_code: str | None,
) -> list[str]:
    """Download every configured IBKR flex query into the imports folder.

    Args:
        config: The application configuration.
        from_date: From date in YYYY-MM-DD format, or None for the overlap day.
        to_date: To date in YYYY-MM-DD format, or None for today.
        reference_code: Retry one earlier request by its reference code instead.

    Returns:
        One line per query that failed, naming the query and the error. Empty
        when every query succeeded.

    Raises:
        typer.Exit: When nothing could be downloaded at all.
    """
    start, end = _parse_date(from_date), _parse_date(to_date)
    ibkr = IbkrBroker(config.ibkr)
    ibkr.sign_in()
    try:
        with spinner(SPINNER_IBKR) as progress:
            task = progress.add_task("Downloading...", total=None)

            def describe(text: str) -> None:
                progress.update(task, description=text)

            if reference_code:
                describe("Downloading by reference code...")
                ibkr.retry(reference_code, describe)
                return []
            result = ibkr.download(start, end, describe)
    except BrokerError as e:
        console_error(f"{reference_code or 'IBKR'}: {e}")
        raise typer.Exit(1) from None

    if result.files:
        console_success(f'Files saved to: "{config.imports_path}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No source files downloaded")
    return [f"{failure.part}: {failure.error}" for failure in result.failures]


def _handle_credentials(broker: str) -> None:  # pragma: no cover
    """Reset the stored credentials of the specified broker.

    Args:
        broker: The broker identifier.
    """
    if broker == "ibkr":
        IbkrBroker(get_config().ibkr).reset_credentials()
    elif broker == "wealthsimple":
        ws = WealthsimpleService()
        ws.reset_credentials()
        console_success("Wealthsimple credentials reset successfully")
    else:
        console_error(f"Credential management not supported for broker '{broker}'")
        raise typer.Exit(1)


def download_wealthsimple(
    from_date: str | None,
    to_date: str | None,
) -> None:
    """Download Wealthsimple transactions.

    Args:
        from_date (str | None): From date string in YYYY-MM-DD format.
        to_date (str | None): To date string IN YYYY-MM-DD format.
    """
    ws = WealthsimpleService()
    window = resolve_window(BrokerId.WS, _parse_date(from_date), _parse_date(to_date))
    resolved_from_date = window.start.strftime("%Y%m%d")
    resolved_to_date = window.end.strftime("%Y%m%d")
    ws.ensure_authenticated()

    with spinner(SPINNER_WEALTHSIMPLE) as progress:
        from_dt = datetime.combine(window.start, time(), tzinfo=TORONTO_TZ)
        to_dt = datetime.combine(window.end, time(), tzinfo=TORONTO_TZ)
        task = progress.add_task("Retrieving accounts...", total=None)
        accounts = ws.get_accounts()
        account_ids = [a.id for a in accounts]

        progress.update(task, description="Downloading transactions...")
        activities = ws.get_activities(
            account_ids,
            from_dt,
            to_dt,
            load_all=True,
        )

    console_info(f"Retrieved {len(activities)} source rows")

    if len(activities) > 0:
        show_activities_preview(activities, accounts)

        csv_name = ws_activities_file_name(resolved_from_date, resolved_to_date)
        ws.export_activities_to_csv(activities, csv_name, accounts)

        console_success(f'File saved: "{get_config().imports_path / csv_name}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No source rows downloaded")


def download_ws_statement(from_date: str) -> list[Path]:
    """Retrieve wealthsimple monthly statement.

    Args:
        from_date: From date string in YYYY-MM-DD format.
            Example: '2024-05-01' for May 2024 statement.

    Returns:
        The statement files written, one per account with transactions. Empty
        when the month has no statement yet.
    """
    ws = WealthsimpleService()
    ws.ensure_authenticated()

    with spinner(SPINNER_WEALTHSIMPLE) as progress:
        task = progress.add_task("Retrieving accounts...", total=None)
        accounts = ws.get_accounts()

        total_rows = 0
        exported_files = []

        progress.update(task, description="Downloading monthly statements...")
        for account in accounts:
            statement_rows = ws.get_monthly_statement(account.id, from_date)
            account_name = account.account_name
            if statement_rows:
                csv_name = ws_statement_file_name(account_name, from_date[:7])
                ws.export_statement_to_csv(statement_rows, csv_name)
                total_rows += len(statement_rows)
                exported_files.append(csv_name)
            else:
                msg = f"No statement rows found for account {account_name}"
                console_warning(msg)

    config = get_config()
    if exported_files:
        console_success(f"Retrieved {total_rows} statement rows")
        console_success(f'Files saved to: "{config.statements_path}"')
        for filename in exported_files:
            console_info(f"  - {filename}")
        console_info("To import these statement files, run:")
        console_info("  folio settle-info --import")
    else:
        console_warning("No statement rows downloaded")
    return [config.statements_path / name for name in exported_files]


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
