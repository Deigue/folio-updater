"""Forex exporter module.

Handles exporting FX rates from database to Excel.
"""

from __future__ import annotations

import logging

import pandas as pd
from openpyxl import load_workbook

from app.app_context import get_config
from services.forex_service import ForexService
from utils.backup import rolling_backup
from utils.constants import Column

logger = logging.getLogger(__name__)


class ForexExporter:
    """Handles exporting FX rates from database to Excel."""

    def __init__(self) -> None:
        """Initialize the forex exporter."""
        config = get_config()
        self.folio_path = config.folio_path
        self.forex_sheet = config.forex_sheet()

    def export_full(self, start_date: str | None = None) -> int:
        """Perform a full export of FX rates to Excel.

        This method:
        1. Checks database for existing FX rates
        2. Fetches missing rates from API if needed
        3. Stores new rates in database
        4. Exports all rates from database to Excel

        Args:
            start_date: Start date in YYYY-MM-DD format. If None, uses earliest
                       transaction date from database.

        Returns:
            int: Number of FX rate records exported.
        """
        self._ensure_fx_data_current(start_date)
        fx_df = ForexService.get_fx_rates_from_db()
        if fx_df.empty:  # pragma: no cover
            logger.warning("No FX rates available for export")
            return 0

        fx_count = len(fx_df)
        if self.folio_path.exists():
            mode = "a"
            sheet_exists = "replace"
            rolling_backup(self.folio_path)
            self._check_file_access()
        else:
            mode = "w"
            sheet_exists = None

        with pd.ExcelWriter(
            self.folio_path,
            engine="openpyxl",
            mode=mode,
            if_sheet_exists=sheet_exists,
        ) as writer:
            fx_df.to_excel(
                writer,
                sheet_name=self.forex_sheet,
                index=False,
            )

        logger.info("=" * 60)
        logger.info("Full export completed: %d fx rates exported", fx_count)
        logger.info("=" * 60)

        return fx_count

    def export_update(self) -> int:
        """Perform an incremental export of new FX rates to Excel.

        This method:
        1. Checks database for missing FX rates (up to today)
        2. Fetches and stores any missing rates
        3. Appends only new rates to Excel. If sheet doesn't exist or is empty, then
        performs full export from database.

        Returns:
            int: Number of new FX rate records exported.

        Raises:
            FileNotFoundError: If the Excel file doesn't exist.
            ValueError: If there's an issue with reading the sheet.
        """
        if not self.folio_path.exists():  # pragma: no cover
            msg = f"Excel file not found: {self.folio_path}"
            logger.error(msg)
            raise FileNotFoundError(msg)

        try:
            excel_df = pd.read_excel(
                self.folio_path,
                sheet_name=self.forex_sheet,
                engine="openpyxl",
            )
        except ValueError as e:  # pragma: no cover
            # Sheet might not exist yet, perform full export
            if "Worksheet named" in str(e) and "not found" in str(e):
                logger.info("FX sheet doesn't exist, performing full export")
                return self.export_full()
            msg = f"Error reading sheet '{self.forex_sheet}': {e}"
            logger.exception(msg)
            raise ValueError(msg) from e

        if excel_df.empty:  # pragma: no cover
            logger.info("FX sheet is empty, performing full export")
            return self.export_full()

        latest_excel_date = excel_df[Column.FX.DATE].max()
        logger.debug("Latest FX date in Excel: %s", latest_excel_date)

        self._ensure_fx_data_current()

        # Get new rates from database that are after the latest Excel date
        next_date = pd.to_datetime(latest_excel_date) + pd.Timedelta(days=1)
        start_date = next_date.strftime("%Y-%m-%d")

        new_fx_df = ForexService.get_fx_rates_from_db(start_date)
        if new_fx_df.empty:  # pragma: no cover
            logger.info("No new FX rates to export")
            return 0

        fx_count = len(new_fx_df)
        logger.info("Found %d new FX rates to export", fx_count)
        rolling_backup(self.folio_path)
        self._check_file_access()
        workbook = load_workbook(self.folio_path)
        worksheet = workbook[self.forex_sheet]

        for _, row in new_fx_df.iterrows():
            worksheet.append(row.tolist())

        workbook.save(self.folio_path)
        workbook.close()

        logger.info("=" * 60)
        logger.info("Update export completed: %d new FX rates exported", fx_count)
        logger.info("=" * 60)

        return fx_count

    def _ensure_fx_data_current(self, start_date: str | None = None) -> None:
        """Ensure FX data in database is current.

        Args:
            start_date: Optional start date for initial data fetch.
        """
        if ForexService.is_fx_data_current():  # pragma: no cover
            return
        fx_df = ForexService.get_missing_fx_data(start_date)
        if fx_df.empty:
            return

        ForexService.insert_fx_data(fx_df)

    def _check_file_access(self) -> None:
        """Raise an exception if the file is not accessible for reading and writing."""
        try:
            with self.folio_path.open("rb+"):
                return
        except PermissionError as e:  # pragma: no cover
            msg = (
                f"File '{self.folio_path}' is not accessible for "
                f"reading and writing: {e}"
            )
            logger.exception(msg)
            raise
