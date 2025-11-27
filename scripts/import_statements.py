"""Simple script to import transactions from a generic Excel file."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from app import bootstrap
from importers import import_statements

bootstrap.reload_config()

# Use tkinter to open a native file picker
root = tk.Tk()
root.withdraw()  # Hide the main window
import_path = filedialog.askopenfilename(
    title="Select Statement file to import",
    filetypes=[("Statement files", "*.xlsx *.xls *.csv")],
)

if import_path and Path(import_path).exists():
    import_file = Path(import_path)
    num_txns = import_statements(import_file)
    print(f"Updated {num_txns} transactions from {import_file}.")
else:
    print("No file selected or file not found.")
