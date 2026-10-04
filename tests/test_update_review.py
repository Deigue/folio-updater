"""Tests for folio update's review engine."""

from __future__ import annotations

from datetime import date

import pandas as pd

from domain import Action, CheckStatus, Column, SettlementOutcome
from engine.checks import CheckFinding, CheckResult
from engine.update_review import (
    add_command,
    header_rows,
    odd_merges,
    old_db_dupes,
    review_checks,
    review_import,
    review_late_cancellations,
    review_statement,
    review_stuck,
    review_transfer_pairs,
    review_unpriced,
    split_remaining,
    statement_months,
    transfer_like_pairs,
)
from models import (
    CancelEvent,
    ImportResults,
    MergeEvent,
    SettlementMatch,
    UpdateStage,
)
from tests.helpers.seed import ACCOUNT, TICKER, TSX_TICKER, VENTURE_TICKER

OTHER_ACCOUNT = "OTHERACCT"
CAD = "CAD"
TODAY = date(2026, 10, 3)

# A broker export's header line, as read, and the same line once mapped.
SOURCE_COLUMNS = ["TradeDate", "Buy/Sell", "Proceeds", "CurrencyPrimary", "Symbol"]
HEADER_ROW = {
    Column.Txn.TXN_DATE: "TradeDate",
    Column.Txn.ACTION: "Buy/Sell",
    Column.Txn.AMOUNT: "Proceeds",
    Column.Txn.CURRENCY: "CurrencyPrimary",
    Column.Txn.TICKER: "Symbol",
    Column.REJECTION_REASON: "INVALID TxnDate, INVALID Action",
}
MISSING_AMOUNT_ROW = {
    Column.Txn.TXN_DATE: "2026-09-15",
    Column.Txn.ACTION: Action.DIVIDEND.value,
    Column.Txn.AMOUNT: None,
    Column.Txn.CURRENCY: "CAD",
    Column.Txn.TICKER: TSX_TICKER,
    Column.Txn.ACCOUNT: ACCOUNT,
    Column.REJECTION_REASON: "MISSING Amount",
}
# A withdrawal the broker sent without its amount or currency.
BARE_WITHDRAWAL_ROW = {
    Column.Txn.TXN_DATE: "2026-09-20",
    Column.Txn.ACTION: Action.WITHDRAWAL.value,
    Column.Txn.AMOUNT: None,
    Column.Txn.CURRENCY: None,
    Column.Txn.ACCOUNT: ACCOUNT,
    Column.REJECTION_REASON: "MISSING $, MISSING Amount",
}
# A broker cancelling a deposit, and the deposit, as mapped.
CANCEL_ROW: dict[str, object] = {
    Column.Txn.TXN_DATE: "2026-09-08",
    Column.Txn.ACTION: "Deposits/Withdrawals",
    Column.Txn.AMOUNT: "-13000",
    Column.Txn.CURRENCY: CAD,
    Column.Txn.ACCOUNT: ACCOUNT,
}
DEPOSIT_ROW: dict[str, object] = {
    **CANCEL_ROW,
    Column.Txn.TXN_DATE: "2026-09-07",
    Column.Txn.AMOUNT: "13000",
}


def _txn(
    txn_date: str,
    action: Action,
    amount: float,
    ticker: str = TICKER,
    account: str = ACCOUNT,
    currency: str = "USD",
    txn_id: int | None = None,
) -> dict[str, object]:
    """Build one transaction row."""
    row: dict[str, object] = {
        Column.Txn.TXN_DATE: txn_date,
        Column.Txn.ACTION: action.value,
        Column.Txn.AMOUNT: amount,
        Column.Txn.CURRENCY: currency,
        Column.Txn.PRICE: 10.0,
        Column.Txn.UNITS: 1.0,
        Column.Txn.TICKER: ticker,
        Column.Txn.ACCOUNT: account,
    }
    if txn_id is not None:
        row[Column.Txn.TXN_ID] = txn_id
    return row


