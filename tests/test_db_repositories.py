"""Each side table's repository takes and returns its model."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from db import (
    aliases,
    backup_scope,
    current_backup,
    drop_table,
    fx_rates,
    get_connection,
    get_tables,
    quote_history,
    quotes,
    revisions,
    txns,
)
from domain import Column, PriceRange, QuoteStatus, Table
from models import FxRate, Quote, QuoteHistory, TickerAlias

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from .helpers.context import TempContext

OLDER = TickerAlias(old_ticker="AAA", new_ticker="BBB", effective_date="2021-03-15")
NEWER = TickerAlias(old_ticker="CCC", new_ticker="DDD", effective_date="2023-09-04")


def _alias_count(backup: Path | None) -> int:
    """Count the renames a backup file holds."""
    assert backup is not None, "no backup was taken"
    with closing(sqlite3.connect(backup)) as conn:
        query = f'SELECT COUNT(*) FROM "{Table.TICKER_ALIASES}"'
        return int(conn.execute(query).fetchone()[0])


def test_aliases_read_back_oldest_first(temp_ctx: TempContext) -> None:
    """Multi-hop chains resolve in effective-date order."""
    with temp_ctx():
        aliases.write([NEWER, OLDER])

        assert aliases.read() == [OLDER, NEWER]


def test_aliases_are_found_by_their_old_ticker(temp_ctx: TempContext) -> None:
    with temp_ctx():
        aliases.write([OLDER])

        assert aliases.find("AAA") == OLDER
        assert aliases.find("BBB") is None


def test_an_alias_write_replaces_the_same_old_ticker(temp_ctx: TempContext) -> None:
    moved = TickerAlias(old_ticker="AAA", new_ticker="EEE", effective_date="2022-01-10")

    with temp_ctx():
        aliases.write([OLDER])
        aliases.write([moved])

        assert aliases.read() == [moved]


def test_reading_aliases_does_not_create_their_table(temp_ctx: TempContext) -> None:
    """A new watched table would make every saved ledger stale."""
    with temp_ctx():
        assert aliases.read() == []
        assert aliases.find("AAA") is None

        with get_connection() as conn:
            assert Table.TICKER_ALIASES not in get_tables(conn)


def test_an_alias_write_backs_up_first(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        aliases.write([OLDER])
        with backup_scope():
            aliases.write([NEWER])
            backup = current_backup()

        assert _alias_count(backup) == 1


def test_an_alias_delete_backs_up_first(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        aliases.write([OLDER, NEWER])
        with backup_scope():
            removed = aliases.delete("AAA")
            backup = current_backup()

        assert removed == 1
        assert aliases.read() == [NEWER]
        assert _alias_count(backup) == 2


# -- FX RATES -------------------------------------------------------------------


def _rate(date: str, usd_cad: str) -> FxRate:
    """Build a rate; its inverse is never what these tests look at."""
    return FxRate(date=date, usd_cad=Decimal(usd_cad), cad_usd=Decimal("0.75"))


JAN_2 = _rate("2024-01-02", "1.25")
JAN_5 = _rate("2024-01-05", "1.5")
JAN_9 = _rate("2024-01-09", "2")


def test_fx_rates_read_back_in_date_order(temp_ctx: TempContext) -> None:
    with temp_ctx():
        written = fx_rates.write([JAN_9, JAN_2, JAN_5])

        assert written == 3
        assert fx_rates.read() == [JAN_2, JAN_5, JAN_9]


def test_fx_rates_know_their_earliest_and_latest_dates(temp_ctx: TempContext) -> None:
    with temp_ctx():
        fx_rates.write([JAN_5, JAN_2, JAN_9])

        assert fx_rates.earliest_date() == "2024-01-02"
        assert fx_rates.latest_date() == "2024-01-09"


def test_fx_rates_read_empty_before_any_are_held(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert fx_rates.read() == []
        assert fx_rates.earliest_date() is None
        assert fx_rates.latest_date() is None
        assert fx_rates.summary("2024-01-05") == "empty"


def test_an_fx_write_refuses_a_date_already_held(temp_ctx: TempContext) -> None:
    """A published rate never changes, so a second one is a mistake."""
    with temp_ctx():
        fx_rates.write([JAN_2])

        with pytest.raises(sqlite3.IntegrityError):
            fx_rates.write([_rate("2024-01-02", "1.75")])

        assert fx_rates.read() == [JAN_2]


def test_an_fx_write_takes_no_backup(temp_ctx: TempContext) -> None:
    """Rates can always be fetched again."""
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            fx_rates.write([JAN_2])

            assert current_backup() is None


def test_fx_summary_covers_rates_up_to_the_cutoff(temp_ctx: TempContext) -> None:
    with temp_ctx():
        fx_rates.write([JAN_2, JAN_5, JAN_9])

        assert fx_rates.summary("2024-01-06") == "2|2.75|2024-01-05"


def test_fx_summary_keeps_the_earliest_rate_past_the_cutoff(
    temp_ctx: TempContext,
) -> None:
    """A conversion dated before every rate still uses the earliest one."""
    with temp_ctx():
        fx_rates.write([JAN_5, JAN_9])

        assert fx_rates.summary("2024-01-01") == "1|1.5|2024-01-05"


# -- REVISIONS AND TXNS ----------------------------------------------------------


def test_revision_tokens_change_with_a_watched_table(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="AAA")
        before = revisions.tokens()
        seed_transaction(ticker="BBB")

        assert before[Table.TXNS] != revisions.tokens()[Table.TXNS]


def test_revision_tokens_leave_out_a_dropped_table(temp_ctx: TempContext) -> None:
    """A dropped table's token must not match a ledger built from it."""
    with temp_ctx():
        seed_transaction(ticker="AAA")
        with get_connection() as conn:
            drop_table(conn, Table.TXNS)

        assert Table.TXNS not in revisions.tokens()


