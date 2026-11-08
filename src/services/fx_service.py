"""FX service module.

Handles fetching FX rates from Bank of Canada API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

from db import fx_rates, txns
from domain import TORONTO_TZ, Column
from domain.numeric import dec
from models import FxRate
from term import announce

logger = logging.getLogger(__name__)

# The Bank of Canada's own column names.
BOC_DATE_COLUMN = "date"
BOC_USDCAD_COLUMN = "FXUSDCAD"


class FxService:
    """Service for handling FX rate operations."""

    BOC_BASE_URL = (
        "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/csv"
    )

    @classmethod
    def get_fx_rates_from_boc(cls, start_date: str) -> list[FxRate]:
        """Get FX rates from Bank of Canada web-query.

        Args:
            start_date: Start date in YYYY-MM-DD format.

        Returns:
            The rates published since then, oldest first, none on a failure.
        """
        try:
            url = f"{cls.BOC_BASE_URL}?start_date={start_date}"
            logger.debug("FETCH FX rates from BoC API (start_date=%s)", start_date)

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Parse CSV response: the Bank of Canada format has multiple sections
            csv_text = cls._extract_observations_csv(response.text)
            if not csv_text:  # pragma: no cover
                logger.warning("No observations data found in response")
                return []

            csv_data = StringIO(csv_text)
            raw_df = pd.read_csv(csv_data)
            return cls._process_fx_data(raw_df)

        except requests.RequestException:
            logger.exception("Failed to fetch FX rates from Bank of Canada URL.")
            return []
        except (ValueError, KeyError):
            logger.exception("Error processing FX data from Bank of Canada URL.")
            return []

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
        for i, line in enumerate(lines):
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
            if cleaned_line:
                cleaned_line = cleaned_line.removeprefix("\ufeff")  # Remove BOM
                clean_lines.append(cleaned_line)

        return "\n".join(clean_lines)

    @staticmethod
    def _process_fx_data(raw_df: pd.DataFrame) -> list[FxRate]:
        """Process raw Bank of Canada FX data.

        Args:
            raw_df: Raw DataFrame from Bank of Canada CSV.

        Returns:
            The rates, oldest first.
        """
        if raw_df.empty:  # pragma: no cover
            return []

        # The columns are fixed, so a KeyError here is a format change the
        # caller reports.
        fx_df = raw_df[[BOC_DATE_COLUMN, BOC_USDCAD_COLUMN]].copy()
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
        return [
            FxRate(date=str(day), usd_cad=dec(usd_cad), cad_usd=dec(cad_usd))
            for day, usd_cad, cad_usd in fx_df[
                [Column.FX.DATE, Column.FX.FXUSDCAD, Column.FX.FXCADUSD]
            ].itertuples(index=False, name=None)
        ]

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

    @classmethod
    def _default_start(cls) -> str:
        """Work out how far back coverage has to reach for this folio.

        Returns:
            The earliest transaction date, or 30 days ago for an empty folio.
        """
        earliest = txns.earliest_date()
        if earliest is not None:
            return earliest
        fallback = (datetime.now(TORONTO_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")
        announce.info(f"No transactions found, using {fallback} as FX start date")
        return fallback

    @classmethod
    def ensure_coverage(cls, start: str | None = None, end: str | None = None) -> int:
        """Ensure there is FX coverage for all transactions in the folio.

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
        existing_min = fx_rates.earliest_date()
        existing_max = fx_rates.latest_date()
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
        # Drop dates that we already have, so we only insert new ones.
        held = {rate.date for rate in fx_rates.read()}
        fresh = [rate for rate in fetched if rate.date not in held]
        if not fresh:
            return 0
        written = fx_rates.write(fresh)
        logger.debug("Inserted %d FX records into database", written)
        return written
