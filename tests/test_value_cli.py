"""Tests for `folio value`: every account's market value on a past date."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cli.commands import value as value_command
from cli.main import app
from db import historical_prices
from models import HistoricalPrice, HistoricalPriceCoverage
from services.price_history import PricePoint

from .helpers.cli import assert_in_output, run_cli
from .helpers.seed import seed_cash, seed_fx, seed_transaction

if TYPE_CHECKING:
    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.context import TempContext

# One flat rate across the month these tests trade and value in.
FX = {f"2025-09-{day:02d}": "1.4000" for day in range(1, 31)}


def _seed_three_accounts() -> None:
    """Two TFSAs and a margin account, one holding a USD security.

    WS-TFSA: 4,500 cash and 10 RY.TO. QT-TFSA: 1,000 cash. IBKR-MARGIN: 400 USD
    cash and 10 SPY.
    """
    seed_fx(FX)
    seed_cash("CONTRIBUTION", amount="5000", account="WS-TFSA", date="2025-09-02")
    seed_transaction(
        ticker="RY.TO",
        date="2025-09-03",
        account="WS-TFSA",
        currency="CAD",
        amount="-500",
        price="50",
        units="10",
    )
    seed_cash("CONTRIBUTION", amount="1000", account="QT-TFSA", date="2025-09-02")
    seed_cash(
        "CONTRIBUTION",
        amount="1000",
        currency="USD",
        account="IBKR-MARGIN",
        date="2025-09-02",
    )
    seed_transaction(
        ticker="SPY",
        date="2025-09-03",
        account="IBKR-MARGIN",
        amount="-600",
        price="60",
        units="10",
    )


def _table_rows(output: str) -> dict[str, list[str]]:
    """Read each table row's cells, keyed by its first."""
    rows: dict[str, list[str]] = {}
    for line in output.splitlines():
        cells = [cell for cell in re.split(r"\s*[│|]\s*|\s{2,}", line.strip()) if cell]
        if len(cells) > 2:
            rows[cells[0]] = cells[1:]
    return rows


def _store(symbol: str, day: str, price: str) -> None:
    """Store one close, the symbol covered through September."""
    historical_prices.write(
        [HistoricalPrice(symbol=symbol, date=day, price=Decimal(price))],
        [
            HistoricalPriceCoverage(
                symbol=symbol,
                fetched_from="2025-09-01",
                fetched_through="2025-09-30",
            ),
        ],
    )


