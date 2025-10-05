"""Setup initial or default folio for the application."""

import logging
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db import db, schema_manager
from exporters import transaction_exporter
from mock.mock_data import generate_transactions
from services.forex_service import ForexService
from utils.backup import rolling_backup
from utils.constants import DEFAULT_TICKERS, Column, Table
from utils.settlement_calculator import settlement_calculator

if TYPE_CHECKING:
    from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


def ensure_folio_exists(*, mock: bool = True) -> None:
    """Ensure that the folio exists.

    Checks if the folio exists. If mock is True (default), creates a folio with
    mock data if it does not exist.

    Args:
        mock (bool, optional): Whether to create mock data in the folio.

    Raises:
        FileNotFoundError: If the parent directory does not exist.

    """
    configuration = get_config()
    folio_path: Path = configuration.folio_path
    if folio_path.exists():
        logger.debug("Folio file already exists at %s", folio_path)
        return
    if not mock:
        msg: str = f"The folio '{folio_path}' does not exist. Please create it."
        logger.error(msg)
        raise FileNotFoundError(msg)

    folio_path_parent: Path = folio_path.parent
    default_data_dir: Path = configuration.project_root / "data"

    # Only create data folder in automated fashion
    if folio_path_parent.is_relative_to(default_data_dir):
        folio_path_parent.mkdir(parents=True, exist_ok=True)
    elif not folio_path_parent.exists():
        msg: str = (
            f"The folder '{folio_path_parent}' does not exist. Please create it "
            "before running."
        )
        logger.error(msg)
        raise FileNotFoundError(msg)
    else:
        logger.debug("Folio path is valid: %s", folio_path)

    _create_default_folio()


def _create_default_folio() -> None:
    """Create default folio with mock data."""
    configuration = get_config()
    tickers_df = pd.DataFrame({Column.Ticker.TICKER: DEFAULT_TICKERS})
    transactions_list = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
    transactions_df = pd.concat(transactions_list, ignore_index=True)

    # Explicity don't call import_transactions to avoid logging of mock data.
    transactions_df = settlement_calculator.add_settlement_dates_to_dataframe(
        transactions_df,
    )

    schema_manager.create_txns_table()
    with db.get_connection() as conn:
        if db.get_row_count(conn, Table.TXNS) > 0:
            rolling_backup(configuration.db_path)
        transactions_df.to_sql(Table.TXNS, conn, if_exists="append", index=False)
    fx_df = ForexService.get_missing_fx_data()
    ForexService.insert_fx_data(fx_df)

    transactions_df = transaction_exporter.remove_internal_columns(transactions_df)
    with pd.ExcelWriter(configuration.folio_path, engine="openpyxl") as writer:
        tickers_df.to_excel(
            writer,
            index=False,
            sheet_name=configuration.tickers_sheet(),
        )
        transactions_df.to_excel(
            writer,
            index=False,
            sheet_name=configuration.transactions_sheet(),
        )
        fx_df.to_excel(
            writer,
            sheet_name=configuration.forex_sheet(),
            index=False,
        )

    logger.info("Created default folio at %s", configuration.folio_path)
