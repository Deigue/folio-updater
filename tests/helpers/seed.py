"""Direct-to-database transaction seeding for tests.

Tests for `edit` and `delete` need a known row to operate on.

`seed_transaction` writes the same row straight to the Txns table, keeping only the one
derived step a seeded row genuinely needs: settlement dates from the market calendars.
Rows are byte-identical to what `folio add` produces.
"""

from __future__ import annotations

import pandas as pd

from app import get_config
from db import (
    create_fx_table,
    create_txns_table,
    get_connection,
    get_last_insert_rowid,
    helpers,
    insert_or_replace,
)
from domain import Column, Currency, Sign, Table
from engine.settlement import settlement_calculator
from ingest import ActionValidationRules

# Inside the mock data range, so settlement calculations hit the market
# calendars preloaded by the session fixture.
TXN_DATE = "2025-08-15"
ACCOUNT = "TESTACCT"
TICKER = "TESTTKR"

# Mock symbols
TSX_TICKER = "TSTKR"  # Toronto listed, so the importer spells it TSTKR.TO
VENTURE_TICKER = "VNTKR"  # TSX Venture, needing a transform rule to correct

# keep track of prepared columns by database, reseed only when needed.
_prepared_txn_columns: dict[str, frozenset[str]] = {}

# settle dates derived from the triplet are always the same. Repeatedly called.
# memoized by key: (date, action, currency)
_settlement_dates: dict[tuple[str, str, str], tuple[object, object]] = {}


def reset_seed_state() -> None:
    """Forget which databases have been prepared. Called between tests."""
    _prepared_txn_columns.clear()


def _prepare_txns_table(settled: pd.DataFrame) -> None:
    """Build the Txns table and widen it, skipping what is already done."""
    db_path = str(get_config().db_path)
    columns = frozenset(settled.columns)
    known = _prepared_txn_columns.get(db_path)
    if known is not None and columns <= known:
        return

    if known is None:
        create_txns_table()
    helpers.sync_txns_table_columns(settled)
    _prepared_txn_columns[db_path] = columns | (known or frozenset())


def _apply_settlement(
    row: dict[str, object],
    *,
    date: str,
    action: str,
    currency: str,
) -> None:
    """Fill a row's settlement columns, deriving them at most once per triple.

    The derived date depends on the transaction date, the action and the
    currency's market calendar, and on nothing else in the row, so the first row
    of a given triple pays for every later one.

    Args:
        row: The row to fill, updated in place.
        date: Transaction date, in YYYY-MM-DD form.
        action: Transaction action, which sets the settlement period.
        currency: Currency code, which picks the market calendar.
    """
    key = (date, action, currency)
    settled = _settlement_dates.get(key)
    if settled is None:
        derived = settlement_calculator.add_settlement_dates_to_dataframe(
            pd.DataFrame([row]),
        ).iloc[0]
        settled = (
            derived[Column.Txn.SETTLE_DATE],
            derived[Column.Txn.SETTLE_CALCULATED],
        )
        _settlement_dates[key] = settled

    row[Column.Txn.SETTLE_DATE], row[Column.Txn.SETTLE_CALCULATED] = settled


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
        _apply_settlement(row, date=date, action=action, currency=currency)
    settled = pd.DataFrame([row])
    row = {str(key): value for key, value in settled.iloc[0].to_dict().items()}

    # Mirrors the pipeline's original setup.
    _prepare_txns_table(settled)
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
