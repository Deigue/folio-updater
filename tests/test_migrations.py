"""A folio from an older build is migrated to the schema a new folio starts with."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from db.migrations import LATEST, MigrationError, bring_up_to_date
from db.queries import append_rows, get_connection, get_rows
from db.schema import (
    create_fx_table,
    create_quote_history_table,
    create_quotes_table,
    create_ticker_aliases_table,
    create_txns_table,
)
from domain import Column, Table

if TYPE_CHECKING:
    from pathlib import Path

    from tests.helpers.context import TempContext

# The schema every folio had before migrations existed (version 0), frozen as
# the build before them wrote it. Fee and Description were added as text when
# an import first carried them.
_DATE = '"[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]"'
_CHANGE = (
    'INSERT OR REPLACE INTO "Revisions" ("TableName", "Token") '
    "VALUES ('{0}', hex(randomblob(16)))"
)
_VERSION_0 = [
    f"""CREATE TABLE "Txns" ("TxnId" INTEGER PRIMARY KEY AUTOINCREMENT,
    "TxnDate" TEXT CHECK(length("TxnDate") = 10 AND "TxnDate" GLOB {_DATE}),
    "Action" TEXT CHECK("Action" IN ('BUY', 'SELL', 'DIVIDEND', 'CONTRIBUTION', 'FCH',
        'FXT', 'ROC', 'SPLIT', 'TFR_IN', 'TFR_OUT', 'WITHDRAWAL')),
    "Amount" NUMERIC(20,10), "$" TEXT CHECK("$" IN ('USD', 'CAD')),
    "Price" NUMERIC(20,10), "Units" NUMERIC(20,10),
    "Ticker" TEXT CHECK("Ticker" IS NULL OR ("Ticker" = UPPER("Ticker")
        AND length("Ticker") > 0)),
    "Account" TEXT CHECK("Account" IS NOT NULL AND length("Account") > 0),
    "SettleDate" TEXT CHECK(length("SettleDate") = 10 AND "SettleDate" GLOB {_DATE}),
    "SettleCalculated" INTEGER CHECK("SettleCalculated" IN (0, 1)))""",
    'ALTER TABLE "Txns" ADD COLUMN "Fee" TEXT',
    'ALTER TABLE "Txns" ADD COLUMN "Description" TEXT',
    f"""CREATE TABLE "FX" ("Date" TEXT PRIMARY KEY CHECK(length("Date") = 10 AND
    "Date" GLOB {_DATE}), "FXUSDCAD" NUMERIC(20,10) NOT NULL,
    "FXCADUSD" NUMERIC(20,10) NOT NULL)""",
    f"""CREATE TABLE "TickerAliases" ("OldTicker" TEXT PRIMARY KEY,
    "NewTicker" TEXT NOT NULL, "EffectiveDate" TEXT NOT NULL
    CHECK(length("EffectiveDate") = 10 AND "EffectiveDate" GLOB {_DATE}))""",
    """CREATE TABLE "QuoteHistory" ("Symbol" TEXT NOT NULL, "Range" TEXT NOT NULL
    CHECK("Range" IN ('2h', '1d', '2d', '1wk', '1mo', '6mo', 'YTD', '1y', '5y', 'All')),
    "Seq" INTEGER NOT NULL, "Date" TEXT NOT NULL, "Close" NUMERIC(20,10) NOT NULL,
    "FetchedAt" TEXT NOT NULL, PRIMARY KEY ("Symbol", "Range", "Seq"))""",
    """CREATE TABLE "Quotes" ("Symbol" TEXT PRIMARY KEY CHECK("Symbol" = UPPER("Symbol")
    AND length("Symbol") > 0), "ProviderSymbol" TEXT NOT NULL, "Price" NUMERIC(20,10),
    "PrevClose" NUMERIC(20,10), "Currency" TEXT CHECK("Currency" IS NULL OR
    "Currency" IN ('USD', 'CAD', 'EUR')), "Name" TEXT, "Sector" TEXT, "Exchange" TEXT,
    "MarketCap" NUMERIC(20,10), "QuoteType" TEXT, "TrailingPE" NUMERIC(20,10),
    "ForwardPE" NUMERIC(20,10), "EPS" NUMERIC(20,10), "Beta" NUMERIC(20,10),
    "DividendRate" NUMERIC(20,10), "DividendYield" NUMERIC(20,10),
    "LastDividend" NUMERIC(20,10), "ExDividendDate" TEXT, "EarningsDate" TEXT,
    "High52" NUMERIC(20,10), "Low52" NUMERIC(20,10), "Avg50" NUMERIC(20,10),
    "Avg200" NUMERIC(20,10), "Volume" NUMERIC(20,10), "AvgVolume" NUMERIC(20,10),
    "ExpenseRatio" NUMERIC(20,10), "TotalAssets" NUMERIC(20,10), "Category" TEXT,
    "FundFamily" TEXT, "Splits" TEXT, "QuoteTime" TEXT, "FetchedAt" TEXT,
    "MetaFetchedAt" TEXT, "Source" TEXT,
    "Status" TEXT CHECK("Status" IN ('OK', 'NOT_FOUND', 'ERROR')))""",
    'CREATE TABLE "Revisions" ("TableName" TEXT PRIMARY KEY, "Token" TEXT NOT NULL)',
    *(
        f'CREATE TRIGGER "{table}_{event.lower()}_revises" AFTER {event} ON "{table}" '
        f"BEGIN {_CHANGE.format(table)}; END"
        for table in ("Txns", "TickerAliases")
        for event in ("INSERT", "UPDATE", "DELETE")
    ),
]

_TXN_COLUMNS = (
    '"TxnId", "TxnDate", "Action", "Amount", "$", "Price", "Units", "Ticker", '
    '"Account", "SettleDate", "SettleCalculated", "Fee", "Description"'
)


def _version_0(db_path: Path, *statements: str) -> None:
    """Write a folio the way the build before migrations left one."""
    with closing(sqlite3.connect(db_path)) as conn:
        for statement in (*_VERSION_0, *statements):
            conn.execute(statement)
        conn.commit()


def _schema(db_path: Path) -> list[tuple[str, str, str]]:
    """Every table, index and trigger a folio holds, whitespace aside."""
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name",
        ).fetchall()
    return [(kind, name, _spaced(sql or "")) for kind, name, sql in rows]


def _spaced(sql: str) -> str:
    """Collapse the layout of a statement, which SQLite keeps as it was written."""
    sql = re.sub(r"\s+", " ", sql)
    return re.sub(r"\s*([(),])\s*", r"\1", sql).strip()


def _version(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
    return int(version)


def test_a_version_0_folio_is_migrated_to_the_schema_a_new_one_has(
    temp_ctx: TempContext,
) -> None:
    """The folio is backed up first, then ends where a new folio starts."""
    with temp_ctx(backup={"enabled": True}) as ctx:
        db_path = ctx.config.db_path
        _version_0(
            db_path,
            f'INSERT INTO "Txns" ({_TXN_COLUMNS}) VALUES '
            "(1, '2024-02-01', 'BUY', -10, 'CAD', 1, 10, 'SPY', 'TFSA-TEST', "
            "'2024-02-02', 0, NULL, NULL)",
        )
        with get_connection():
            pass
        migrated = _schema(db_path)
        backups = list(ctx.config.backup_path.rglob("*.db"))
        assert [_version(backup) for backup in backups] == [0]

        db_path.unlink()
        for create in (
            create_txns_table,
            create_fx_table,
            create_ticker_aliases_table,
            create_quote_history_table,
            create_quotes_table,
        ):
            create()
        fresh = _schema(db_path)
        fresh_version = _version(db_path)

    assert migrated == fresh
    assert fresh_version == LATEST


def test_migrating_keeps_every_transaction_its_id_and_its_extra_columns(
    temp_ctx: TempContext,
) -> None:
    """TxnIds, the counter, fees, extras and a missing settlement date carry over."""
    rows = [
        (
            "1, '2024-02-01', 'BUY', -1001.5, 'USD', 100.15, 10, 'SPY', 'TFSA-TEST', "
            "'2024-02-02', 0, '-1.5', 'Bought', 'R-1'"
        ),
        (
            "4, '2024-03-01', 'CONTRIBUTION', 500, 'CAD', NULL, NULL, NULL, "
            "'TFSA-TEST', "
            "NULL, NULL, NULL, NULL, NULL"
        ),
        (
            "9, '2024-03-05', 'DIVIDEND', 3.21, 'USD', NULL, NULL, 'SPY', 'TFSA-TEST', "
            "'2024-03-05', 1, NULL, NULL, 'R-2'"
        ),
    ]
    with temp_ctx() as ctx:
        db_path = ctx.config.db_path
        _version_0(
            db_path,
            'ALTER TABLE "Txns" ADD COLUMN "BrokerRef" TEXT',
            *(f'INSERT INTO "Txns" VALUES ({row})' for row in rows),
            # A deleted transaction leaves the counter past the last TxnId.
            "UPDATE sqlite_sequence SET seq = 12 WHERE name = 'Txns'",
        )
        with get_connection() as conn:
            frame = get_rows(conn, Table.TXNS, order_by=f'"{Column.Txn.TXN_ID}"')
            append_rows(
                conn,
                Table.TXNS,
                frame.drop(columns=[Column.Txn.TXN_ID]).head(1),
            )
            (next_id,) = conn.execute('SELECT MAX("TxnId") FROM "Txns"').fetchone()

    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    assert [record[Column.Txn.TXN_ID] for record in records] == [1, 4, 9]
    assert Decimal(str(records[0][Column.Txn.FEE])) == Decimal("-1.5")
    assert [record["BrokerRef"] for record in records] == ["R-1", None, "R-2"]
    assert records[1][Column.Txn.SETTLE_DATE] == "2024-03-01"
    assert records[1][Column.Txn.SETTLE_CALCULATED] == 1
    assert list(frame.columns)[-1] == "BrokerRef"
    assert next_id == 13


def test_a_folio_a_migration_cannot_carry_is_left_as_it_was(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        db_path = ctx.config.db_path
        _version_0(
            db_path,
            f"INSERT INTO \"Txns\" ({_TXN_COLUMNS}) VALUES (1, '2024-02-01', NULL, "
            "-10, 'CAD', 1, 10, 'SPY', 'TFSA-TEST', '2024-02-02', 0, NULL, NULL)",
        )
        before = _schema(db_path)

        with (
            closing(sqlite3.connect(db_path)) as conn,
            pytest.raises(MigrationError, match="1 transaction"),
        ):
            bring_up_to_date(conn)

        assert _schema(db_path) == before
        assert _version(db_path) == 0


def test_a_folio_a_newer_build_migrated_is_refused(temp_ctx: TempContext) -> None:
    """An older build never reads, or writes, a schema it does not know."""
    with temp_ctx() as ctx:
        db_path = ctx.config.db_path
        _version_0(db_path, f"PRAGMA user_version = {LATEST + 1}")
        before = _schema(db_path)

        with (
            closing(sqlite3.connect(db_path)) as conn,
            pytest.raises(MigrationError, match="newer folio"),
        ):
            bring_up_to_date(conn)

        assert _schema(db_path) == before
