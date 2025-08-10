import pandas as pd
from src import config

def ensure_folio_exists():
    folio_path = config.FOLIO_PATH
    if folio_path.exists():
        print(f"[INFO] Folio file already exists at {folio_path}.")
        return

    parent_dir = folio_path.parent
    expected_data_dir = config.PROJECT_ROOT / "data"

    # Only create data folder in automated fashion
    if parent_dir.resolve() == expected_data_dir.resolve():
        expected_data_dir.mkdir(parents=True, exist_ok=True)
    elif not parent_dir.exists():
        raise FileNotFoundError(
            f"[ERROR] The folder '{parent_dir}' does not exist. "
            f"Please create it before running."
        )

    create_default_folio()

def create_default_folio():
    """
    Create a default Excel file with arbitrary tickers.
    Common but varied tickers added to test different data scenarios.
    """
    df = pd.DataFrame({"Ticker": ["SPY", "AAPL"]})
    tickers_sheet = config.SHEETS["tickers"]

    with pd.ExcelWriter(config.FOLIO_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=tickers_sheet)