def _results(
    *,
    excluded: pd.DataFrame | None = None,
    intra: pd.DataFrame | None = None,
    db: pd.DataFrame | None = None,
    final: pd.DataFrame | None = None,
    merges: list[MergeEvent] | None = None,
    cancels: list[CancelEvent] | None = None,
) -> ImportResults:
    """Build import results whose tally adds up, from the parts given."""
    empty = pd.DataFrame()
    results = ImportResults(
        excluded_df=empty if excluded is None else excluded,
        intra_rejected_df=empty if intra is None else intra,
        db_rejected_df=empty if db is None else db,
        final_df=empty if final is None else final,
        merge_events=merges or [],
        cancel_events=cancels or [],
    )
    # Read exactly as many rows as the stages account for, so the tally balances.
    read = (
        results.imported_count()
        + results.cancelled_count()
        + results.excluded_count()
        + results.intra_rejected_count()
        + results.db_rejected_count()
        + results.merge_candidates()
        - results.merged_into()
    )
    results.read_df = pd.DataFrame(
        [dict.fromkeys(SOURCE_COLUMNS, "")] * read,
        columns=SOURCE_COLUMNS,
    )
    return results


def _merge(merged_amount: float, sources: list[tuple[str, float]]) -> MergeEvent:
    """Build a merge event from (action, amount) source rows."""
    return MergeEvent(
        merged_row={
            Column.Txn.TXN_DATE: "2026-09-10",
            Column.Txn.ACTION: Action.DIVIDEND.value,
            Column.Txn.AMOUNT: merged_amount,
            Column.Txn.TICKER: TICKER,
        },
        source_rows=pd.DataFrame(
            [
                {
                    Column.Txn.TXN_DATE: "2026-09-10",
                    Column.Txn.ACTION: action,
                    Column.Txn.AMOUNT: amount,
                    Column.Txn.TICKER: TICKER,
                }
                for action, amount in sources
            ],
        ),
    )


class TestAddCommand:
    """The `folio add` suggested for a row that did not make it in."""

    def test_fills_present_values_and_placeholders(self) -> None:
        """Present values are filled in; missing ones become placeholders."""
        command = add_command(pd.Series(MISSING_AMOUNT_ROW))
        assert command.startswith("folio add -t DIVIDEND -d 2026-09-15")
        assert f"-a {ACCOUNT}" in command
        assert f"-s {TSX_TICKER}" in command
        assert "-m <amount>" in command
        assert "--force" not in command

    def test_leaves_out_optional_fields_it_lacks(self) -> None:
        """An FXT stored without a ticker is added back without one."""
        row = pd.Series(
            {**_txn("2026-09-15", Action.FXT, -1.0), Column.Txn.TICKER: None},
        )
        command = add_command(row, force=True)
        assert " -s " not in command
        assert "<" not in command

    def test_force_and_quoting(self) -> None:
        """A value with spaces is quoted, and force appends --force."""
        row = pd.Series({**MISSING_AMOUNT_ROW, Column.Txn.ACCOUNT: "MY ACCT"})
        command = add_command(row, force=True)
        assert '-a "MY ACCT"' in command
        assert command.endswith("--force")


