"""Download command for the folio CLI.

Handles downloading statements from brokers like IBKR.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from cli import (
    ProgressDisplay,
    TransactionDisplay,
    console_error,
    console_info,
    console_print,
    console_rule,
    console_success,
    console_warning,
)
from db import get_connection, get_max_value
from models.wealthsimple import ActivityFeedItem
from services import DownloadRequest, IBKRService, IBKRServiceError, WealthsimpleService
from utils import TORONTO_TZ, Column, Table, get_import_logger

if TYPE_CHECKING:
    from models.wealthsimple.activity_feed_item import ActivityFeedItem
    from utils import Config

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
    credentials: bool = typer.Option(
        default=False,
        help="Reset credentials for the broker",
    ),
    statement: bool = typer.Option(
        default=False,
        help="Download monthly statement using from date (Wealthsimple only)",
    ),
    reference_code: str | None = typer.Option(
        None,
        "-r",
        "--reference",
        help="Reference code to retry download for IBKR",
    ),
) -> None:
    """Download transactions from broker and save as CSV file."""
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
            wealthsimple_statement(from_date)
        else:
            wealthsimple_transactions(from_date, to_date)

    if broker == "ibkr":
        _handle_ibkr_download(
            config=config,
            from_date=from_date,
            to_date=to_date,
            reference_code=reference_code,
        )


def _handle_ibkr_download(
    config: Config,
    from_date: str | None,
    to_date: str | None,
    reference_code: str | None,
) -> None:
    files_downloaded: bool = False
    with IBKRService() as ibkr:
        _ensure_ibkr_token(ibkr)

        if reference_code:
            _handle_ibkr_reference_code(ibkr, reference_code)
            return

        broker_config: dict[str, str] = _get_broker_config(config, "ibkr")
        resolved_to_date: str = _resolve_to_date(to_date)
        resolved_from_date: str = _resolve_from_date(from_date, "ibkr")
        placeholder_ids = {"111111", "999999"}

        for query_name, query_id in broker_config.items():
            if not query_id or query_id in placeholder_ids:
                console_warning(f"Skipping {query_name}: No valid query ID configured")
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
                console_success(f"{query_name}: {lines} lines received")
            except IBKRServiceError as e:
                console_error(f"{query_name}: {e}")

    if files_downloaded:
        console_success(f'Files saved to: "{config.imports_path}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No transactions downloaded")


def _ensure_ibkr_token(ibkr: IBKRService) -> None:
    """Ensure IBKR token exists, prompting if necessary.

    Args:
        ibkr: The IBKR service instance.
    """
    try:
        ibkr.get_token()
    except IBKRServiceError:
        console_info("No IBKR flex token found in keyring.")
        new_token = typer.prompt(
            "Enter your IBKR flex token",
            hide_input=True,
            confirmation_prompt=True,
        )
        ibkr.set_token(new_token)
        console_success("Flex token stored securely")


def _handle_ibkr_reference_code(ibkr: IBKRService, reference_code: str) -> None:
    try:
        lines = ibkr.download_and_save_statement(reference_code)
        console_success(f"{reference_code}: Received {lines} lines")
    except IBKRServiceError as e:
        console_error(f"{reference_code}: {e}")
        raise typer.Exit(1) from None


def _handle_credentials(broker: str) -> None:  # pragma: no cover
    """Manage credentials for the specified broker.

    For IBKR, prompts to set/override the flex token.
    For Wealthsimple, resets stored credentials.

    Args:
        broker: The broker identifier.
    """
    if broker == "ibkr":
        console_info("Setting new IBKR flex token...")
        with IBKRService() as ibkr:
            new_token = typer.prompt(
                "Enter your IBKR flex token",
                hide_input=True,
                confirmation_prompt=True,
            )
            ibkr.set_token(new_token)
            console_success("Flex token stored securely")
    elif broker == "wealthsimple":
        ws = WealthsimpleService()
        ws.reset_credentials()
        console_success("Wealthsimple credentials reset successfully")
    else:
        console_error(f"Credential management not supported for broker '{broker}'")
        raise typer.Exit(1)


def wealthsimple_transactions(
    from_date: str | None,
    to_date: str | None,
) -> None:
    """Download Wealthsimple transactions.

    Args:
        from_date (str | None): From date string in YYYY-MM-DD format.
        to_date (str | None): To date string IN YYYY-MM-DD format.
    """
    ws = WealthsimpleService()
    resolved_to_date: str = _resolve_to_date(to_date)
    resolved_from_date: str = _resolve_from_date(from_date, "ws")

    with ProgressDisplay.spinner_progress("blue") as progress:
        task = progress.add_task("Connecting to Wealthsimple API...", total=None)

        from_dt = datetime.strptime(resolved_from_date, "%Y%m%d").replace(
            tzinfo=TORONTO_TZ,
        )
        to_dt = datetime.strptime(resolved_to_date, "%Y%m%d").replace(
            tzinfo=TORONTO_TZ,
        )
        accounts = [a.id for a in ws.get_accounts()]

        progress.update(task, description="Downloading transactions...")
        activities: list[ActivityFeedItem] = ws.get_activities(
            accounts,
            from_dt,
            to_dt,
            load_all=True,
        )
        progress.remove_task(task)

    console_info(f"Retrieved {len(activities)} transactions")

    if len(activities) > 0:
        display = TransactionDisplay()

        # Display sample of retrieved activities
        description_max_length = 30
        sample_data = [
            {
                "Date": act.occurred_at.strftime("%Y-%m-%d"),
                "Action": act.type,
                "Ticker": act.asset_symbol or "",
                "Amount": float(act.amount) if act.amount else 0,
                "Currency": act.currency,
                "Account": (act.account_id[:8] + "..." if act.account_id else ""),
                "Description": (
                    (act.description[:description_max_length] + "...")
                    if (
                        act.description is not None
                        and len(act.description) > description_max_length
                    )
                    else (act.description if act.description is not None else "")
                ),
            }
            for act in activities[:10]
        ]

        display.show_data_table(
            sample_data,
            title=f"Downloaded Transactions Preview ({len(activities)} total)",
            max_rows=10,
        )

        csv_name = f"ws_activities_{resolved_from_date}_{resolved_to_date}.csv"
        ws.export_activities_to_csv(activities, csv_name)

        console_success(f'File saved: "{get_config().imports_path}/{csv_name}"')
        console_info("To import these files, run:")
        console_info("  folio import")
    else:
        console_warning("No transactions downloaded")


def wealthsimple_statement(from_date: str) -> None:
    """Retrieve wealthsimple monthly statement.

    Args:
        from_date (str | None): From date string in YYYY-MM-DD format.
            Example: '2024-05-01' for May 2024 statement.
    """
    ws = WealthsimpleService()
    accounts = ws.get_accounts()

    total_transactions = 0
    exported_files = []

    for account in accounts:
        statement = ws.get_monthly_statement(account.id, from_date)
        account_id = account.nickname or account.id
        if statement:
            date_for_filename = from_date.replace("-", "")[:6]  # YYYY-MM-DD -> YYYYMM
            csv_name = f"ws_statement_{account_id}_{date_for_filename}.csv"
            ws.export_statement_to_csv(statement, csv_name)
            total_transactions += len(statement)
            exported_files.append(csv_name)
        else:
            console_warning(f"No statement transactions found for account {account_id}")

    if exported_files:
        config = get_config()
        console_success(f"Retrieved {total_transactions} statement transactions")
        console_success(f'Files saved to: "{config.statements_path}"')
        for filename in exported_files:
            console_info(f"  - {filename}")
        console_info("To import these statement files, run:")
        console_info("  folio settle-info --import")
    else:
        console_warning("No statement transactions downloaded")


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
        with get_connection() as conn:
            if account_override:
                account_has_broker = f"Account = '{account_override}'"
            else:  # pragma: no cover
                account_has_broker = f"UPPER(Account) LIKE UPPER('%{broker}%')"
            latest_date_str: str | None = get_max_value(
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
                console_info(
                    f"from_date: Using latest {broker.upper()} transaction date: "
                    f"{latest_date_str}",
                )
                return resolved_date
    except (ValueError, OSError, sqlite3.Error) as e:
        console_warning(f"Warning: Could not determine latest transaction date: {e}")

    current_date = datetime.now(tz=TORONTO_TZ)
    fallback_date = current_date.replace(day=1)
    if fallback_date == current_date:  # pragma: no cover
        fallback_date = _get_previous_month(fallback_date)
    resolved_date = fallback_date.strftime("%Y%m%d")
    formatted_date = fallback_date.strftime("%Y-%m-%d")
    console_info(f"from_date: Using fallback date: {formatted_date}")
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
        console_error(
            f"Error: No configuration found for broker '{broker}' in config.yaml",
        )
        console_error("Please add broker configuration to config.yaml")
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
        console_error(f"Error: Invalid date format. Use YYYY-MM-DD: {e}")
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
    console_print(f"Date: {activity.occurred_at.isoformat()}")
    console_print(f"Account ID: {activity.account_id}")
    console_print(f"Type: {activity.type} ({activity.sub_type})")
    console_print(f"Asset: {activity.asset_symbol}")
    console_print(f"Quantity: {activity.asset_quantity}")
    console_print(f"Amount: {activity.amount} {activity.currency}")
    console_print(f"Status: {activity.status}")
    console_print(f"Description: {activity.description}")
    console_rule()


if __name__ == "__main__":
    app()
