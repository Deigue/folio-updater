"""Excel exporter module.

Handles export tasks from the internal database to Excel files.
"""

import logging
from app.app_context import get_config
logger = logging.getLogger(__name__)
class TransactionExporter:
    """Handles exporting transactions from SQLite database to Excel sheets."""

    def __init__(self, folio_path: Path | None = None) -> None:
        """Initialize the transaction exporter.

        Args:
            folio_path: Optional path to the Excel file. If None, uses config.
        """
        config = get_config()
        self.folio_path = folio_path or config.folio_path
        self.sheet_name = config.transactions_sheet()

