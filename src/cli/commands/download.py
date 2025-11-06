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
from app.app_context import get_config
from db import db
from models.activity_feed_item import ActivityFeedItem
from services.ibkr_service import DownloadRequest, IBKRService, IBKRServiceError
from services.wealthsimple_service import WealthsimpleService
from utils.constants import TORONTO_TZ, Column, Table
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from models.activity_feed_item import ActivityFeedItem
    from utils.config import Config

app = typer.Typer()
logger = logging.getLogger(__name__)
import_logger = get_import_logger()

SUPPORTED_BROKERS = {"ibkr", "wealthsimple"}


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
    *,
    token: bool = typer.Option(
        default=False,
        help="Prompt to set/override the flex token for IBKR",
    ),
    reference_code: str | None = typer.Option(
        None,
        "-r",
        "--reference",
        help="Reference code to retry download for IBKR",
    ),
) -> None:
    """Download transactions from broker and save as CSV files.

    This command downloads Flex Query statements from Interactive Brokers
    and saves them as CSV files in the imports directory for later import.

    If no flex token is found in the keyring, you will be prompted to enter it.
    Use --token to force setting a new token (exits after setting).
    """
    config = bootstrap.reload_config()
    if broker not in SUPPORTED_BROKERS:
        typer.echo(
            f"Error: Broker '{broker}' not supported. "
            f"Supported brokers: {SUPPORTED_BROKERS}",
            err=True,
        )
        raise typer.Exit(1)

    if broker == "wealthsimple":
        wealthsimple_transactions(from_date, to_date)

    if broker == "ibkr":
        _handle_ibkr_download(
            config=config,
            from_date=from_date,
            to_date=to_date,
            token=token,
            reference_code=reference_code,
        )


def _handle_ibkr_download(
    config: Config,
    from_date: str | None,
    to_date: str | None,
    reference_code: str | None,
    *,
    token: bool,
) -> None:
    files_downloaded: bool = False
    with IBKRService() as ibkr:
        if _handle_ibkr_token(ibkr, token=token):
            return

        if reference_code:
            _handle_ibkr_reference_code(ibkr, reference_code)
            return

        broker_config: dict[str, str] = _get_broker_config(config, "ibkr")
        resolved_to_date: str = _resolve_to_date(to_date)
        resolved_from_date: str = _resolve_from_date(from_date, "ibkr")
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


def _handle_ibkr_token(ibkr: IBKRService, *, token: bool) -> bool:
    if token:
        typer.echo("Setting new IBKR flex token...")
        new_token = typer.prompt(
            "Enter your IBKR flex token",
            hide_input=True,
            confirmation_prompt=True,
        )
        ibkr.set_token(new_token)
        typer.echo("✓ Flex token stored securely")
        return True

    try:
        ibkr.get_token()
    except IBKRServiceError:
        typer.echo("No IBKR flex token found in keyring.")
        new_token = typer.prompt(
            "Enter your IBKR flex token",
            hide_input=True,
            confirmation_prompt=True,
        )
        ibkr.set_token(new_token)
        typer.echo("✓ Flex token stored securely")
    return False


def _handle_ibkr_reference_code(ibkr: IBKRService, reference_code: str) -> None:
    try:
        lines = ibkr.download_and_save_statement(reference_code)
        typer.echo(f"✓ {reference_code}: Received {lines} lines")
    except IBKRServiceError as e:
        typer.echo(f"✗ {reference_code}: {e}", err=True)
        raise typer.Exit(1) from None


def wealthsimple_transactions(
    from_date: str | None,
    to_date: str | None,
) -> None:
    """Retrieve wealthsimple transactions.

    Args:
        from_date (str | None): From date string in YYYY-MM-DD format.
        to_date (str | None): To date string IN YYYY-MM-DD format.
    """
    ws = WealthsimpleService()
    resolved_to_date: str = _resolve_to_date(to_date)
    resolved_from_date: str = _resolve_from_date(from_date, "ws")
    from_dt = datetime.strptime(resolved_from_date, "%Y%m%d").replace(
        tzinfo=TORONTO_TZ,
    )
    typer.echo(f"From Date: {from_dt.isoformat()}")
    to_dt = datetime.strptime(resolved_to_date, "%Y%m%d").replace(
        tzinfo=TORONTO_TZ,
    )

    accounts = [a["id"] for a in ws.get_accounts()]
    activities: list[ActivityFeedItem] = ws.get_activities(
        accounts,
        from_dt,
        to_dt,
        load_all=True,
    )

    typer.echo(f"\nRetrieved {len(activities)} transactions\n")

    if len(activities) > 0:
        csv_name = f"ws_activities_{resolved_from_date}_{resolved_to_date}.csv"
        ws.export_activities_to_csv(activities, csv_name)
        typer.echo(f'Files saved to: "{get_config().imports_path}"')
        typer.echo("\nTo import these files, run:")
        typer.echo("  folio import --dir default")
    else:
        typer.echo("\n⚠ No transactions downloaded")


def _resolve_from_date(
    from_date_str: str | None,
    broker: str,
    account_override: str | None = None,
) -> str:
    """Resolve the from date, using fallbacks if not specified.

    Fallback to latest transaction date from broker, or first of current month.

    Args:
        from_date_str (str | None): The from date string provided by user.
        broker (str): The broker identifier.
        account_override (str | None): Optional account to filter transactions.

    Returns:
        str: The resolved from date in YYYYMMDD format.
    """
    if from_date_str:
        return _format_date_for_api(from_date_str)

    # Fallback handling
    try:
        with db.get_connection() as conn:
            if account_override:
                account_has_broker = f"Account = '{account_override}'"
            else:  # pragma: no cover
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
    if fallback_date == current_date:  # pragma: no cover
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


def _get_previous_month(dt: datetime) -> datetime:  # pragma: no cover
    """Return a datetime object set to the first day of the previous month."""
    year = dt.year
    month = dt.month
    if month == 1:
        year -= 1
        month = 12
    else:
        month -= 1
    return dt.replace(year=year, month=month, day=1)


def _print_activity_feed_item(activity: ActivityFeedItem) -> None:  # pragma: no cover
    """Print a formatted activity feed item.

    Args:
        activity: The activity feed item to print.
    """
    typer.echo(f"Date: {activity.occurred_at.isoformat()}")
    typer.echo(f"Account ID: {activity.account_id}")
    typer.echo(f"Type: {activity.type} ({activity.sub_type})")
    typer.echo(f"Asset: {activity.asset_symbol}")
    typer.echo(f"Quantity: {activity.asset_quantity}")
    typer.echo(f"Amount: {activity.amount} {activity.currency}")
    typer.echo(f"Status: {activity.status}")
    typer.echo(f"Description: {activity.description}")
    typer.echo("-" * 60)


if __name__ == "__main__":
    app()