def test_txns_know_their_earliest_transaction_and_latest_settlement_dates(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_transaction(date="2024-03-04", settle_date="2024-03-06")
        seed_transaction(date="2024-02-01", settle_date="2024-02-05")

        assert txns.earliest_date() == "2024-02-01"
        assert txns.latest_settlement_date() == "2024-03-06"


def test_txns_have_no_dates_when_the_folio_is_empty(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert txns.earliest_date() is None
        assert txns.latest_settlement_date() is None


# -- QUOTES ---------------------------------------------------------------------

_SPY = Quote(
    symbol="SPY",
    provider_symbol="SPY",
    price=Decimal("512.34"),
    status=QuoteStatus.OK,
)
_RY = Quote(symbol="RY.TO", provider_symbol="RY.TO", status=QuoteStatus.NOT_FOUND)


def test_quotes_read_back_by_symbol(temp_ctx: TempContext) -> None:
    with temp_ctx():
        quotes.write([_SPY, _RY])

        assert quotes.read() == {"SPY": _SPY, "RY.TO": _RY}
        assert quotes.read(["RY.TO"]) == {"RY.TO": _RY}


def test_quotes_read_empty_before_any_are_cached(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert quotes.read() == {}


def test_a_quote_write_replaces_the_cached_one(temp_ctx: TempContext) -> None:
    repriced = Quote(symbol="SPY", provider_symbol="SPY", price=Decimal("498.1"))

    with temp_ctx():
        quotes.write([_SPY])
        quotes.write([repriced])

        assert quotes.read() == {"SPY": repriced}


def test_quotes_delete_only_the_symbols_named(temp_ctx: TempContext) -> None:
    with temp_ctx():
        quotes.write([_SPY, _RY])

        assert quotes.delete(["SPY"]) == 1
        assert quotes.read() == {"RY.TO": _RY}


def test_quotes_delete_every_row_when_no_symbols_are_named(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        quotes.write([_SPY, _RY])

        assert quotes.delete() == 2
        assert quotes.read() == {}


def test_a_quote_write_and_delete_take_no_backup(temp_ctx: TempContext) -> None:
    """The quotes cache can always be fetched again."""
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            quotes.write([_SPY])
            quotes.delete(["SPY"])

            assert current_backup() is None


# -- QUOTE HISTORY --------------------------------------------------------------

_FETCHED = datetime(2024, 6, 3, 21, 15, tzinfo=UTC)


def _point(symbol: str, seq: int, close: str) -> QuoteHistory:
    """Build one point on a symbol's one-year chart."""
    return QuoteHistory(
        symbol=symbol,
        range=PriceRange.YEAR_1,
        seq=seq,
        date=f"2024-05-{seq + 10}",
        close=Decimal(close),
        fetched_at=_FETCHED,
    )


def test_history_reads_back_in_chart_order(temp_ctx: TempContext) -> None:
    second, first = _point("SPY", 1, "101.5"), _point("SPY", 0, "99.25")

    with temp_ctx():
        quote_history.replace("SPY", [second, first])

        assert quote_history.read(["SPY"]) == [first, second]


def test_a_history_replace_swaps_only_that_symbol(temp_ctx: TempContext) -> None:
    kept = _point("RY.TO", 0, "140.75")
    fresh = _point("SPY", 0, "105.5")

    with temp_ctx():
        quote_history.replace("RY.TO", [kept])
        quote_history.replace("SPY", [_point("SPY", 0, "99.25")])
        quote_history.replace("SPY", [fresh])

        assert quote_history.read(["RY.TO", "SPY"]) == [kept, fresh]


def test_history_reads_only_the_symbols_named(temp_ctx: TempContext) -> None:
    wanted = _point("SPY", 0, "99.25")

    with temp_ctx():
        quote_history.replace("SPY", [wanted])
        quote_history.replace("RY.TO", [_point("RY.TO", 0, "140.75")])

        assert quote_history.read(["SPY"]) == [wanted]


def test_history_deletes_by_symbol_or_all(temp_ctx: TempContext) -> None:
    kept = _point("RY.TO", 0, "140.75")

    with temp_ctx():
        quote_history.replace("SPY", [_point("SPY", 0, "99.25")])
        quote_history.replace("RY.TO", [kept])

        quote_history.delete(["SPY"])
        assert quote_history.read(["RY.TO", "SPY"]) == [kept]

        quote_history.delete()
        assert quote_history.read(["RY.TO", "SPY"]) == []


def test_history_with_an_unreadable_stamp_reads_as_none(temp_ctx: TempContext) -> None:
    """The cache is simply fetched again, rather than stopping the command."""
    with temp_ctx():
        quote_history.replace("SPY", [_point("SPY", 0, "99.25")])
        with get_connection() as conn:
            conn.execute(
                f'UPDATE "{Table.QUOTE_HISTORY}" '
                f"SET \"{Column.QuoteHistory.FETCHED_AT}\" = 'not a time'",
            )
            conn.commit()

        assert quote_history.read(["SPY"]) == []


def test_a_history_write_takes_no_backup(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            quote_history.replace("SPY", [_point("SPY", 0, "99.25")])

            assert current_backup() is None
