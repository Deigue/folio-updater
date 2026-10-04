"""Rendering what a `folio update` run did.

Every stage prints its own output as it runs. This is the condensed account
printed last: what changed in the folio, what needs a human (with the command
for each), what is left for a later run, and, when a stage stopped the run,
how to get going again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

from domain import Column, SettlementOutcome, TransactionContext
from models import UpdateStage
from term import console_print, console_rule, get_symbol
from term.console import console_panel
from ui.format import safe_str
from ui.views.transactions import TransactionDisplay
from ui.vocabulary import THEME_EXCLUDED, THEME_SUCCESS
from ui.widgets import show_data_table

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from models import Concern, UpdateReport

_REPORT_STYLE = "bright_blue"
_CONCERN_STYLE = "yellow"


def show_workbook_link(path: Path) -> None:
    """Print a clickable link to the workbook the run wrote.

    Args:
        path: The workbook written.
    """
    console_print("")
    console_print(
        f"{get_symbol('success')}Folio workbook: "
        f"[link={path.resolve().as_uri()}]{escape(str(path))}[/link]",
    )


def show_update_report(report: UpdateReport) -> None:
    """Print the end-of-run report.

    Args:
        report: What the run did and what it raised.
    """
    console_print("")
    console_rule("Update Report", style=_REPORT_STYLE)
    _show_changes(report)
    _show_concerns(report.concerns)
    _show_pending(report.pending)
    _show_outcome(report)


def _show_changes(report: UpdateReport) -> None:
    """Print the transactions added and the settlement dates confirmed."""
    console_rule("What changed", style=THEME_SUCCESS)
    if report.new_txns.empty:
        console_print(f"{get_symbol('info')}No new transactions.")
    else:
        TransactionDisplay().transactions_table(
            report.new_txns,
            f"New transactions ({len(report.new_txns)})",
            max_rows=len(report.new_txns),
            context=TransactionContext.IMPORT,
        )

    confirmed = [
        match for match in report.settled if match.outcome is SettlementOutcome.MATCHED
    ]
    if confirmed:
        show_data_table(
            [
                {
                    "TxnId": match.txn_id,
                    str(Column.Txn.TXN_DATE): match.txn_date,
                    "Settles": match.settle_date,
                    str(Column.Txn.ACTION): match.action,
                    str(Column.Txn.TICKER): match.ticker,
                    str(Column.Txn.ACCOUNT): match.account or "",
                }
                for match in confirmed
            ],
            title=f"Settlement dates confirmed ({len(confirmed)})",
            max_rows=len(confirmed),
            theme=THEME_SUCCESS,
        )
    else:
        console_print(f"{get_symbol('info')}No settlement dates confirmed.")


def _show_concerns(concerns: list[Concern]) -> None:
    """Print each concern: what it is, the rows involved and how to fix it."""
    if not concerns:
        return
    console_rule(f"Needs a look ({len(concerns)})", style=_CONCERN_STYLE)
    for number, concern in enumerate(concerns, start=1):
        console_print("")
        console_print(
            f"{get_symbol('warning')}[bold {_CONCERN_STYLE}]{number}. "
            f"{escape(f'[{concern.stage}]')} {escape(concern.title)}"
            f"[/bold {_CONCERN_STYLE}]",
        )
        console_print(f"   {escape(concern.why)}")
        _show_concern_rows(concern)
        for detail in concern.details:
            console_print(f"   - {escape(detail)}")
        for command in concern.commands:
            console_print(f"   [cyan]{escape(command)}[/cyan]")


def _show_concern_rows(concern: Concern) -> None:
    """Print a concern's rows: as transactions when they are, else as a table."""
    rows = concern.rows
    if rows.empty:
        return
    # Stored or imported rows carry the transaction columns; summaries built
    # for a concern (transfer pairs, statement rows) do not carry a currency.
    if {Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.CURRENCY} <= set(
        rows.columns,
    ):
        context = (
            TransactionContext.SETTLEMENT
            if concern.stage is UpdateStage.STATEMENTS
            else TransactionContext.IMPORT
        )
        TransactionDisplay().transactions_table(rows, None, len(rows), context)
        return
    show_data_table(
        [
            {str(k): safe_str(v) for k, v in row.items()}
            for row in rows.to_dict("records")
        ],
        max_rows=len(rows),
        theme=THEME_EXCLUDED,
    )


def _show_pending(pending: list[str]) -> None:
    """Print what was expected and needs nothing now."""
    if not pending:
        return
    console_rule("Expected, nothing to do", style="dim")
    for line in pending:
        console_print(f"[dim]  {escape(line)}[/dim]")


def _show_outcome(report: UpdateReport) -> None:
    """Close the report: the failure that stopped the run, or how it ended."""
    console_print("")
    if report.failure is not None:
        failure = report.failure
        console_panel(
            f"[bold]{escape(failure.error)}[/bold]",
            title=f"Update stopped at {failure.stage}",
            style="red",
            expand=False,
        )
        for step in failure.next_steps:
            console_print(_step_line(step))
    elif report.concerns:
        console_print(
            f"{get_symbol('warning')}[{_CONCERN_STYLE}]Update finished with "
            f"{len(report.concerns)} item(s) to look at above.[/{_CONCERN_STYLE}]",
        )
    else:
        console_print(
            f"{get_symbol('success')}[green]Update finished, nothing needs a "
            "look.[/green]",
        )


def _step_line(step: str) -> str:
    """Render one recovery step, setting a command apart so it copies cleanly."""
    if step.startswith("folio "):
        return f"     [cyan]{escape(step)}[/cyan]"
    return f"  {escape(step)}"
