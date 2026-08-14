"""Tests for parsing the Bank of Canada FX response."""

# ERA001: BOC refresh instructions below deliberate runnable command
# ruff: noqa: ERA001

from __future__ import annotations

from unittest.mock import patch

import pytest

from services import ForexService
from utils.constants import Column

# * The rest of the suite runs with `get_fx_rates_from_boc` stubbed out; this
# * module is testing that very method, so it opts out and mocks `requests`.
pytestmark = pytest.mark.no_mock_forex

# ---------------------------------------------------------------------------
# CANNED BANK OF CANADA RESPONSE -- Update if changed.
#
# Capture a fresh copy with:
#
#   uv run python -c "import requests; print(requests.get(
#     'https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/csv'
#     '?start_date=2025-08-01', timeout=30).text)"
#
# Keep the awkward data if available from response for testing.
# Sample below covers sibling currency columns (so column detection has to
# actually choose), a BOM on the header, a holiday row with a blank rate, and
# rows out of date order.
# ---------------------------------------------------------------------------
BOC_CSV_RESPONSE = (
    '"TERMS AND CONDITIONS"\n'
    '"https://www.bankofcanada.ca/terms/"\n'
    "\n"
    '"SERIES"\n'
    '"id","label","description"\n'
    '"FXAUDCAD","AUD/CAD","Australian dollar to Canadian dollar daily rate"\n'
    '"FXUSDCAD","USD/CAD","US dollar to Canadian dollar daily rate"\n'
    "\n"
    '"OBSERVATIONS"\n'
    '﻿"date","FXAUDCAD","FXUSDCAD"\n'
    '"2025-08-06","0.8934","1.3702"\r\n'
    '"2025-08-01","0.8912","1.3750"\n'
    '"2025-08-04","0.8905",""\n'
    '"2025-08-05","0.8921","1.3785"\n'
)

# Blank-rate 2025-08-04 is dropped; the rest come back in date order.
EXPECTED_DATES = ["2025-08-01", "2025-08-05", "2025-08-06"]
EXPECTED_USDCAD = [1.3750, 1.3785, 1.3702]


def test_boc_response_is_parsed_into_fx_rates() -> None:
    """A Bank of Canada CSV becomes a sorted, inverted, gap-free FX frame."""
    with patch("services.forex_service.requests.get") as mock_get:
        mock_get.return_value.text = BOC_CSV_RESPONSE
        fx_df = ForexService.get_fx_rates_from_boc("2025-08-01")

    # The requested start date is threaded into the query string.
    requested_url = mock_get.call_args.args[0]
    assert requested_url.endswith("?start_date=2025-08-01")
    assert mock_get.call_args.kwargs["timeout"] == 30

    assert list(fx_df.columns) == [
        Column.FX.DATE,
        Column.FX.FXUSDCAD,
        Column.FX.FXCADUSD,
    ]
    assert list(fx_df[Column.FX.DATE]) == EXPECTED_DATES
    assert list(fx_df[Column.FX.FXUSDCAD]) == EXPECTED_USDCAD

    # The CAD->USD leg is the inverse, rounded the same way as the raw rate.
    assert list(fx_df[Column.FX.FXCADUSD]) == [
        round(1.0 / rate, 10) for rate in EXPECTED_USDCAD
    ]


def test_missing_observations_section_yields_empty_frame() -> None:
    """A response without an OBSERVATIONS block is discarded, not guessed at."""
    with patch("services.forex_service.requests.get") as mock_get:
        mock_get.return_value.text = '"TERMS AND CONDITIONS"\n"nothing useful"\n'
        fx_df = ForexService.get_fx_rates_from_boc("2025-08-01")

    assert fx_df.empty
