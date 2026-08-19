"""Forex service module.

Handles fetching FX rates from Bank of Canada API.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

from app import get_config
from db import (
    create_fx_table,
    get_connection,
    get_max_value,
    get_min_value,
    get_rows,
    get_tables,
)
from utils import TORONTO_TZ, Column, Table, info_both
from utils.backup import rolling_backup

logger = logging.getLogger(__name__)


class ForexService:
    """Service for handling FX rate operations."""

    BOC_BASE_URL = (
        "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/csv"
    )

    @staticmethod
    def get_latest_fx_date_from_db() -> str | None:
        """Get the latest FX rate date from the database.

        Returns:
            Latest FX rate date in YYYY-MM-DD format, or None if no rates exist.
        """
        try:
            with get_connection() as conn:
                tables = get_tables(conn)
                if Table.FX not in tables:
                    logger.debug("FX table does not exist")
                    return None

                result = get_max_value(conn, Table.FX, Column.FX.DATE)
                if result:
                    logger.debug("Latest FX date in database: %s", result)
                    return result
                logger.debug("No FX rates found in database")  # pragma: no cover
                return None  # pragma: no cover
        except sqlite3.Error as e:
            logger.debug("Could not get latest FX date from database: %s", e)
            return None

    @staticmethod
    def get_fx_rates_from_db(start_date: str | None = None) -> pd.DataFrame:
        """Get FX rates from the database.

        Args:
            start_date: Optional start date to filter from (YYYY-MM-DD format).
                       If None, returns all rates.

        Returns:
            DataFrame with FX rates from database.
        """
        try:
            with get_connection() as conn:
                tables = get_tables(conn)
                if Table.FX not in tables:  # pragma: no cover
                    logger.debug("FX table does not exist")
                    return pd.DataFrame()

                where = None
                params = None
                if start_date:  # pragma: no cover
                    where = f'"{Column.FX.DATE}" >= ?'
                    params = [start_date]
                df = get_rows(
                    conn,
                    Table.FX,
                    where=where,
                    params=params,
                    order_by=Column.FX.DATE,
                )

                logger.debug("Retrieved %d FX rates from database", len(df))
                return df

        except sqlite3.Error as e:
            logger.debug("Could not get FX rates from database: %s", e)
            return pd.DataFrame()

    @classmethod
    def get_fx_rates_from_boc(cls, start_date: str) -> pd.DataFrame:
        """Get FX rates from Bank of Canada web-query.

        Args:
            start_date: Start date in YYYY-MM-DD format.

        Returns:
            DataFrame with Date, FXUSDCAD, and FXCADUSD columns.
        """
        try:
            url = f"{cls.BOC_BASE_URL}?start_date={start_date}"
            logger.debug("FETCH FX rates from BoC API (start_date=%s)", start_date)

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Parse CSV response - Bank of Canada format has multiple sections
            csv_text = cls._extract_observations_csv(response.text)
            if not csv_text:  # pragma: no cover
                logger.warning("No observations data found in response")
                return pd.DataFrame()

            csv_data = StringIO(csv_text)
            raw_df = pd.read_csv(csv_data)
            return cls._process_fx_data(raw_df)

        except requests.RequestException:
            logger.exception("Failed to fetch FX rates from Bank of Canada URL.")
            return pd.DataFrame()
        except (ValueError, KeyError):
            logger.exception("Error processing FX data from Bank of Canada URL.")
            return pd.DataFrame()

    @staticmethod
    def insert_fx_data(fx_df: pd.DataFrame) -> int:
        """Insert FX data into the database.

        Args:
            fx_df: Prepared FX DataFrame to insert.

        Returns:
            Number of rows inserted.
        """
        if fx_df.empty:  # pragma: no cover
            return 0

        rolling_backup(get_config().db_path)
        with get_connection() as conn:
            if get_tables(conn).count(Table.FX) == 0:
                create_fx_table()
            rows_inserted = fx_df.to_sql(
                Table.FX,
                conn,
                if_exists="append",
                index=False,
                method="multi",
            )

        logger.debug("Inserted %d FX records into database", len(fx_df))
        return len(fx_df) if rows_inserted is None else rows_inserted

    @staticmethod
    def _extract_observations_csv(response_text: str) -> str:
        """Extract the observations section from Bank of Canada CSV response.

        The Bank of Canada CSV has multiple sections:
        - TERMS AND CONDITIONS
        - SERIES
        - OBSERVATIONS (this is what we want)

        Args:
            response_text: Raw CSV response text from Bank of Canada API.

        Returns:
            CSV string containing only the observations section.
        """
        lines = response_text.split("\n")

        # Find the start of observations section
        observations_start = None
        for i, line in enumerate(lines):  # pragma: no break
            if '"OBSERVATIONS"' in line:
                observations_start = i + 1  # Header is the next line
                break

        if observations_start is None:  # pragma: no cover
            logger.warning("Could not find OBSERVATIONS section in response")
            return ""

        # Get all lines from header onwards
        observations_lines = lines[observations_start:]

        # Clean lines: remove empty lines, carriage returns, and BOM
        clean_lines = []
        for raw_line in observations_lines:
            cleaned_line = raw_line.strip()
            if cleaned_line and cleaned_line != "\r":
                cleaned_line = cleaned_line.removeprefix("\ufeff")  # Remove BOM
                clean_lines.append(cleaned_line)

        return "\n".join(clean_lines)

    @staticmethod
    def _process_fx_data(raw_df: pd.DataFrame) -> pd.DataFrame:
        """Process raw Bank of Canada FX data.

        Args:
            raw_df: Raw DataFrame from Bank of Canada CSV.

        Returns:
            Processed DataFrame with required FX columns.
        """
        if raw_df.empty:  # pragma: no cover
            return pd.DataFrame()
        date_col = None
        usdcad_col = None

        for col in raw_df.columns:  # pragma: no cover
            if col.lower() in ["date", "datetime", "time"]:
                date_col = col
                break  # pragma: no cover

        for col in raw_df.columns:  # pragma: no cover
            if "usdcad" in col.lower() or "fxusdcad" in col.lower():
                usdcad_col = col
                break  # pragma: no cover

        if date_col is None or usdcad_col is None:  # pragma: no cover
            logger.error("Could not find required columns in BoC FX data")
            logger.debug("Available columns: %s", list(raw_df.columns))
            return pd.DataFrame()

        fx_df = raw_df[[date_col, usdcad_col]].copy()
        fx_df = fx_df.dropna()
        fx_df.columns = [Column.FX.DATE, Column.FX.FXUSDCAD]

        # Ensure date format is YYYY-MM-DD
        fx_df[Column.FX.DATE] = pd.to_datetime(
            fx_df[Column.FX.DATE],
        ).dt.strftime("%Y-%m-%d")

        # Calculate inverse rate (CAD to USD)
        fx_df[Column.FX.FXCADUSD] = 1.0 / fx_df[Column.FX.FXUSDCAD].astype(
            float,
        )

        fx_df[Column.FX.FXUSDCAD] = fx_df[Column.FX.FXUSDCAD].round(10)
        fx_df[Column.FX.FXCADUSD] = fx_df[Column.FX.FXCADUSD].round(10)

        fx_df = fx_df.sort_values(Column.FX.DATE).reset_index(drop=True)

        logger.debug("Processed %d FX records from API", len(fx_df))
        return fx_df

    @staticmethod
    def get_earliest_transaction_date() -> str | None:
        """Get the earliest transaction date from the database.

        Returns:
            Earliest transaction date in YYYY-MM-DD format, or None if no transactions.
        """
        try:
            with get_connection() as conn:
                tables = get_tables(conn)
                if Table.TXNS not in tables:
                    logger.debug("Txns table does not exist")
                    return None

                result = get_min_value(conn, Table.TXNS, Column.Txn.TXN_DATE)
                if result:
                    logger.debug("Earliest transaction date: %s", result)
                    return result
                logger.debug("No transactions found in database")
                return None
        except sqlite3.Error as e:
            logger.debug("Could not get earliest transaction date: %s", e)
            return None

    @staticmethod
    def effective_today() -> str:
        """Give the latest date the Bank of Canada could already have published.

        Rates land at 4:30 PM Toronto time, so before that the newest date worth
        asking for is yesterday's.

        Returns:
            Date in YYYY-MM-DD format.
        """
        current = datetime.now(TORONTO_TZ)
        if (
            current.time()
            < current.replace(hour=16, minute=30, second=0, microsecond=0).time()
        ):  # pragma: no cover
            current = current - timedelta(days=1)
        return current.strftime("%Y-%m-%d")

    @staticmethod
    def get_earliest_fx_date_from_db() -> str | None:
        """Get the earliest FX rate date held in the database.

        Returns:
            Earliest FX rate date in YYYY-MM-DD format, or None if none exist.
        """
        try:
            with get_connection() as conn:
                if Table.FX not in get_tables(conn):
                    return None
                return get_min_value(conn, Table.FX, Column.FX.DATE)
        except sqlite3.Error as e:
            logger.debug("Could not get earliest FX date from database: %s", e)
            return None

    @classmethod
    def _default_start(cls) -> str:
        """Work out how far back coverage has to reach for this folio.

        Returns:
            The earliest transaction date, or 30 days ago for an empty folio.
        """
        earliest = cls.get_earliest_transaction_date()
        if earliest is not None:
            return earliest
        fallback = (datetime.now(TORONTO_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")
        info_both(f"No transactions found, using {fallback} as FX start date")
        return fallback

    @classmethod
    def ensure_coverage(cls, start: str | None = None, end: str | None = None) -> int:
        """Ensure there is forex coverage for all transactions in the folio.

        This refetches from `start` whenever `start` predates what is on hand,
        and otherwise tops up the forward end.

        Args:
            start: Earliest date that needs a rate, YYYY-MM-DD. Defaults to the
                folio's own earliest transaction date
            end: Latest date that needs a rate, YYYY-MM-DD. (capped to latest available)

        Returns:
            Number of new rate rows inserted.
        """
        first_needed = start or cls._default_start()
        existing_min = cls.get_earliest_fx_date_from_db()
        existing_max = cls.get_latest_fx_date_from_db()
        today = cls.effective_today()  # latest available boc date
        target_end = min(end, today) if end else today

        if existing_min is None or existing_max is None or first_needed < existing_min:
            fetch_from = first_needed
        elif existing_max < target_end:
            fetch_from = (pd.to_datetime(existing_max) + pd.Timedelta(days=1)).strftime(
                "%Y-%m-%d",
            )
        else:
            logger.debug("FX coverage already spans %s to %s", first_needed, target_end)
            return 0

        fetched = cls.get_fx_rates_from_boc(fetch_from)
        if fetched.empty:
            return 0

        # Drop dates that we already have, so we only insert new ones.
        held: set[str] = set()
        if existing_min is not None:
            held = set(cls.get_fx_rates_from_db()[Column.FX.DATE])
        fresh = fetched[~fetched[Column.FX.DATE].isin(held)]
        if fresh.empty:
            return 0
        return cls.insert_fx_data(fresh)
