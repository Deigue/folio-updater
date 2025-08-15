"""
DB Helper functions
"""


import sqlite3
from src import config

DB_PATH = config.PROJECT_ROOT / "data" / "folio.db"

def get_connection():
    """Return sqlite3.Connection. Ensure parent data folder exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)

def get_columns(connection, table_name: str):
    """Return list of column names for a table (in defined order)."""
    cursor = connection.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]