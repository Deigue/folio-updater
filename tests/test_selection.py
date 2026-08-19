"""Tests for the shared transaction selection engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cli.selection import SelectionMode, select_transactions
from datagen import ensure_data_exists
from db import add_column_to_table, get_connection, get_rows, update_rows
from utils.constants import Column, Table

from .helpers.seed import ACCOUNT, seed_transaction

if TYPE_CHECKING:
    from tests.test_types import TempContext

# Restricts a selection to the seeded rows, leaving the mock folio out of it.
_SEEDED = f"account:{ACCOUNT}"


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


class TestBareLimitOrder:
    """Bare `first N`/`last N` (no explicit sort:) mean earliest/most-recent N."""

    def test_bare_last_n_returns_the_most_recent(self, temp_ctx: TempContext) -> None:
        """'last N' with no sort returns the N newest transactions by date."""
        with temp_ctx():
            ensure_data_exists()
            seed_transaction(date="2025-01-01")
            middle = seed_transaction(date="2025-06-01")
            newest = seed_transaction(date="2025-12-01")

            selection = select_transactions([_SEEDED, "last", "2"])

            assert selection.txn_ids == [middle, newest]

    def test_bare_first_n_returns_the_earliest(self, temp_ctx: TempContext) -> None:
        """'first N' with no sort returns the N oldest transactions by date."""
        with temp_ctx():
            ensure_data_exists()
            oldest = seed_transaction(date="2025-01-01")
            middle = seed_transaction(date="2025-06-01")
            seed_transaction(date="2025-12-01")

            selection = select_transactions([_SEEDED, "first", "2"])

            assert selection.txn_ids == [oldest, middle]

    def test_explicit_limit_keeps_the_newest_first_display(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """'limit:N' (not `first`/`last`) still caps the plain newest-first order."""
        with temp_ctx():
            ensure_data_exists()
            seed_transaction(date="2025-01-01")
            newest = seed_transaction(date="2025-12-01")
            middle = seed_transaction(date="2025-06-01")

            selection = select_transactions([_SEEDED, "limit:2"])

            assert selection.txn_ids == [newest, middle]


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


class TestTextNumericColumns:
    """Test querying non-default numeric columns stored as text."""

    def test_fee_comparison_is_numeric(self, temp_ctx: TempContext) -> None:
        """`fee<-1` finds the large negative fee, not the one that sorts low."""
        with temp_ctx():
            ensure_data_exists()
            large = seed_transaction(fee="-4.97")
            # Lexicographically '-0.04' < '-1' while '-4.97' is not.
            seed_transaction(fee="-0.04")
            seed_transaction(fee="0.0038")

            selection = select_transactions([_SEEDED, "fee<-1"])

            assert selection.txn_ids == [large]

    def test_fee_sort_is_numeric(self, temp_ctx: TempContext) -> None:
        """Ascending fee order runs -4.97, -0.04, 0.0038, not '-0.04' first."""
        with temp_ctx():
            ensure_data_exists()
            negative = seed_transaction(fee="-4.97")
            small_negative = seed_transaction(fee="-0.04")
            positive = seed_transaction(fee="0.0038")

            selection = select_transactions([_SEEDED, "sort:fee"])

            assert selection.txn_ids == [negative, small_negative, positive]

    def test_fee_equality_ignores_stored_precision(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """`fee:10.0` matches a fee stored as the text '10'."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction(fee="10")

            selection = select_transactions([_SEEDED, "fee:10.0"])

            assert selection.txn_ids == [txn_id]

    def test_non_numeric_fee_value_falls_back_to_text(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """A value that is not a number is compared as text rather than raising."""
        with temp_ctx():
            ensure_data_exists()
            seed_transaction(fee="-4.97")

            selection = select_transactions([_SEEDED, "fee:notanumber"])

            assert selection.transactions.empty

    def test_optional_numeric_column_compares_numerically(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """An optional field declared `numeric` gets the same treatment as Fee."""
        overrides = {
            "optional_columns": {
                "Commission": {"keywords": ["Commission"], "type": "numeric"},
            },
        }
        with temp_ctx(overrides):
            ensure_data_exists()
            large = seed_transaction()
            small = seed_transaction()
            _seed_commissions({large: "-4.97", small: "-0.04"})

            selection = select_transactions([_SEEDED, "commission<-1"])

            assert selection.txn_ids == [large]


def _seed_commissions(values: dict[int, str]) -> None:
    """Add a Commission column the way an import would, and fill it in.

    Args:
        values: TxnId to the commission text stored against it.
    """
    with get_connection() as conn:
        add_column_to_table(conn, Table.TXNS, "Commission", "TEXT")
        update_rows(
            conn,
            Table.TXNS,
            [
                {Column.Txn.TXN_ID: txn_id, "Commission": value}
                for txn_id, value in values.items()
            ],
            where_columns=[Column.Txn.TXN_ID],
            set_columns=["Commission"],
        )


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
