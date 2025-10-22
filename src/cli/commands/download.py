"""Download command for the folio CLI.

Handles downloading statements from brokers like IBKR.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import typer

from app import bootstrap
from db import db
from services.ibkr_service import DownloadRequest, IBKRService, IBKRServiceError
from utils.constants import TORONTO_TZ, Column, Table
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from utils.config import Config

app = typer.Typer()
logger = logging.getLogger(__name__)
import_logger = get_import_logger()

SUPPORTED_BROKERS = {"ibkr"}


@app.command(name="")
def download_statements(
    broker: str = typer.Option(
        "ibkr",
        "-b",
        "--broker",
        help="Broker to download from (default: 'ibkr')",
    ),
    from_date: str | None = typer.Option(
        None,
        "-f",
        "--from",
        help="Date in YYYY-MM-DD format (default: latest transaction from broker)",
    ),
    to_date: str | None = typer.Option(
        None,
        "-t",
        "--to",
        help="Date in YYYY-MM-DD format (default: today)",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Set the flex token for IBKR (will be stored securely)",
    ),
    reference_code: str | None = typer.Option(
        None,
        "-r",
        "--reference",
        help="Reference code to retry download for (IBKR only)",
    ),
) -> None:
    """Download statements from broker and save as CSV files.

    This command downloads Flex Query statements from Interactive Brokers
    and saves them as CSV files in the imports directory for later import.

    Before first use, you need to set your IBKR flex token:
    folio download --broker ibkr --token YOUR_TOKEN
    """
    config = bootstrap.reload_config()
    if broker not in SUPPORTED_BROKERS:
        typer.echo(
            f"Error: Broker '{broker}' not supported. "
            f"Supported brokers: {SUPPORTED_BROKERS}",
            err=True,
        )
        raise typer.Exit(1)

    # ! Firstly handle token as it is needed for everything else
    with IBKRService() as ibkr:
        if token:
            ibkr.set_token(token)
            typer.echo("✓ Flex token stored securely")
            return

        try:
            token = ibkr.get_token()
        except IBKRServiceError as e:
            typer.echo(f"✗ {e}", err=True)
            raise typer.Exit(1) from None

        if reference_code:
            try:
                lines = ibkr.download_and_save_statement(reference_code)
                typer.echo(f"✓ {reference_code}: Received {lines} lines")
            except IBKRServiceError as e:
                typer.echo(f"✗ {reference_code}: {e}", err=True)
                raise typer.Exit(1) from None

        broker_config: dict[str, str] = _get_broker_config(config, broker)
        resolved_to_date: str = _resolve_to_date(to_date)
        resolved_from_date: str = _resolve_from_date(from_date, broker)
        files_downloaded: bool = False
        placeholder_ids = {"111111", "999999"}

        for query_name, query_id in broker_config.items():
            if not query_id or query_id in placeholder_ids:
                typer.echo(
                    f"Skipping {query_name}: No valid query ID configured",
                    err=True,
                )
                continue
            try:
                request = DownloadRequest(
                    query_name=query_name,
                    query_id=query_id,
                    from_date=resolved_from_date,
                    to_date=resolved_to_date,
                )
                lines: int = ibkr.download_and_save_statement(request)
                files_downloaded = True
                typer.echo(f"✓ {query_name}: {lines} lines received")
            except IBKRServiceError as e:
                typer.echo(f"✗ {query_name}: {e}", err=True)

    if files_downloaded:
        typer.echo(f'Files saved to: "{config.imports_path}"')
        typer.echo("\nTo import these files, run:")
        typer.echo("  folio import --dir default")
    else:
        typer.echo("\n⚠ No transactions downloaded")


def _resolve_from_date(from_date_str: str | None, broker: str) -> str:
    """Resolve the from date, using fallbacks if not specified.

    Fallback to latest transaction date from broker, or first of current month.

    Args:
        from_date_str (str | None): The from date string provided by user.
        broker (str): The broker identifier.

    Returns:
        str: The resolved from date in YYYYMMDD format.
    """
    if from_date_str:
        return _format_date_for_api(from_date_str)

    # Fallback handling
    try:
        with db.get_connection() as conn:
            account_has_broker = f"UPPER(Account) LIKE UPPER('%{broker}%')"
            latest_date_str: str | None = db.get_max_value(
                conn,
                Table.TXNS,
                Column.Txn.TXN_DATE,
                account_has_broker,
            )
            if latest_date_str:
                latest_date = datetime.strptime(
                    latest_date_str,
                    "%Y-%m-%d",
                ).replace(tzinfo=TORONTO_TZ)
                resolved_date = latest_date.strftime("%Y%m%d")
                typer.echo(
                    f"from_date: Using latest {broker.upper()} transaction date: "
                    f"{latest_date_str}",
                )
                return resolved_date
    except (ValueError, OSError, sqlite3.Error) as e:
        typer.echo(
            f"Warning: Could not determine latest transaction date: {e}",
            err=True,
        )

    current_date = datetime.now(tz=TORONTO_TZ)
    fallback_date = current_date.replace(day=1)
    if fallback_date == current_date:
        fallback_date = _get_previous_month(fallback_date)
    resolved_date = fallback_date.strftime("%Y%m%d")
    formatted_date = fallback_date.strftime("%Y-%m-%d")
    typer.echo(f"from_date: Using fallback date: {formatted_date}")
    return resolved_date


def _resolve_to_date(to_date_str: str | None) -> str:
    """Resolve the to date, using today if not specified.

    Args:
        to_date_str (str | None): The to date string provided by user.

    Returns:
        str: The resolved to date in YYYYMMDD format.
    """
    if to_date_str:
        return _format_date_for_api(to_date_str)
    today = datetime.now(tz=TORONTO_TZ)
    return today.strftime("%Y%m%d")


def _get_broker_config(config: Config, broker: str) -> dict[str, str]:
    """Get broker configuration for the specified broker.

    Args:
        config (Config): The application configuration.
        broker (str): The broker identifier.

    Returns:
        dict[str, str]: The broker configuration.
    """
    broker_config: dict[str, str] | None = config.brokers.get(broker)
    if not broker_config:
        typer.echo(
            f"Error: No configuration found for broker '{broker}' in config.yaml",
            err=True,
        )
        typer.echo("Please add broker configuration to config.yaml", err=True)
        raise typer.Exit(1)
    return broker_config


def _format_date_for_api(date_str: str) -> str:
    """Format date string to YYYYMMDD for API.

    Args:
        date_str (str): Date string in YYYY-MM-DD format.

    Returns:
        str: Date string in YYYYMMDD format.
    """
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=TORONTO_TZ,
        )
        return parsed_date.strftime("%Y%m%d")
    except ValueError as e:
        typer.echo(
            f"Error: Invalid date format. Use YYYY-MM-DD: {e}",
            err=True,
        )
        raise typer.Exit(1) from e


def _get_previous_month(dt: datetime) -> datetime:
    """Return a datetime object set to the first day of the previous month."""
    year = dt.year
    month = dt.month
    if month == 1:
        year -= 1
        month = 12
    else:
        month -= 1
    return dt.replace(year=year, month=month, day=1)


if __name__ == "__main__":
    app()