class TestImportReview:
    """Concerns raised from one file's import results."""

    def test_header_rows_are_noise(self) -> None:
        """Repeated header lines are counted, never raised as concerns."""
        excluded = pd.DataFrame([HEADER_ROW, HEADER_ROW])
        concerns, noise = review_import("trades.csv", _results(excluded=excluded), None)
        assert concerns == []
        assert noise == ["trades.csv: 2 repeated header row(s)"]

    def test_header_mask_spares_real_rows(self) -> None:
        """A row with real values is not mistaken for a header."""
        excluded = pd.DataFrame([HEADER_ROW, MISSING_AMOUNT_ROW])
        mask = header_rows(excluded, SOURCE_COLUMNS)
        assert mask.tolist() == [True, False]

    def test_header_mask_of_nothing(self) -> None:
        """No exclusions means no header rows."""
        assert header_rows(pd.DataFrame(), SOURCE_COLUMNS).empty

    def test_real_exclusion_is_a_concern(self) -> None:
        """An excluded transaction is raised, with its reason and the add for it."""
        excluded = pd.DataFrame([HEADER_ROW, BARE_WITHDRAWAL_ROW])
        concerns, _ = review_import("ws.csv", _results(excluded=excluded), None)
        assert len(concerns) == 1
        concern = concerns[0]
        assert concern.stage is UpdateStage.IMPORT
        assert concern.details == (
            f"2026-09-20 WITHDRAWAL {ACCOUNT}: MISSING $, MISSING Amount",
        )
        assert "-c <currency> -m <amount>" in concern.commands[0]
        assert concern.commands[0].startswith("folio add -t WITHDRAWAL")

    def test_unpaid_dividend_is_expected(self) -> None:
        """A dividend listed before it pays is noted, not raised."""
        excluded = pd.DataFrame([MISSING_AMOUNT_ROW])
        concerns, noise = review_import("ws.csv", _results(excluded=excluded), None)
        assert concerns == []
        unpaid = f"2026-09-15 DIVIDEND {TSX_TICKER} CAD {ACCOUNT}"
        assert noise == [f"Dividend announced, not paid yet: {unpaid}"]

    def test_paired_cancellation_is_expected(self) -> None:
        """A cancellation that voided its row in the file is noted, not raised."""
        event = CancelEvent(cancellation=CANCEL_ROW, cancelled=DEPOSIT_ROW)
        concerns, noise = review_import("cash.csv", _results(cancels=[event]), None)
        assert concerns == []
        voided = f"2026-09-07 Deposits/Withdrawals 13000 CAD {ACCOUNT}"
        assert noise == [f"cash.csv: cancelled by the broker, both left out: {voided}"]

    def test_intra_duplicates_are_a_concern(self) -> None:
        """Every dropped copy gets a forced add."""
        intra = pd.DataFrame([_txn("2026-09-02", Action.FXT, -100.0)] * 2)
        concerns, _ = review_import("trades.csv", _results(intra=intra), None)
        assert len(concerns) == 1
        assert len(concerns[0].commands) == 2
        assert all(c.endswith("--force") for c in concerns[0].commands)

    def test_resume_day_duplicates_are_noise(self) -> None:
        """Duplicates on or after the resume day are expected."""
        dupes = pd.DataFrame([_txn("2026-09-02", Action.BUY, -50.0)])
        concerns, noise = review_import(
            "trades.csv",
            _results(db=dupes),
            "2026-09-02",
        )
        assert concerns == []
        assert noise == ["trades.csv: 1 row(s) re-sent for the resume day"]

    def test_older_duplicates_are_a_concern(self) -> None:
        """A duplicate from before the resume day means an overlapping file."""
        dupes = pd.DataFrame(
            [
                _txn("2026-08-01", Action.BUY, -50.0),
                _txn("2026-09-02", Action.BUY, -60.0),
            ],
        )
        concerns, noise = review_import(
            "trades.csv",
            _results(db=dupes),
            "2026-09-02",
        )
        assert len(concerns) == 1
        assert len(concerns[0].rows) == 1
        assert len(noise) == 1

    def test_without_resume_date_all_duplicates_are_old(self) -> None:
        """A file that is not a broker download has no expected overlap."""
        dupes = pd.DataFrame([_txn("2026-09-02", Action.BUY, -50.0)])
        assert len(old_db_dupes(dupes, None)) == 1

    def test_tally_mismatch_is_a_concern(self) -> None:
        """Rows unaccounted for by any stage are raised."""
        results = _results(final=pd.DataFrame([_txn("2026-09-02", Action.BUY, -1.0)]))
        results.final_df = pd.DataFrame()
        concerns, _ = review_import("trades.csv", results, None)
        assert [c.title for c in concerns] == [
            "Import tally does not add up for trades.csv",
        ]

    def test_clean_import_raises_nothing(self) -> None:
        """An import with nothing unusual reports nothing."""
        final = pd.DataFrame([_txn("2026-09-02", Action.BUY, -1.0)])
        assert review_import("trades.csv", _results(final=final), None) == ([], [])


class TestMerges:
    """Merges whose shape suggests the wrong rows were combined."""

    def test_ordinary_merge_is_fine(self) -> None:
        """A dividend and its withholding tax merge without comment."""
        event = _merge(8.5, [("Dividends", 10.0), ("Withholding Tax", -1.5)])
        assert odd_merges([event]) == []

    def test_repeated_action_is_odd(self) -> None:
        """Two dividends in one group means a correction was folded in."""
        event = _merge(
            8.5,
            [("Dividends", 10.0), ("Dividends", -10.0), ("Dividends", 10.0)],
        )
        assert odd_merges([event]) == [event]

    def test_sign_flip_is_odd(self) -> None:
        """A deduction outweighing the payment flips the merged sign."""
        event = _merge(-1.5, [("Dividends", 1.0), ("Withholding Tax", -2.5)])
        assert odd_merges([event]) == [event]

    def test_odd_merge_is_a_concern(self) -> None:
        """The concern lists every source row and a query to find it."""
        event = _merge(-1.5, [("Dividends", 1.0), ("Withholding Tax", -2.5)])
        concerns, _ = review_import("cash.csv", _results(merges=[event]), None)
        assert len(concerns) == 1
        assert len(concerns[0].rows) == 2
        assert concerns[0].commands == (f"folio query {TICKER} 2026-09-10",)


