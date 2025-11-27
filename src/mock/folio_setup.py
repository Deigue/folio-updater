"""Setup initial or default folio for the application."""

import logging
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db import db, schema_manager
from exporters import ParquetExporter
from mock.mock_data import generate_transactions
from services import ForexService
from utils.backup import rolling_backup
from utils.constants import DEFAULT_TICKERS, Table
from utils.settlement_calculator import settlement_calculator

if TYPE_CHECKING:
    from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


def ensure_data_exists(*, mock: bool = True) -> None:
    """Ensure that the folio exists.

    Checks if the folio exists. If mock is True (default), creates a folio with
    mock data if it does not exist.

    Args:
        mock (bool, optional): Whether to create mock data in the folio.

    Raises:
        FileNotFoundError: If the parent directory does not exist.

    """
    configuration = get_config()
    transaction_data = configuration.txn_parquet
    if transaction_data.exists():
        logger.debug("Transaction data file already exists at %s", transaction_data)
        return
    if not mock:
        msg: str = f'MISSING transaction data: "{transaction_data}"'
        logger.error(msg)
        raise FileNotFoundError(msg)

    folio_path_parent: Path = configuration.folio_path.parent
    default_data_dir: Path = configuration.project_root / "data"

    # Only create data folder in automated fashion
    if folio_path_parent.is_relative_to(default_data_dir):
        folio_path_parent.mkdir(parents=True, exist_ok=True)
    elif not folio_path_parent.exists():
        msg: str = f'MISSING folder: "{folio_path_parent}"'
        logger.error(msg)
        raise FileNotFoundError(msg)

    create_mock_data()


def create_mock_data() -> None:
    """Create default folio with mock data."""
    configuration = get_config()
    transactions_list = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
    transactions_df = pd.concat(transactions_list, ignore_index=True)

    # Only settlement calculation needed, mock data is already clean.
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
    exporter = ParquetExporter()
    exporter.export_all()

    logger.info('CREATED mock data at "%s"', configuration.data_path)
