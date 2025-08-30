"""Setup initial or default folio for the application."""

import logging
from typing import TYPE_CHECKING

import pandas as pd

from db import db
from mock.mock_data import generate_transactions
from utils.config import Config
from utils.constants import DEFAULT_TICKERS, Column, Table

if TYPE_CHECKING:
    from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


def _create_default_folio(configuration: Config) -> None:
    """Create default folio with mock data."""
    tickers_df = pd.DataFrame({Column.Ticker.TICKER: DEFAULT_TICKERS})
    transactions_list = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
    transactions_df = pd.concat(transactions_list, ignore_index=True)

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

    with db.get_connection(configuration) as conn:
        transactions_df.to_sql(Table.TXNS.value, conn, if_exists="replace", index=False)

    logger.info("Created default folio at %s", configuration.folio_path)


def ensure_folio_exists(configuration: Config) -> None:
    """Ensure that the folio exists.

    If the folio does not exist, create a default one with mock data. If the file
    already exists, do nothing.

    Raises:
        FileNotFoundError: If the parent directory does not exist.

    """
    folio_path: Path = configuration.folio_path
    if folio_path.exists():
        logger.debug("Folio file already exists at %s", folio_path)
        return

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
        logger.debug("Folio path is valid: %s", folio_path)  # pragma: no cover

    _create_default_folio(configuration)
