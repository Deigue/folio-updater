"""Tests for the shared transaction selection engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cli.selection import SelectionMode, select_transactions
from datagen import ensure_data_exists
from db import get_connection, get_rows
from utils.constants import Column, Table

if TYPE_CHECKING:
    from tests.test_types import TempContext


def _all_txn_ids() -> list[int]:
    """Get every TxnId currently in the database, ascending."""
    with get_connection() as conn:
        rows = get_rows(
            conn,
            Table.TXNS,
            order_by=f'"{Column.Txn.TXN_ID}" ASC',
        )
    return [int(txn_id) for txn_id in rows[Column.Txn.TXN_ID]]


class TestSelectionMode:
    """Choosing between TxnIds and query terms."""

    def test_all_integer_terms_are_txn_ids(self, temp_ctx: TempContext) -> None:
        """A list of plain integers selects by TxnId."""
        with temp_ctx():
            ensure_data_exists()
            ids = _all_txn_ids()[:3]

            selection = select_transactions([str(i) for i in ids])

            assert selection.mode is SelectionMode.IDS
            assert selection.query is None
            assert selection.txn_ids == ids

    def test_mixed_terms_are_query_terms(self, temp_ctx: TempContext) -> None:
        """Any non-integer term makes the whole thing a query."""
        with temp_ctx():
            ensure_data_exists()

            selection = select_transactions(["BUY", "2024"])

            assert selection.mode is SelectionMode.QUERY
            assert selection.query is not None
            assert selection.requested_ids == ()

    def test_ids_are_deduplicated_in_order(self, temp_ctx: TempContext) -> None:
        """A repeated TxnId is requested once, in the order first given."""
        with temp_ctx():
            ensure_data_exists()
            first, second = _all_txn_ids()[:2]

            selection = select_transactions(
                [str(second), str(first), str(second)],
            )

            assert selection.requested_ids == (second, first)
            assert selection.txn_ids == sorted([first, second])

    def test_missing_ids_are_reported(self, temp_ctx: TempContext) -> None:
        """TxnIds with no matching row come back in missing_ids."""
        with temp_ctx():
            ensure_data_exists()
            existing = _all_txn_ids()[0]

            selection = select_transactions([str(existing), "999999"])

            assert selection.missing_ids == (999999,)
            assert selection.txn_ids == [existing]

    def test_results_are_ordered_by_txn_id(self, temp_ctx: TempContext) -> None:
        """Id selections come back in TxnId order regardless of input order."""
        with temp_ctx():
            ensure_data_exists()
            first, second, third = _all_txn_ids()[:3]

            selection = select_transactions(
                [str(third), str(first), str(second)],
            )

            assert selection.txn_ids == [first, second, third]


    def test_empty_query_result_has_no_txn_ids(self, temp_ctx: TempContext) -> None:
        """A selection that matched nothing reports no ids rather than raising."""
        with temp_ctx():
            ensure_data_exists()

            selection = select_transactions(["ticker:NOSUCHTICKER"])

            assert selection.transactions.empty
            assert selection.txn_ids == []


class TestTailLimit:
    """'last N' takes the tail of the requested sort order, not its head."""

    def test_ascending_sort_last_n_returns_the_highest_ids(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """'sort:txnid last N' returns the N highest TxnIds, sorted ascending."""
        with temp_ctx():
            ensure_data_exists()
            expected = _all_txn_ids()[-5:]

            selection = select_transactions(["sort:txnid", "last", "5"])

            assert selection.txn_ids == expected

    def test_descending_sort_last_n_returns_the_lowest_ids(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """'sort:-txnid last N' returns the N lowest TxnIds, sorted descending."""
        with temp_ctx():
            ensure_data_exists()
            expected = list(reversed(_all_txn_ids()[:5]))

            selection = select_transactions(["sort:-txnid", "last", "5"])

            assert selection.txn_ids == expected

    def test_first_n_still_returns_the_head(self, temp_ctx: TempContext) -> None:
        """'sort:txnid first N' is unaffected: it still takes the head."""
        with temp_ctx():
            ensure_data_exists()
            expected = _all_txn_ids()[:5]

            selection = select_transactions(["sort:txnid", "first", "5"])

            assert selection.txn_ids == expected


class TestSelectionBounds:
    """The guard that keeps a selection from sweeping the whole folio."""

    def test_query_without_filters_is_unbounded(self, temp_ctx: TempContext) -> None:
        """Sorting alone narrows nothing."""
        with temp_ctx():
            ensure_data_exists()

            assert select_transactions(["sort:Amount"]).is_unbounded

    def test_limit_only_query_is_bounded(self, temp_ctx: TempContext) -> None:
        """A limit caps the damage even with no filters."""
        with temp_ctx():
            ensure_data_exists()

            selection = select_transactions(["limit:2"])

            assert not selection.is_unbounded
            assert len(selection.transactions) == 2

    def test_filtered_query_is_bounded(self, temp_ctx: TempContext) -> None:
        """A filter is enough to make a selection deliberate."""
        with temp_ctx():
            ensure_data_exists()

            assert not select_transactions(["BUY"]).is_unbounded

    def test_id_selection_is_never_unbounded(self, temp_ctx: TempContext) -> None:
        """An explicit id list is bounded by definition."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = _all_txn_ids()[0]

            assert not select_transactions([str(txn_id)]).is_unbounded


class TestSelectionDescribe:
    """The audit-log description of a selection."""

    def test_describes_an_id_selection(self, temp_ctx: TempContext) -> None:
        """Id selections name the ids that were asked for."""
        with temp_ctx():
            ensure_data_exists()
            first, second = _all_txn_ids()[:2]

            described = select_transactions([str(first), str(second)]).describe()

            assert described == f"TxnIds {first} {second}"

    def test_describes_a_query_selection(self, temp_ctx: TempContext) -> None:
        """Query selections quote the parsed query."""
        with temp_ctx():
            ensure_data_exists()

            described = select_transactions(["BUY"]).describe()

            assert described.startswith("query: ")
            assert "BUY" in described
