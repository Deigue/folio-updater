"""Tests for the one duplicate check."""

from __future__ import annotations

from decimal import Decimal

from domain import Action, Currency
from ingest.dedupe import find_duplicates
from models import NewTxn, Txn, txns_frame

_DATE = "2024-03-11"
_SETTLED = "2024-03-12"


def _stored(txn_id: int, **changes: object) -> Txn:
    """Build a stored purchase."""
    fields: dict[str, object] = {
        "txn_date": _DATE,
        "action": Action.BUY,
        "amount": Decimal("-427.30"),
        "currency": Currency.USD,
        "price": Decimal("42.73"),
        "units": Decimal(10),
        "fee": None,
        "ticker": "MSFT",
        "account": "QX-RRSP",
        "settle_date": _SETTLED,
        "settle_calculated": True,
    }
    return NewTxn(**{**fields, **changes}).stored_as(txn_id)  # ty: ignore[invalid-argument-type]


class TestWithinTheBatch:
    """Rows of one batch matching each other."""

    def test_every_copy_is_a_duplicate(self) -> None:
        """Both copies are flagged, the first included, the odd one out is not."""
        rows = txns_frame([_stored(1), _stored(2), _stored(3, ticker="SPY")])

        duplicates = find_duplicates(rows)

        assert duplicates.within.tolist() == [True, True, False]
        assert not duplicates.of_stored.any()

    def test_a_fee_does_not_tell_two_rows_apart(self) -> None:
        """Only the essential fields count, so a different fee is still a match."""
        rows = txns_frame([_stored(1), _stored(2, fee=Decimal("4.95"))])

        assert find_duplicates(rows).within.all()


class TestAgainstStored:
    """Rows matching transactions the folio already holds."""

    def test_names_the_stored_transactions_it_matches(self) -> None:
        """A match flags the row and returns the stored transaction behind it."""
        clash = _stored(7)
        other = _stored(8, ticker="SPY")
        rows = txns_frame([_stored(20), _stored(21, units=Decimal(11))])

        duplicates = find_duplicates(rows, [clash, other])

        assert duplicates.of_stored.tolist() == [True, False]
        assert duplicates.matches == [clash]
        assert not duplicates.within.any()

    def test_nothing_stored_matches_nothing(self) -> None:
        """With no stored transactions there is nothing to match."""
        duplicates = find_duplicates(txns_frame([_stored(1)]))

        assert duplicates.matches == []
        assert not duplicates.of_stored.any()