class TestTransferPairs:
    """Withdrawal/contribution pairs that look like internal transfers."""

    def _txns(
        self,
        in_account: str = OTHER_ACCOUNT,
        in_date: str = "2026-09-03",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                _txn("2026-09-01", Action.WITHDRAWAL, -2600.0, "", ACCOUNT, CAD, 1),
                _txn(in_date, Action.CONTRIBUTION, 2600.0, "", in_account, CAD, 2),
                _txn(
                    "2026-09-01",
                    Action.CONTRIBUTION,
                    999.0,
                    "",
                    OTHER_ACCOUNT,
                    CAD,
                    3,
                ),
            ],
        )

    def test_pair_across_accounts(self) -> None:
        """Same amount, different account, within the window, one leg new."""
        pairs = transfer_like_pairs(self._txns(), new_ids=[2])
        assert pairs[["Out", "In"]].to_numpy().tolist() == [[1, 2]]

    def test_same_account_is_not_a_pair(self) -> None:
        """Money out and back into the same account is not a transfer."""
        assert transfer_like_pairs(self._txns(in_account=ACCOUNT), [2]).empty

    def test_outside_window_is_not_a_pair(self) -> None:
        """Legs too far apart are unrelated."""
        assert transfer_like_pairs(self._txns(in_date="2026-09-20"), [2]).empty

    def test_other_currency_is_not_a_pair(self) -> None:
        """Legs in different currencies never pair."""
        txns = self._txns()
        txns.loc[txns[Column.Txn.TXN_ID] != 1, Column.Txn.CURRENCY] = "USD"
        assert transfer_like_pairs(txns, [1]).empty

    def test_old_pairs_are_left_alone(self) -> None:
        """Pairs with no leg from this run are not raised again."""
        assert transfer_like_pairs(self._txns(), new_ids=[3]).empty
        assert transfer_like_pairs(self._txns(), new_ids=[]).empty

    def test_concern_relabels_both_legs(self) -> None:
        """The concern names an edit for each leg."""
        concern = review_transfer_pairs(self._txns(), [1])
        assert concern is not None
        assert concern.commands == (
            "folio edit 1 --set Action=TFR_OUT",
            "folio edit 2 --set Action=TFR_IN",
        )
        assert review_transfer_pairs(self._txns(), []) is None


