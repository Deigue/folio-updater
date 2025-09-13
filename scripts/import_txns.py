"""Simple script to import transactions from a generic Excel file."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from app import bootstrap
from importers.excel_importer import import_transactions

bootstrap.reload_config()

# Use tkinter to open a native file picker
root = tk.Tk()
root.withdraw()  # Hide the main window
import_path = filedialog.askopenfilename(
    title="Select Folio file to import",
    filetypes=[("Folio files", "*.xlsx *.xls *.csv")],
)

if import_path and Path(import_path).exists():
    import_file = Path(import_path)
    num_txns = import_transactions(import_file, None, None)
    print(f"Imported {num_txns} transactions from {import_file}.")
else:
    print("No file selected or file not found.")
