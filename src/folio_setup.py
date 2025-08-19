"""
Setup initial or default folio for the application.
"""


import logging
import pandas as pd

from src.constants import DEFAULT_TICKERS
from src.mock_data import generate_transactions
from src import db

logger = logging.getLogger(__name__)

def _create_default_folio():
    """
    Create a default Excel file with arbitrary tickers.
    Common but varied tickers added to test different data scenarios.
    """
    from src import config
    df_tickers = pd.DataFrame({"Ticker": DEFAULT_TICKERS})
    tickers_sheet = config.tickers_sheet()

    txns_list = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
    df_txns = pd.concat(txns_list, ignore_index=True)

    with pd.ExcelWriter(config.FOLIO_PATH, engine="openpyxl") as writer:
        df_tickers.to_excel(writer, index=False, sheet_name=tickers_sheet)
        df_txns.to_excel(writer, index=False, sheet_name="Txns")

    with db.get_connection() as conn:
        df_txns.to_sql("Txns", conn, if_exists="replace", index=False)

    logger.info("Created default folio at %s", config.FOLIO_PATH)

def ensure_folio_exists():
    from src import config
    folio_path = config.FOLIO_PATH
    if folio_path.exists():
        logger.debug(f"Folio file already exists at {folio_path}.")
        return

    parent_dir = folio_path.parent
    expected_data_dir = config.PROJECT_ROOT / "data"

    # Only create data folder in automated fashion
    if parent_dir.resolve() == expected_data_dir.resolve():
        expected_data_dir.mkdir(parents=True, exist_ok=True)
    elif not parent_dir.exists():
        msg = f"The folder '{parent_dir}' does not exist. Please create it before running."
        logger.error(msg)
        raise FileNotFoundError(msg)
    else:
        logger.debug(f"Folio path is valid: {folio_path}") # pragma: no cover

    _create_default_folio()