class TestStatements:
    """Choosing statement months and reviewing what statements left behind."""

    def test_only_finished_months(self) -> None:
        """The current month and later are never asked for; junk is skipped."""
        dates = ["2026-08-29", "2026-08-31", "2026-09-30", "2026-10-01", "nope"]
        assert statement_months(dates, TODAY) == ["2026-08", "2026-09"]

    def test_split_remaining(self) -> None:
        """Dates in a month already on hand are stuck; the rest are pending."""
        calculated = pd.DataFrame(
            {
                Column.Txn.TXN_ID: [1, 2, 3],
                Column.Txn.SETTLE_DATE: ["2026-08-04", "2026-09-04", "2026-10-02"],
            },
        )
        stuck, pending = split_remaining(calculated, {"2026-08", "2026-10"}, TODAY)
        assert stuck[Column.Txn.TXN_ID].tolist() == [1]
        assert pending[Column.Txn.TXN_ID].tolist() == [2, 3]

    def test_split_nothing(self) -> None:
        """No calculated dates leaves nothing stuck or pending."""
        stuck, pending = split_remaining(pd.DataFrame(), set(), TODAY)
        assert stuck.empty
        assert pending.empty

    def test_stuck_concern(self) -> None:
        """Each stuck date gets an edit naming its TxnId."""
        stuck = pd.DataFrame({Column.Txn.TXN_ID: [7]})
        concern = review_stuck(stuck)
        assert concern is not None
        assert concern.commands == ("folio edit 7 --set SettleDate=<date>",)
        assert review_stuck(pd.DataFrame()) is None

    def test_unplaced_rows_and_rejected_transfers(self) -> None:
        """Unmatched and ambiguous rows are raised; matched ones are not."""

        def match(outcome: SettlementOutcome, ticker: str) -> SettlementMatch:
            return SettlementMatch(
                outcome=outcome,
                settle_date="2026-09-04",
                txn_date="2026-09-03",
                action="BUY",
                ticker=ticker,
                currency="CAD",
                amount=100.0,
                candidates=2 if outcome is SettlementOutcome.AMBIGUOUS else 0,
            )

        matches = [
            match(SettlementOutcome.MATCHED, TICKER),
            match(SettlementOutcome.ALREADY_SETTLED, TICKER),
            match(SettlementOutcome.UNMATCHED, VENTURE_TICKER),
            match(SettlementOutcome.AMBIGUOUS, TSX_TICKER),
        ]
        concerns = review_statement("ws_statement_X_202609.csv", matches, 1)
        unplaced, rejected = concerns
        assert unplaced.rows["Result"].tolist() == ["no match", "ambiguous (2)"]
        assert unplaced.commands[0] == f"folio query {VENTURE_TICKER} 2026-09-03"
        assert unplaced.commands[1] == "folio edit <TxnId> --set SettleDate=2026-09-04"
        assert rejected.title.startswith("1 transfer(s)")

    def test_clean_statement(self) -> None:
        """A statement that placed everything raises nothing."""
        assert review_statement("s.csv", [], 0) == []


class TestChecksAndPrices:
    """Health checks and unpriced holdings."""

    def test_failing_checks_are_concerns(self) -> None:
        """FAIL raises with its findings; WARN is only counted."""
        results = [
            CheckResult(
                "Units",
                "unit-balances",
                CheckStatus.FAIL,
                "1 oversold",
                (CheckFinding(subject=TICKER, detail="sold more than held"),),
            ),
            CheckResult("Fees", "fee-conventions", CheckStatus.WARN, "odd"),
            CheckResult("Cash", "cash-balances", CheckStatus.OK, "fine"),
        ]
        concerns, warned = review_checks(results)
        assert warned == 1
        assert len(concerns) == 1
        assert concerns[0].commands == ("folio check --only unit-balances",)
        assert concerns[0].details == (f"{TICKER}: sold more than held",)

    def test_unpriced_holdings(self) -> None:
        """Each unpriced holding is named once."""
        concern = review_unpriced([VENTURE_TICKER, VENTURE_TICKER])
        assert concern is not None
        assert concern.title.endswith(VENTURE_TICKER)
        assert f"folio ticker {VENTURE_TICKER}" in concern.commands
        assert review_unpriced([]) is None


class TestLateCancellations:
    """Cancellations whose voided row was imported by an earlier run."""

    STORED = pd.DataFrame(
        [
            _txn("2026-08-01", Action.CONTRIBUTION, 13000.0, "", ACCOUNT, CAD, 5),
            _txn("2026-09-07", Action.CONTRIBUTION, 13000.0, "", ACCOUNT, CAD, 9),
            _txn(
                "2026-09-07",
                Action.CONTRIBUTION,
                13000.0,
                "",
                OTHER_ACCOUNT,
                CAD,
                10,
            ),
        ],
    )

    def test_offers_the_stored_row(self) -> None:
        """Only a recent stored row of the same account and currency is offered."""
        concerns = review_late_cancellations(
            "cash.csv",
            [CancelEvent(cancellation=CANCEL_ROW)],
            self.STORED,
        )
        assert [c.commands for c in concerns] == [("folio delete 9",)]

    def test_nothing_stored_to_void(self) -> None:
        """With nothing to offer, the concern says to check the broker."""
        concerns = review_late_cancellations(
            "cash.csv",
            [
                CancelEvent(cancellation=CANCEL_ROW),
                CancelEvent(cancellation=CANCEL_ROW, cancelled=DEPOSIT_ROW),
            ],
            self.STORED.iloc[0:0],
        )
        assert len(concerns) == 1
        assert concerns[0].commands == ()
        assert "neither in this file nor in the folio" in concerns[0].why
