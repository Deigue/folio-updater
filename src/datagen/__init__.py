"""Data generation module for folio-updater.

This module exports utilities for creating mock/test data.
"""

from datagen.folio_setup import create_mock_data, ensure_data_exists
from datagen.mock_data import (
    DEFAULT_TXN_COUNT,
    generate_transactions,
    get_mock_data_date_range,
)

__all__ = [
    "DEFAULT_TXN_COUNT",
    "create_mock_data",
    "ensure_data_exists",
    "generate_transactions",
    "get_mock_data_date_range",
]
