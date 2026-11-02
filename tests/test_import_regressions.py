"""Regressions for typed spreadsheet dates and rejected-row audit output."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from db import get_connection
from domain import Column
from importers import import_transactions
from ingest import prepare_transactions
from models import Concern, ImportResults, UpdateStage
from ui.transaction_table import _row_cells
from ui.views.imports import (
    _build_audit_blocks,
    _build_dupes_panel,
    _build_invalid_panel,
    _expand_block,
)
from ui.views.update import _show_concern_rows

from .helpers.console import capture_output

if TYPE_CHECKING:
    from .helpers.context import TempContext


def test_real_excel_date_cells_import_as_text_and_deduplicate(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx({"optional_columns": {"ReviewDate": {"type": "date"}}}) as ctx:
        path = ctx.config.project_root / "real-dates.xlsx"
        frame = pd.DataFrame(
            {
                "TxnDate": pd.to_datetime(["2025-01-02"]),
                "SettleDate": pd.to_datetime(["2025-01-03"]),
                "ReviewDate": pd.to_datetime(["2025-01-06"]),
                "Action": ["BUY"],
                "Amount": [-100],
                "Price": [10],
                "Units": [10],
                "Ticker": ["AAA"],
                "$": ["CAD"],
                "Account": ["TEST-TFSA"],
            },
        )
        frame.to_excel(path, index=False)
        first = import_transactions(path)
        assert first.imported_count() == 1
        for column in ("TxnDate", "SettleDate", "ReviewDate"):
            assert isinstance(first.final_df[column].iloc[0], str)
        with get_connection() as conn:
            stored = conn.execute(
                "SELECT TxnDate, SettleDate, ReviewDate, typeof(TxnDate), "
                "typeof(SettleDate), typeof(ReviewDate) FROM Txns",
            ).fetchone()
        assert tuple(stored) == (
            "2025-01-02",
            "2025-01-03",
            "2025-01-06",
            "text",
            "text",
            "text",
        )
        again = import_transactions(path)
        assert again.imported_count() == 0
        assert len(again.db_rejected_df) == 1


def test_audit_uses_original_labels_after_cancellations_and_rejections(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transforms = {
        "cancellations": [
            {
                "name": "Reversal",
                "conditions": {"Description": ["CANCELLATION"]},
                "match_fields": ["Account", "$", "Action"],
            },
        ],
    }
    frame = pd.DataFrame(
        {
            "TxnDate": ["2025-01-02"] * 4,
            "Action": ["CONTRIBUTION", "CONTRIBUTION", "BUY", "DIV"],
            "Amount": [100, -100, "bad-amount", 5],
            "$": ["CAD"] * 4,
            "Ticker": ["AAA"] * 4,
            "Price": [0, 0, 10, 0],
            "Units": [0, 0, 1, 0],
            "Account": ["CANCEL-ME", "CANCEL-ME", "REJECT-ME", "KEEP-ME"],
            "Description": ["", "CANCELLATION", "", ""],
        },
    )
    with temp_ctx({"transforms": transforms}):
        caplog.set_level(logging.DEBUG, logger="importer")
        result = prepare_transactions(frame)
    assert len(result.cancel_events) == 1
    assert list(result.invalid_df.index) == [2]
    rejects = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith(" - ")
    ]
    assert len(rejects) == 1
    assert "Account=REJECT-ME" in rejects[0]
    assert "bad-amount" in rejects[0]
    assert "3 - Auto-formatted Action: 'DIV' -> 'DIVIDEND'" in caplog.text
    assert "Comparison failed" not in caplog.text


def test_compact_panels_show_ellipsis_and_expanded_invalid_keeps_the_reason() -> None:
    reason = "INVALID Amount: a long explanation with the final detail"
    frame = pd.DataFrame(
        {
            "TxnDate": ["2025-01-02"],
            "Action": ["BUY"],
            "Amount": ["unparseable-amount-with-more-text"],
            Column.REJECTION_REASON: [reason],
        },
    )
    with capture_output(width=160) as captured:
        captured.console.print(_build_invalid_panel(frame, 1))
        captured.console.print(_build_dupes_panel(frame, "Duplicates"))
        compact = captured.get_text()
        assert "…" in compact
        assert "unparseable-amou…" in compact or "unparseable-am…" in compact
        blocks = _build_audit_blocks(ImportResults(invalid_df=frame))
        assert blocks[0].expandable
        _expand_block(blocks[0])
        assert reason in captured.get_text()


@pytest.mark.parametrize("amount", [None, pd.NA, float("nan"), "", "bad[amount]"])
def test_unusable_amount_is_not_displayed_as_zero_in_update_report(
    amount: object,
) -> None:
    frame = pd.DataFrame(
        {
            "TxnDate": ["2025-01-02"],
            "Action": ["BUY"],
            "$": ["CAD"],
            "Amount": [amount],
        },
    )
    cells = _row_cells(frame.iloc[0], [Column.Txn.AMOUNT])
    assert "0.00" not in cells[Column.Txn.AMOUNT]
    with capture_output(width=300) as captured:
        _show_concern_rows(
            Concern(
                stage=UpdateStage.IMPORT,
                title="Invalid",
                why="Bad amount",
                rows=frame,
            ),
        )
    assert "0.00" not in captured.get_text()
    if isinstance(amount, str) and amount:
        assert amount in captured.get_text()


def test_real_zero_amount_is_still_displayed_as_zero() -> None:
    cells = _row_cells(pd.Series({"Action": "BUY", "Amount": 0}), [Column.Txn.AMOUNT])
    assert "0.00" in cells[Column.Txn.AMOUNT]
