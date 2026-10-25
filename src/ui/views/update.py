"""Rendering what a `folio update` run did.

Every stage prints its own output as it runs. This is the condensed account
printed last: what changed in the folio, what needs a human (with the command
for each), what a later run resolves on its own, what was expected and needs
nothing, and, when a stage stopped the run, how to get going again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

from domain import Column, SettlementOutcome
from models import UpdateStage
from term import StatusMark, console_print, console_rule, get_symbol
from term.console import console_panel
from ui.transaction_table import show_transactions
from ui.vocabulary import (
    RULE_UPDATE,
    SETTLEMENT_HEADER,
    THEME_REMOVED,
    THEME_SUCCESS,
    TransactionContext,
)
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from pathlib import Path

    from models import Concern, UpdateReport

_CONCERN_STYLE = "yellow"


def show_workbook_link(path: Path) -> None:
    """Print a clickable link to the workbook the run wrote.

    Args:
        path: The workbook written.
    """
    console_print("")
    console_print(
        f"{get_symbol(StatusMark.SUCCESS)}Folio workbook: "
        f"[link={path.resolve().as_uri()}]{escape(str(path))}[/link]",
    )


def show_update_report(report: UpdateReport) -> None:
    """Print the end-of-run report.

    Args:
        report: What the run did and what it raised.
    """
    console_print("")
    console_rule("Update Report", style=RULE_UPDATE)
    _show_changes(report)
    _show_concerns(report.concerns)
    _show_notes("Pending, a later update resolves these", report.pending)
    _show_notes("Expected, nothing to do", report.expected)
    _show_outcome(report)


def _show_changes(report: UpdateReport) -> None:
    """Print the transactions added and the settlement dates confirmed."""
    console_rule("What changed", style=THEME_SUCCESS)
    if report.new_txns.empty:
        console_print(f"{get_symbol(StatusMark.INFO)}No new transactions.")
    else:
        show_transactions(
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
                    str(Column.Txn.TXN_ID): match.txn_id,
                    str(Column.Txn.TXN_DATE): match.txn_date,
                    SETTLEMENT_HEADER: match.settle_date,
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
        console_print(f"{get_symbol(StatusMark.INFO)}No settlement dates confirmed.")


def _show_concerns(concerns: list[Concern]) -> None:
    """Print each concern: what it is, the rows involved and how to fix it."""
    if not concerns:
        return
    console_rule(f"Needs a look ({len(concerns)})", style=_CONCERN_STYLE)
    for number, concern in enumerate(concerns, start=1):
        console_print("")
        console_print(
            f"{get_symbol(StatusMark.WARNING)}[bold {_CONCERN_STYLE}]{number}. "
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
        show_transactions(rows, None, len(rows), context)
        return
    show_data_table(
        [
            {str(k): escape(v) if isinstance(v, str) else v for k, v in row.items()}
            for row in rows.to_dict("records")
        ],
        max_rows=len(rows),
        theme=THEME_REMOVED,
    )


def _show_notes(title: str, lines: list[str]) -> None:
    """Print one section of notes that need nothing from the user now."""
    if not lines:
        return
    console_rule(title, style="dim")
    for line in lines:
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
            f"{get_symbol(StatusMark.WARNING)}[{_CONCERN_STYLE}]Update finished with "
            f"{len(report.concerns)} item(s) to look at above.[/{_CONCERN_STYLE}]",
        )
    else:
        console_print(
            f"{get_symbol(StatusMark.SUCCESS)}[green]Update finished, nothing needs a "
            "look.[/green]",
        )


def _step_line(step: str) -> str:
    """Render one recovery step, setting a command apart so it copies cleanly."""
    if step.startswith("folio "):
        return f"     [cyan]{escape(step)}[/cyan]"
    return f"  {escape(step)}"
