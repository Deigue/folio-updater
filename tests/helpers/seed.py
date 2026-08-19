"""Direct-to-database transaction seeding for tests.

Tests for `edit` and `delete` need a known row to operate on.

`seed_transaction` writes the same row straight to the Txns table, keeping only the one
derived step a seeded row genuinely needs: settlement dates from the market calendars.
Rows are byte-identical to what `folio add` produces.
"""

from __future__ import annotations

import pandas as pd

from db import (
    create_fx_table,
    create_txns_table,
    get_connection,
    get_last_insert_rowid,
    helpers,
    insert_or_replace,
)
from ingest import ActionValidationRules
from utils.constants import Column, Currency, Sign, Table
from utils.settlement_calculator import settlement_calculator

# Inside the mock data range, so settlement calculations hit the market
# calendars preloaded by the session fixture.
TXN_DATE = "2025-08-15"
ACCOUNT = "TESTACCT"
TICKER = "TESTTKR"


def _numeric(value: str | None) -> float | int | None:
    """Coerce a CLI-style numeric string the way the import formatter would."""
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def seed_transaction(
    *,
    action: str = "BUY",
    date: str = TXN_DATE,
    account: str = ACCOUNT,
    currency: str = Currency.USD.value,
    ticker: str | None = TICKER,
    amount: str | None = "-1502.50",
    price: str | None = "150.25",
    units: str | None = "10",
    fee: str | None = None,
    settle_date: str | None = None,
) -> int:
    """Insert one known transaction directly and return its TxnId.

    Args:
        action: Transaction action (BUY, SELL, ...).
        date: Transaction date in YYYY-MM-DD form.
        account: Owning account name.
        currency: Currency code.
        ticker: Ticker symbol, or None for a row that carries no security.
        amount: Total cash amount, or None to leave it blank.
        price: Optional price per unit.
        units: Optional number of units.
        fee: Optional fee. Supplying one adds the Fee column to the table, the
            same way an `add` carrying a fee would.
        settle_date: Explicit settlement date. Left to the market calendars
            when omitted, which is what an imported row gets.

    Returns:
        The TxnId assigned by SQLite.
    """
    row: dict[str, object] = {
        Column.Txn.TXN_DATE: date,
        Column.Txn.ACTION: action,
        Column.Txn.AMOUNT: _numeric(amount),
        Column.Txn.CURRENCY: currency,
        Column.Txn.PRICE: _numeric(price),
        Column.Txn.UNITS: _numeric(units),
        Column.Txn.TICKER: ticker,
        Column.Txn.ACCOUNT: account,
    }
    if fee is not None:
        row[Column.Txn.FEE] = _numeric(fee)
    if settle_date is not None:
        row[Column.Txn.SETTLE_DATE] = settle_date
        row[Column.Txn.SETTLE_CALCULATED] = 0

    for column, sign in ActionValidationRules.get_sign_rules_for_action(action).items():
        value = row.get(column)
        if not isinstance(value, (int, float)) or value == 0:
            continue
        if (sign is Sign.NEGATIVE and value > 0) or (
            sign is Sign.POSITIVE and value < 0
        ):
            row[column] = -value

    if settle_date is None:
        settled = settlement_calculator.add_settlement_dates_to_dataframe(
            pd.DataFrame([row]),
        )
    else:
        settled = pd.DataFrame([row])
    row = {str(key): value for key, value in settled.iloc[0].to_dict().items()}

    # Mirrors the pipeline's original setup.
    create_txns_table()
    helpers.sync_txns_table_columns(settled)
    with get_connection() as conn:
        insert_or_replace(conn, Table.TXNS, row)
        return get_last_insert_rowid(conn)


def seed_fx(rows: dict[str, str]) -> None:
    """Write exact FX rates so a replay's conversions are predictable.

    Args:
        rows: `YYYY-MM-DD` to `FXUSDCAD`, as strings so the stored value is
            exactly what the test asked for. `FXCADUSD` is derived the way
            `ForexService` derives it, and is never read by the engine.
    """
    create_fx_table()
    with get_connection() as conn:
        for date, rate in rows.items():
            insert_or_replace(
                conn,
                Table.FX,
                {
                    Column.FX.DATE: date,
                    Column.FX.FXUSDCAD: rate,
                    Column.FX.FXCADUSD: str(round(1.0 / float(rate), 10)),
                },
            )
