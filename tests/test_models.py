"""Each stored table's model reads back exactly what it writes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from db import txns as txn_store
from db.catalog import get_columns
from db.queries import append_rows, get_connection, get_rows
from db.schema import (
    create_fx_table,
    create_quote_history_table,
    create_quotes_table,
    create_ticker_aliases_table,
    create_txns_table,
)
from domain import Action, Column, Currency, PriceRange, QuoteStatus, Table
from models import (
    Fundamentals,
    FxRate,
    NewTxn,
    Quote,
    QuoteHistory,
    QuoteSplit,
    TickerAlias,
    Txn,
)
from models.columns import stored_columns

if TYPE_CHECKING:
    from collections.abc import Callable

    from _typeshed import DataclassInstance

    from tests.helpers.context import TempContext

_EXTRA = "BrokerRef"


def _store_txns(new: list[NewTxn]) -> list[Txn]:
    """Write transactions the way an import does, then read them back."""
    create_txns_table()
    return txn_store.by_ids(txn_store.append(new))


def _store(table: Table, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Append rows to a side table, then read every row back."""
    with get_connection() as conn:
        append_rows(conn, table, pd.DataFrame(rows))
        return _records(get_rows(conn, table))


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    """Return a frame's rows, each keyed by column name."""
    return [
        {str(column): value for column, value in record.items()}
        for record in frame.to_dict("records")
    ]


def test_fx_rate_reads_back_what_it_writes(temp_ctx: TempContext) -> None:
    """Both directions of the rate keep their full precision."""
    rate = FxRate(
        date="2024-02-29",
        usd_cad=Decimal("1.3542"),
        cad_usd=Decimal("0.7384432137"),
    )

    with temp_ctx():
        create_fx_table()
        (stored,) = _store(Table.FX, [rate.to_row()])

    assert FxRate.from_row(stored) == rate


def test_ticker_alias_reads_back_what_it_writes(temp_ctx: TempContext) -> None:
    """A rename keeps both tickers and the day it took effect."""
    alias = TickerAlias(
        old_ticker="FB",
        new_ticker="META",
        effective_date="2022-06-09",
    )

    with temp_ctx():
        create_ticker_aliases_table()
        (stored,) = _store(Table.TICKER_ALIASES, [alias.to_row()])

    assert TickerAlias.from_row(stored) == alias


def test_quote_history_reads_back_what_it_writes(temp_ctx: TempContext) -> None:
    """A chart point keeps its range, place, close and when it was fetched."""
    point = QuoteHistory(
        symbol="SPY",
        range=PriceRange.YEAR_1,
        seq=3,
        date="2024-05-17",
        close=Decimal("529.45"),
        fetched_at=datetime(2024, 5, 18, 14, 30, tzinfo=UTC),
    )

    with temp_ctx():
        create_quote_history_table()
        (stored,) = _store(Table.QUOTE_HISTORY, [point.to_row()])

    assert QuoteHistory.from_row(stored) == point


def test_quote_reads_back_what_it_writes(temp_ctx: TempContext) -> None:
    """Fundamentals, splits and both fetch times come back as they went in."""
    quote = Quote(
        symbol="VFV.TO",
        provider_symbol="VFV.TO",
        price=Decimal("142.87"),
        prev_close=Decimal("141.02"),
        currency=Currency.CAD,
        name="Vanguard S&P 500 Index ETF",
        exchange="TOR",
        fundamentals=Fundamentals(
            quote_type="ETF",
            dividend_yield=Decimal("0.0109"),
            high_52=Decimal("150.11"),
            category="US Equity",
        ),
        splits=(QuoteSplit("2019-05-01", Decimal(2)),),
        quote_time="2024-08-02T16:00:00-04:00",
        fetched_at=datetime(2024, 8, 2, 20, 5, tzinfo=UTC),
        meta_fetched_at=datetime(2024, 7, 30, 12, 0, tzinfo=UTC),
        source="yfinance",
        status=QuoteStatus.OK,
    )
    never_fetched = Quote(symbol="SPY", provider_symbol="SPY")

    with temp_ctx():
        create_quotes_table()
        stored = _store(Table.QUOTES, [quote.to_row(), never_fetched.to_row()])

    assert [Quote.from_row(row) for row in stored] == [quote, never_fetched]


def test_txn_reads_back_what_it_writes(temp_ctx: TempContext) -> None:
    """Blanks stay blank, extras survive, and each transaction gets its ID."""
    buy = NewTxn(
        txn_date="2024-03-04",
        action=Action.BUY,
        amount=Decimal("-1234.5678"),
        currency=Currency.USD,
        price=Decimal("123.45678"),
        units=Decimal(10),
        fee=Decimal("-4.95"),
        ticker="MSFT",
        account="TFSA-TEST",
        settle_date="2024-03-05",
        settle_calculated=True,
        description="Bought ten",
        extras={_EXTRA: "A-17"},
    )
    contribution = NewTxn(
        txn_date="2024-04-10",
        action=Action.CONTRIBUTION,
        amount=Decimal(250),
        currency=Currency.CAD,
        price=None,
        units=None,
        fee=None,
        ticker=None,
        account="TFSA-TEST",
        settle_date="2024-04-10",
        settle_calculated=False,
    )

    with temp_ctx():
        stored = _store_txns([buy, contribution])

    first, second = stored
    assert first == buy.stored_as(first.txn_id)
    # Every row has the extra column, so the contribution reads it back blank.
    expected = replace(contribution, extras={_EXTRA: None})
    assert second == expected.stored_as(second.txn_id)
    assert second.txn_id > first.txn_id


def test_a_txn_row_with_an_unknown_action_is_refused() -> None:
    """A row folio cannot read stops the read rather than leaving the replay."""
    row = {
        **NewTxn(
            txn_date="2024-01-02",
            action=Action.BUY,
            amount=Decimal(-100),
            currency=Currency.CAD,
            price=Decimal(10),
            units=Decimal(10),
            fee=None,
            ticker="SPY",
            account="TFSA-TEST",
            settle_date="2024-01-03",
            settle_calculated=True,
        ).to_row(),
        str(Column.Txn.TXN_ID): 1,
        str(Column.Txn.ACTION): "NOT_AN_ACTION",
    }

    with pytest.raises(ValueError, match="NOT_AN_ACTION"):
        Txn.from_row(row)


@pytest.mark.parametrize(
    ("create", "table", "model"),
    [
        (create_txns_table, Table.TXNS, Txn),
        (create_fx_table, Table.FX, FxRate),
        (create_ticker_aliases_table, Table.TICKER_ALIASES, TickerAlias),
        (create_quotes_table, Table.QUOTES, Quote),
        (create_quote_history_table, Table.QUOTE_HISTORY, QuoteHistory),
    ],
)
def test_each_table_is_built_from_its_model(
    temp_ctx: TempContext,
    create: Callable[[], None],
    table: Table,
    model: type[DataclassInstance],
) -> None:
    """A fresh table holds exactly the columns its model declares, in order."""
    with temp_ctx():
        create()
        with get_connection() as conn:
            built = get_columns(conn, table)

    assert built == [column.name for column in stored_columns(model)]
