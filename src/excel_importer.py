"""
Ingest Excel data into the application context.
"""
import pandas as pd
import logging
from . import db, schema
from src.constants import TXN_ESSENTIALS

logger = logging.getLogger(__name__)

def import_transactions(folio_path):
    """
    Imports transactions from Excel files. Maps headers to internal fields.
    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.
    """
    raise NotImplementedError("TODO impl import txns")
