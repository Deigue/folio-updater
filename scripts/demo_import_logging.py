"""Demo script to demonstrate the import logging functionality."""

import logging
import tempfile
from pathlib import Path

import pandas as pd

from app.app_context import get_config, initialize_app
from importers.excel_importer import import_transactions
from utils.logging_setup import init_logging


def create_sample_excel(file_path: Path) -> None:
    """Create a sample Excel file with transaction data."""
    # Sample data with some duplicates
    data = {
        "Transaction Date": [
            "2024-01-01",
            "2024-01-02",
            "2024-01-01",  # Duplicate within file
            "2024-01-03",
            "2024-01-04",
        ],
        "Action": ["BUY", "SELL", "BUY", "BUY", "SELL"],
        "Amount": [1000.0, 500.0, 1000.0, 750.0, 250.0],  # Same amount for duplicate
        "Currency": ["USD", "USD", "USD", "USD", "USD"],
        "Price": [100.0, 50.0, 100.0, 75.0, 25.0],  # Same price for duplicate
        "Units": [10.0, 10.0, 10.0, 10.0, 10.0],  # Same units for duplicate
        "Ticker": [
            "AAPL",
            "MSFT",
            "AAPL",
            "GOOGL",
            "TSLA",
        ],  # Same ticker for duplicate
        "Account": [
            "DEMO-ACCOUNT",
            "DEMO-ACCOUNT",
            "DEMO-ACCOUNT",
            "DEMO-ACCOUNT",
            "DEMO-ACCOUNT",
        ],
    }

    df = pd.DataFrame(data)

    # Create Excel file with the required sheet
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Txns", index=False)

    print(f"Created sample Excel file: {file_path}")
    print(f"Data includes {len(df)} transactions with 1 intra-file duplicate")


def demo_import_logging() -> None:
    """Demonstrate the import logging functionality."""
    print("=== Import Logging Demo ===\n")

    # Initialize application context and logging
    initialize_app()
    init_logging(level=logging.DEBUG)  # Enable DEBUG to see duplicate details

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        temp_path = Path(tmp.name)

    try:
        # Create sample data
        create_sample_excel(temp_path)
        config = get_config()
        txn_sheet = config.transactions_sheet()

        print("\n--- First Import (should import 4 unique transactions) ---")
        result1 = import_transactions(temp_path, None, txn_sheet)
        print(f"First import result: {result1} transactions")

        print("\n--- Second Import (should find duplicates) ---")
        result2 = import_transactions(temp_path, None, txn_sheet)
        print(f"Second import result: {result2} transactions")

        print("\n--- Demo completed ---")
        print("Check the following log files for details:")
        print("- logs/folio.log (general application logs)")
        print("- logs/importer.log (detailed import logs)")
        print("\nThe importer.log will contain:")
        print("- High-level import summaries")
        print("- Detailed duplicate information")
        print("- Summaries for all transactions as they are processed")

    except (FileNotFoundError, PermissionError, ValueError, ImportError) as e:
        print(f"Demo failed: {e}")

    finally:
        # Clean up
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    demo_import_logging()
