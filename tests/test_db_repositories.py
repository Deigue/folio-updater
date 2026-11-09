"""Each side table's repository takes and returns its model."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from app import get_config
from db import (
    add_column_to_table,
    aliases,
    backup_folio,
    backup_scope,
    create_txns_table,
    current_backup,
    drop_table,
    fx_rates,
    get_connection,
    get_tables,
    queries,
    quote_history,
    quotes,
    revisions,
    txns,
    update_rows,
)
from domain import Action, Column, Currency, PriceRange, QuoteStatus, Table
from models import (
    FxRate,
    NewTxn,
    Quote,
    QuoteHistory,
    TickerAlias,
    TxnFilter,
    TxnOperator,
    TxnSearch,
    TxnSelection,
    TxnSort,
)

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from collections.abc import Callable
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


# -- TXNS -----------------------------------------------------------------------

_JAN = "2024-01-15"
_FEB = "2024-02-12"
_MAR = "2024-03-11"


def _ids(selection: TxnSelection) -> list[int]:
    """Run a selection and name what it found."""
    return [txn.txn_id for txn in txns.matching(selection)]


def test_txns_read_in_date_then_id_order(temp_ctx: TempContext) -> None:
    with temp_ctx():
        later = seed_transaction(date=_MAR)
        first = seed_transaction(date=_JAN)
        second = seed_transaction(date=_JAN, ticker="MSFT")

        assert [txn.txn_id for txn in txns.read()] == [first, second, later]


def test_txns_read_nothing_before_the_table_exists(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert txns.read() == []


def test_a_txn_reads_back_with_its_numbers_and_extras(temp_ctx: TempContext) -> None:
    with temp_ctx():
        txn_id = seed_transaction(
            date=_JAN,
            amount="-1000.50",
            price="25.0125",
            units="40",
            ticker="SPY",
        )
        with get_connection() as conn:
            add_column_to_table(conn, Table.TXNS, "BrokerRef")
            update_rows(
                conn,
                Table.TXNS,
                [{Column.Txn.TXN_ID: txn_id, "BrokerRef": "R-77"}],
                [Column.Txn.TXN_ID],
                ["BrokerRef"],
            )

        (txn,) = txns.read()

    assert txn.action is Action.BUY
    assert (txn.amount, txn.price, txn.units) == (
        Decimal("-1000.5"),
        Decimal("25.0125"),
        Decimal(40),
    )
    assert txn.extras == {"BrokerRef": "R-77"}


def test_txns_by_ids_are_in_id_order_and_skip_unknown_ids(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        first = seed_transaction(date=_MAR)
        second = seed_transaction(date=_JAN)

        found = txns.by_ids([second, 999, first])

    assert [txn.txn_id for txn in found] == [first, second]


def test_txns_since_are_those_after_an_id(temp_ctx: TempContext) -> None:
    with temp_ctx():
        first = seed_transaction(date=_MAR)
        second = seed_transaction(date=_JAN)
        third = seed_transaction(date=_FEB)

        assert [txn.txn_id for txn in txns.since(first)] == [second, third]


def test_a_selection_filters_by_exact_contains_and_any_of(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        spy = seed_transaction(ticker="SPY", account="YON-MARGIN")
        msft = seed_transaction(ticker="MSFT", account="YON-RRSP")
        seed_transaction(ticker="NFLX", account="ZED-TFSA")

        equals = TxnFilter(Column.Txn.TICKER, TxnOperator.EQUALS, "SPY")
        contains = TxnFilter(Column.Txn.ACCOUNT, TxnOperator.CONTAINS, "YON")
        any_of = TxnFilter(Column.Txn.TICKER, TxnOperator.ANY_OF, ("MSFT", "SPY"))

        assert _ids(TxnSelection(filters=(equals,))) == [spy]
        assert sorted(_ids(TxnSelection(filters=(contains,)))) == [spy, msft]
        assert sorted(_ids(TxnSelection(filters=(any_of,)))) == [spy, msft]


def test_a_selection_compares_numbers_numerically(temp_ctx: TempContext) -> None:
    with temp_ctx():
        cheap = seed_transaction(fee="9.5", ticker="SPY")
        dear = seed_transaction(fee="100", ticker="MSFT")

        above = TxnFilter(Column.Txn.FEE, TxnOperator.ABOVE, "20")
        at_most = TxnFilter(Column.Txn.FEE, TxnOperator.AT_MOST, "9.5")

        assert _ids(TxnSelection(filters=(above,))) == [dear]
        assert _ids(TxnSelection(filters=(at_most,))) == [cheap]


def test_a_search_finds_a_ticker_or_an_account(temp_ctx: TempContext) -> None:
    with temp_ctx():
        by_ticker = seed_transaction(ticker="NEWCO", account="ZED-TFSA")
        by_account = seed_transaction(ticker="SPY", account="NEWCO-CASH")
        seed_transaction(ticker="MSFT", account="YON-RRSP")

        search = TxnSearch(term="NEWCO", tickers=("NEWCO", "OLDCO"))

        assert sorted(_ids(TxnSelection(searches=(search,)))) == [
            by_ticker,
            by_account,
        ]


def test_a_selection_is_newest_first_unless_told_otherwise(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        old = seed_transaction(date=_JAN)
        new = seed_transaction(date=_MAR)

        assert _ids(TxnSelection()) == [new, old]
        assert _ids(TxnSelection(oldest_first=True)) == [old, new]


def test_a_sort_breaks_ties_by_newest_date(temp_ctx: TempContext) -> None:
    with temp_ctx():
        old = seed_transaction(date=_JAN, ticker="SPY")
        new = seed_transaction(date=_MAR, ticker="SPY")
        other = seed_transaction(date=_FEB, ticker="MSFT")

        selection = TxnSelection(sorts=(TxnSort(Column.Txn.TICKER, descending=False),))

        assert _ids(selection) == [other, new, old]


def test_first_takes_the_head_of_the_order(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(date=_JAN)
        middle = seed_transaction(date=_FEB)
        newest = seed_transaction(date=_MAR)

        assert _ids(TxnSelection(first=2)) == [newest, middle]


def test_last_takes_the_tail_and_keeps_the_order(temp_ctx: TempContext) -> None:
    with temp_ctx():
        oldest = seed_transaction(date=_JAN)
        middle = seed_transaction(date=_FEB)
        seed_transaction(date=_MAR)

        assert _ids(TxnSelection(last=2)) == [middle, oldest]


def test_calculated_settlements_can_be_narrowed_to_a_broker(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_transaction(date=_JAN, settle_date=_FEB, account="ZED-TFSA")
        second = seed_transaction(date="2025-09-08", account="YON-RRSP")
        third = seed_transaction(date="2025-08-11", account="ZED-TFSA")

        assert [txn.txn_id for txn in txns.calculated_settlements()] == [third, second]
        assert [txn.txn_id for txn in txns.calculated_settlements("zed")] == [third]


def test_txns_count_all_and_calculated(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(date=_JAN, settle_date=_FEB)
        seed_transaction(date="2025-08-11")

        assert txns.count() == 2
        assert txns.count_calculated() == 1


def test_latest_id_is_zero_for_an_empty_folio(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert txns.latest_id() == 0

        newest = seed_transaction(date=_JAN)

        assert txns.latest_id() == newest


def test_traded_tickers_are_sorted_and_skip_blanks(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="SPY")
        seed_transaction(ticker="MSFT")
        seed_transaction(ticker="SPY", date=_JAN)
        seed_transaction(ticker=None, action="CONTRIBUTION")

        assert txns.traded_tickers() == ["MSFT", "SPY"]
        assert txns.tickers() == {"MSFT", "SPY"}


def test_txns_know_their_accounts_and_columns(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(account="YON-RRSP")
        seed_transaction(account="ZED-TFSA")

        with get_connection() as conn:
            add_column_to_table(conn, Table.TXNS, "BrokerRef")

        assert txns.accounts() == {"YON-RRSP", "ZED-TFSA"}
        assert txns.columns()[0] == Column.Txn.TXN_ID
        assert txns.columns()[-1] == "BrokerRef"


def test_latest_date_for_a_broker(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(date=_JAN, account="ZED-TFSA")
        seed_transaction(date=_FEB, account="ZED-RRSP")
        seed_transaction(date=_MAR, account="YON-RRSP")

        assert txns.latest_date_for("zed") == _FEB
        assert txns.latest_date_for("nobroker") is None


# -- TXN WRITES -----------------------------------------------------------------


def _new_txn(ticker: str = "SPY", **changes: object) -> NewTxn:
    """Build a purchase nothing has stored yet."""
    fields: dict[str, object] = {
        "txn_date": _JAN,
        "action": Action.BUY,
        "amount": Decimal("-312.75"),
        "currency": Currency.USD,
        "price": Decimal("20.85"),
        "units": Decimal(15),
        "fee": None,
        "ticker": ticker,
        "account": "ZED-TFSA",
        "settle_date": _FEB,
        "settle_calculated": False,
    }
    return NewTxn(**{**fields, **changes})  # ty: ignore[invalid-argument-type]


def test_append_returns_the_new_ids_in_order(temp_ctx: TempContext) -> None:
    with temp_ctx():
        create_txns_table()
        first, second = _new_txn("SPY"), _new_txn("MSFT")

        ids = txns.append([first, second])

        assert txns.by_ids(ids) == [first.stored_as(ids[0]), second.stored_as(ids[1])]
        assert ids[1] > ids[0]


def _txn_count(backup: Path | None) -> int:
    """Count the transactions a backup file holds."""
    assert backup is not None, "no backup was taken"
    with closing(sqlite3.connect(backup)) as conn:
        query = f'SELECT COUNT(*) FROM "{Table.TXNS}"'
        return int(conn.execute(query).fetchone()[0])


def test_append_backs_up_the_folio_as_it_was(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            txns.append([_new_txn()])
            backup = current_backup()

        assert _txn_count(backup) == 1
        assert txns.count() == 2


def test_append_adds_a_column_for_a_new_extra(temp_ctx: TempContext) -> None:
    with temp_ctx():
        create_txns_table()

        [txn_id] = txns.append([_new_txn(extras={"BrokerRef": "R-31"})])

        assert txns.columns()[-1] == "BrokerRef"
        assert txns.by_ids([txn_id])[0].extras == {"BrokerRef": "R-31"}


def test_an_append_of_nothing_takes_no_backup(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            assert txns.append([]) == []

            assert current_backup() is None


def test_a_refused_batch_stores_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx():
        create_txns_table()

        with pytest.raises(sqlite3.IntegrityError):
            txns.append([_new_txn(), _new_txn("MSFT", account="")])

        assert txns.count() == 0


def test_update_rewrites_a_row_and_keeps_a_none_as_null(temp_ctx: TempContext) -> None:
    with temp_ctx():
        kept = seed_transaction(ticker="MSFT")
        edited = seed_transaction(ticker="SPY", fee="4.95")
        (txn,) = txns.by_ids([edited])
        wanted = replace(txn, amount=Decimal("-99.5"), fee=None)

        assert txns.update([wanted]) == 1

        assert txns.by_ids([edited]) == [wanted]
        assert txns.by_ids([kept])[0].ticker == "MSFT"


def test_update_backs_up_the_folio_as_it_was(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        txn_id = seed_transaction(ticker="AAA", amount="-50.25")
        (txn,) = txns.by_ids([txn_id])
        with backup_scope():
            txns.update([replace(txn, amount=Decimal("-75.5"))])
            backup = current_backup()

        assert backup is not None, "no backup was taken"
        with closing(sqlite3.connect(backup)) as conn:
            query = f'SELECT "{Column.Txn.AMOUNT}" FROM "{Table.TXNS}"'
            assert conn.execute(query).fetchone()[0] == pytest.approx(-50.25)


def test_delete_removes_only_the_ids_named(temp_ctx: TempContext) -> None:
    with temp_ctx():
        gone = seed_transaction(ticker="SPY")
        kept = seed_transaction(ticker="MSFT")

        assert txns.delete([gone]) == 1

        assert [txn.txn_id for txn in txns.read()] == [kept]


def test_delete_backs_up_the_folio_as_it_was(temp_ctx: TempContext) -> None:
    with temp_ctx(backup={"enabled": True}):
        gone = seed_transaction(ticker="SPY")
        seed_transaction(ticker="MSFT")
        with backup_scope():
            txns.delete([gone])
            backup = current_backup()

        assert _txn_count(backup) == 2
        assert txns.count() == 1


def test_trial_append_names_the_refused_row_and_stores_nothing(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        create_txns_table()

        outcomes = txns.trial_append(
            [_new_txn(), _new_txn("MSFT", account=""), _new_txn("NFLX")],
        )

        assert outcomes[0] is None
        assert isinstance(outcomes[1], sqlite3.IntegrityError)
        assert len(outcomes) == 2, "the trial stops at the first refusal"
        assert txns.count() == 0


_READS: list[tuple[str, Callable[[sqlite3.Connection], object], object]] = [
    (
        "get_rows",
        lambda conn: queries.get_rows(conn, Table.TXNS).to_dict("records"),
        [],
    ),
    ("get_records", lambda conn: queries.get_records(conn, Table.TXNS), []),
    ("get_row_count", lambda conn: queries.get_row_count(conn, Table.TXNS), 0),
    (
        "get_max_value",
        lambda conn: queries.get_max_value(conn, Table.TXNS, Column.Txn.TXN_DATE),
        None,
    ),
    (
        "get_min_value",
        lambda conn: queries.get_min_value(conn, Table.TXNS, Column.Txn.TXN_DATE),
        None,
    ),
    (
        "get_distinct_values",
        lambda conn: queries.get_distinct_values(conn, Table.TXNS, Column.Txn.TICKER),
        [],
    ),
    (
        "get_broker_latest_txn_date",
        lambda conn: queries.get_broker_latest_txn_date(conn, "IBKR"),
        None,
    ),
    ("get_revisions", queries.get_revisions, {}),
    (
        "get_fx_summary",
        lambda conn: queries.get_fx_summary(conn, "2024-01-02"),
        "empty",
    ),
]
_READ_IDS = [name for name, _, _ in _READS]


@pytest.mark.parametrize(
    ("read", "empty"),
    [(read, empty) for _, read, empty in _READS],
    ids=_READ_IDS,
)
def test_a_missing_table_reads_as_empty(
    temp_ctx: TempContext,
    read: Callable[[sqlite3.Connection], object],
    empty: object,
) -> None:
    with temp_ctx(), get_connection() as conn:
        assert read(conn) == empty


@pytest.mark.parametrize("read", [read for _, read, _ in _READS], ids=_READ_IDS)
def test_a_locked_folio_raises_instead_of_reading_empty(
    temp_ctx: TempContext,
    read: Callable[[sqlite3.Connection], object],
) -> None:
    with temp_ctx():
        create_txns_table()
        path = get_config().db_path
        with (
            closing(sqlite3.connect(path)) as holder,
            closing(sqlite3.connect(path, timeout=0)) as blocked,
        ):
            holder.execute("BEGIN EXCLUSIVE")

            with pytest.raises(sqlite3.OperationalError, match="locked"):
                read(blocked)


def test_a_misspelt_column_raises_instead_of_reading_empty(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        create_txns_table()
        with (
            get_connection() as conn,
            pytest.raises(
                sqlite3.OperationalError,
                match="no such column",
            ),
        ):
            queries.get_records(conn, Table.TXNS, where="Nope = 1")


def test_a_failed_backup_is_tried_again_by_the_next_write(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        msg = "disk I/O error"
        raise sqlite3.OperationalError(msg)

    with temp_ctx(backup={"enabled": True}):
        seed_transaction(ticker="AAA")
        with backup_scope():
            with monkeypatch.context() as patch:
                patch.setattr("db.backup.backup_database", fail)
                with pytest.raises(sqlite3.OperationalError, match="disk"):
                    backup_folio()

            assert backup_folio() is not None


def test_dropping_txns_and_aliases_removes_their_tables(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="AAA")
        aliases.write([OLDER])

        txns.drop()
        aliases.drop()

        with get_connection() as conn:
            tables = get_tables(conn)
        assert Table.TXNS not in tables
        assert Table.TICKER_ALIASES not in tables
