"""
Setup data for the application
"""

import logging
import pandas as pd

logger = logging.getLogger(__name__)

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

    create_default_folio()

def create_default_folio():
    """
    Create a default Excel file with arbitrary tickers.
    Common but varied tickers added to test different data scenarios.
    """
    from src import config
    df = pd.DataFrame({"Ticker": ["SPY", "AAPL"]})
    tickers_sheet = config.SHEETS["tickers"]

    with pd.ExcelWriter(config.FOLIO_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=tickers_sheet)
    logger.info("Created default folio at %s with sheet '%s'", config.FOLIO_PATH, tickers_sheet)