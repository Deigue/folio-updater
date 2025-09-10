"""Simple script to import transactions from a generic Excel file."""

from pathlib import Path

from app import bootstrap
from importers.excel_importer import import_transactions

bootstrap.reload_config()
import_path = input("Enter the path to the Excel file to import: ").strip()

if Path(import_path).exists():
    import_file = Path(import_path)
    import_transactions(import_file, None, None)
else:
    print(f"File not found: {import_path}")