def test_a_month_values_each_account_and_the_folio_at_month_end(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.historical = {
        "RY.TO": [PricePoint("2025-09-30", Decimal(55))],
        "SPY": [PricePoint("2025-09-30", Decimal(62))],
    }

    with temp_ctx():
        _seed_three_accounts()

        result = run_cli(app, ["value", "2025-09"])

    assert result.exit_code == 0
    assert_in_output("Market value at 2025-09-30", result)
    lines = _table_rows(result.plain_output)
    # Holdings, cash and value: RY.TO at 55, SPY at 62 USD, USD at 1.4.
    assert lines["WS-TFSA"] == ["TFSA", "550.00", "4,500.00", "5,050.00"]
    assert lines["QT-TFSA"] == ["TFSA", "0.00", "1,000.00", "1,000.00"]
    assert lines["IBKR-MARGIN"] == ["NREG", "868.00", "560.00", "1,428.00"]
    assert lines["Total"] == ["1,418.00", "6,060.00", "7,478.00"]
    assert "TFSA" not in lines  # accounts only: no subtotal per type


def test_offline_values_from_the_prices_stored_without_fetching(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        _seed_three_accounts()
        _store("RY.TO", "2025-09-30", "55")
        _store("SPY", "2025-09-30", "62")

        result = run_cli(app, ["value", "2025-09-30", "--offline"])

    assert result.exit_code == 0
    assert not quote_provider.asked("historical_prices")
    assert_in_output("7,478.00", result)


def test_a_holding_with_no_price_is_named_and_left_out(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_three_accounts()
        _store("RY.TO", "2025-09-30", "55")

        result = run_cli(app, ["value", "2025-09", "--offline"])

    assert result.exit_code == 0
    assert_in_output("SPY in IBKR-MARGIN", result)
    assert_in_output("6,610.00", result)  # the folio's value without SPY's 868


def test_the_current_month_values_at_the_latest_stored_close(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(value_command, "today_in_toronto", lambda: date(2025, 9, 17))

    with temp_ctx():
        _seed_three_accounts()
        _store("RY.TO", "2025-09-16", "55")
        _store("SPY", "2025-09-16", "62")

        result = run_cli(app, ["value", "2025-09", "--offline"])

    assert result.exit_code == 0
    assert_in_output("Market value at 2025-09-16 (latest close)", result)
    assert_in_output("7,478.00", result)


@pytest.mark.parametrize(
    ("asked", "reason"),
    [
        ("2025-13", "Use YYYY-MM or YYYY-MM-DD"),
        ("September", "Use YYYY-MM or YYYY-MM-DD"),
        ("2025-09-18", "2025-09-18 has not closed yet"),
        ("2025-10", "2025-10 has not closed yet"),
    ],
    ids=["no-such-month", "not-a-date", "future-day", "future-month"],
)
def test_a_date_that_cannot_be_valued_is_refused(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    asked: str,
    reason: str,
) -> None:
    monkeypatch.setattr(value_command, "today_in_toronto", lambda: date(2025, 9, 17))

    with temp_ctx():
        _seed_three_accounts()

        result = run_cli(app, ["value", asked, "--offline"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert_in_output(reason, result)


def test_a_day_before_the_first_transaction_says_nothing_was_held(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_three_accounts()

        result = run_cli(app, ["value", "2025-08", "--offline"])

    assert result.exit_code == 0
    assert_in_output("The folio held nothing on 2025-08-29", result)


def _seed_priced() -> None:
    """Seed the three accounts, with RY.TO at 55 and SPY at 62 USD on 2025-09-30."""
    _seed_three_accounts()
    _store("RY.TO", "2025-09-30", "55")
    _store("SPY", "2025-09-30", "62")


def test_a_range_lists_each_month_end_with_a_dash_before_an_account_opened(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-08", "2025-09", "--offline"])

    assert result.exit_code == 0
    lines = _table_rows(result.plain_output)
    assert lines["Month"] == ["IBKR-MARGIN", "QT-TFSA", "WS-TFSA", "Total"]
    assert lines["2025-08"] == ["-", "-", "-", "-"]
    assert lines["2025-09"] == ["1,428.00", "1,000.00", "5,050.00", "7,478.00"]


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (["2025-08", "2025-09", "-c", "native"], "native needs a single date"),
        (["2025-08-29", "2025-09"], "A range takes two months"),
        (["2025-09", "2025-08"], "2025-09 comes after 2025-08"),
        (["2025-09", "-b", "-t", "tfsa"], "Use --by-type on its own"),
    ],
    ids=["native-range", "range-from-a-day", "backward-range", "by-type-narrowed"],
)
def test_a_request_that_cannot_be_shown_is_refused(
    temp_ctx: TempContext,
    args: list[str],
    reason: str,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", *args, "--offline"])

    assert result.exit_code == 1
    assert_in_output(reason, result)


def test_a_type_lists_only_its_accounts_and_totals_its_pool(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "-t", "tfsa", "--offline"])

    lines = _table_rows(result.plain_output)
    assert set(lines) == {"Account", "QT-TFSA", "WS-TFSA", "Total"}
    assert lines["Total"] == ["550.00", "5,500.00", "6,050.00"]


def test_one_account_in_any_case_lists_and_totals_only_itself(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "-a", "ws-tfsa", "--offline"])

    lines = _table_rows(result.plain_output)
    assert set(lines) == {"Account", "WS-TFSA", "Total"}
    assert lines["Total"] == ["550.00", "4,500.00", "5,050.00"]


def test_an_account_with_nothing_by_the_date_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "-a", "QT-RRSP", "--offline"])

    assert result.exit_code == 0
    assert_in_output("QT-RRSP held nothing on 2025-09-30", result)


def test_by_type_lists_each_account_types_pool(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "-b", "--offline"])

    lines = _table_rows(result.plain_output)
    assert lines["NREG"] == ["868.00", "560.00", "1,428.00"]
    assert lines["TFSA"] == ["550.00", "5,500.00", "6,050.00"]
    assert lines["Total"] == ["1,418.00", "6,060.00", "7,478.00"]


def test_native_keeps_each_currency_line_apart_with_no_grand_total(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "-c", "native", "--offline"])

    lines = _table_rows(result.plain_output)
    assert lines["IBKR-MARGIN"] == ["NREG", "USD", "620.00", "400.00", "1,020.00"]
    assert lines["Total"] == ["CAD", "550.00", "5,500.00", "6,050.00"]
    assert lines["USD"] == ["620.00", "400.00", "1,020.00"]  # the total's USD line


def test_one_holding_lists_its_units_and_value_per_account(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09-30", "spy", "--offline"])

    lines = _table_rows(result.plain_output)
    assert lines["IBKR-MARGIN"][0:2] == ["NREG", "10"]
    assert lines["IBKR-MARGIN"][-1] == "868.00"
    assert lines["Total"][0] == "10"
    assert lines["Total"][-1] == "868.00"


def test_a_holding_not_held_on_the_date_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_priced()

        result = run_cli(app, ["value", "2025-09", "MSFT", "--offline"])

    assert result.exit_code == 0
    assert_in_output("The folio held no MSFT on 2025-09-30", result)


def test_an_account_emptied_by_the_date_is_left_out(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_priced()
        seed_cash("CONTRIBUTION", amount="200", account="QT-RRSP", date="2025-08-04")
        seed_cash("WITHDRAWAL", amount="-200", account="QT-RRSP", date="2025-09-08")

        single = run_cli(app, ["value", "2025-09", "--offline"])
        ranged = run_cli(app, ["value", "2025-08", "2025-09", "--offline"])
        emptied = run_cli(app, ["value", "2025-09", "2025-09", "--offline"])

    assert "QT-RRSP" not in _table_rows(single.plain_output)
    # It held cash at August's end, so the range keeps its column.
    assert _table_rows(ranged.plain_output)["Month"][1] == "QT-RRSP"
    assert _table_rows(ranged.plain_output)["2025-09"][1] == "0.00"
    assert "QT-RRSP" not in _table_rows(emptied.plain_output)["Month"]
