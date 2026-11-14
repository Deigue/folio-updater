"""Rendering what an import did.

The audit view reports: what was read, cancelled, merged, transformed and rejected.
Each part becomes a tile, and one holding rows back can be expanded by keypress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.markup import escape
from rich.table import Table

from domain import (
    TXN_ESSENTIALS,
    Action,
    Column,
    SettlementOutcome,
)
from exporters.sheet_spec import Fmt
from term.console import (
    StatusMark,
    active_console,
    console_panel,
    console_print,
    console_rule,
    get_symbol,
)
from term.keys import getch
from ui.cells import TREE_BRANCH
from ui.format import safe_str
from ui.layout.columns import FormattedColumn
from ui.layout.paging import available_height
from ui.layout.tiles import Tile, TilingLayout
from ui.transaction_table import (
    build_transactions_table,
    page_source_rows,
    page_transactions,
)
from ui.vocabulary import (
    RULE_STATEMENT,
    SETTLEMENT_HEADER,
    SNUG_PADDING,
    STATUS_FAILED,
    STATUS_NO_DATA,
    STATUS_SUCCESS,
    TABLE_BORDER,
    THEME_DUPES,
    THEME_MERGED,
    THEME_REMOVED,
    THEME_SUCCESS,
    THEME_TRANSFORMS,
    TRANSACTION_COLORS,
    TransactionContext,
)
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import pandas as pd
    from rich.console import RenderableType

    from models import (
        CancelEvent,
        FileImport,
        ImportResults,
        MergeEvent,
        SettlementMatch,
        StatementImportResult,
        TransformEvent,
        Txn,
    )

# Minimum columns always shown for invalid rows
_INVALID_BASE_COLUMNS = [Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.AMOUNT]


@dataclass(frozen=True)
class _Expansion:
    """How to see the rest of a section the audit truncated.

    Attributes:
        key: The key that expands it.
        show: Shows every item.
    """

    key: str
    show: Callable[[], None]


@dataclass(frozen=True)
class _AuditSection:
    """One part of the audit: what it shows, and how to see the rest of it.

    Attributes:
        name: What the expansion hint and the expanded view call it. The heading
            on screen, with its count, is part of the tile's content.
        tile: The truncated content, for the layout to place.
        expansion: How to see the rest. None when the tile already shows it all.
    """

    name: str
    tile: Tile
    expansion: _Expansion | None = None


def show_import_summary(
    filename: str,
    results: ImportResults,
) -> None:
    """Display import summary with success/error styling.

    Args:
        filename: Name of the imported file
        results: ImportResults object containing import metrics
    """
    imported_count = results.imported_count()
    total_count = results.final_db_count
    success = imported_count > 0

    if success:
        icon = get_symbol(StatusMark.SUCCESS)
        color = "green"
        status = "SUCCESS"
    else:
        icon = get_symbol(StatusMark.WARNING)
        color = "yellow"
        status = "NO DATA"

    console_print(
        f"{icon}[{color}]{status}[/{color}]: "
        f"[bold]{filename}[/bold] - "
        f"[bright_white]{imported_count}[/bright_white] transactions imported "
        f"([dim]{total_count} total in database[/dim])",
    )


def _count(header: str) -> FormattedColumn:
    """Describe a summary column counting what an import did."""
    return FormattedColumn(header, fmt=Fmt.COUNT, no_wrap=True)


def show_files_summary(outcomes: Sequence[FileImport]) -> None:
    """Tabulate what every file of a directory import did.

    Args:
        outcomes: One entry per file, in the order imported.
    """
    console_rule("Import Summary", style=THEME_SUCCESS)
    show_data_table(
        [
            {
                "File": outcome.path.name,
                "Transactions": outcome.imported,
                "Status": _file_status(outcome),
            }
            for outcome in outcomes
        ],
        title="Import Summary",
        max_rows=20,
        theme=THEME_SUCCESS,
        columns=(_count("Transactions"),),
    )


def _file_status(outcome: FileImport) -> str:
    """Label one file's row in the import summary."""
    if outcome.error is not None:
        return f"[{STATUS_FAILED}]Failed[/{STATUS_FAILED}]"
    if outcome.imported > 0:
        return f"[{STATUS_SUCCESS}]Success[/{STATUS_SUCCESS}]"
    return f"[{STATUS_NO_DATA}]No data[/{STATUS_NO_DATA}]"


def show_statements_summary(
    statements: Sequence[tuple[str, StatementImportResult]],
) -> None:
    """Tabulate what every statement of a directory import changed.

    Args:
        statements: Each statement's file name and what importing it did.
    """
    console_rule(style=RULE_STATEMENT)
    show_data_table(
        [
            {
                "File": name,
                "Confirmed": result.settlements_confirmed,
                "Unplaced": len(result.settlement_unplaced()),
                "Transfers": result.transfers_created(),
                "Rejected": result.transfers_rejected,
                "Status": _statement_status(result),
            }
            for name, result in statements
        ],
        title="Statement Import Summary",
        max_rows=20,
        columns=tuple(
            map(_count, ("Confirmed", "Unplaced", "Transfers", "Rejected")),
        ),
    )


def _statement_status(result: StatementImportResult) -> str:
    """Label one statement's row in the summary table, symbol included."""
    if result.settlements_confirmed > 0 or result.transfers_created() > 0:
        return f"{get_symbol(StatusMark.SUCCESS)}Success"
    if result.settlement_already_confirmed() and not result.settlement_unplaced():
        return f"{get_symbol(StatusMark.INFO)}Up to date"
    return f"{get_symbol(StatusMark.WARNING)}No updates"


def show_settlement_detail(
    filename: str,
    matches: list[SettlementMatch],
) -> None:
    """Show every statement row that was weighed for a settlement date.

    Matched rows carry the transaction they updated. Rows that matched
    nothing, or matched too much to choose, are the ones worth reading:
    without this they are visible only in the audit log.

    Args:
        filename: Name of the statement the rows came from.
        matches: One entry per candidate row, matched or not.
    """
    if not matches:
        return

    table = Table(
        title=f"Settlement Matching - {filename} ({len(matches)})",
        show_header=True,
        header_style="bold",
        border_style=THEME_TRANSFORMS,
        expand=False,
        padding=SNUG_PADDING,
    )
    table.add_column("Result")
    table.add_column(str(Column.Txn.TXN_ID), justify="right")
    table.add_column(str(Column.Txn.TXN_DATE))
    table.add_column(SETTLEMENT_HEADER)
    table.add_column(str(Column.Txn.ACTION))
    table.add_column(str(Column.Txn.TICKER))
    table.add_column(str(Column.Txn.AMOUNT), justify="right")
    table.add_column(str(Column.Txn.ACCOUNT))

    for match in matches:
        table.add_row(
            _settlement_result_label(match),
            str(match.txn_id) if match.txn_id is not None else "",
            match.txn_date,
            match.settle_date,
            safe_str(match.action),
            safe_str(match.ticker),
            safe_str(match.amount),
            safe_str(match.account or ""),
        )

    console_print(table)


def _settlement_result_label(match: SettlementMatch) -> str:
    """Render one settlement outcome, with the candidate count when it helps.

    Args:
        match: The outcome to label.

    Returns:
        Markup for the Result cell.
    """
    if match.outcome is SettlementOutcome.CONFIRMED:
        return (
            f"{get_symbol(StatusMark.SUCCESS)}"
            f"[{THEME_SUCCESS}]confirmed[/{THEME_SUCCESS}]"
        )
    if match.outcome is SettlementOutcome.ALREADY_CONFIRMED:
        return f"{get_symbol(StatusMark.INFO)}[dim]already confirmed[/dim]"
    if match.outcome is SettlementOutcome.AMBIGUOUS:
        return (
            f"{get_symbol(StatusMark.WARNING)}"
            f"[yellow]ambiguous ({match.candidates})[/yellow]"
        )
    return (
        f"{get_symbol(StatusMark.WARNING)}[{THEME_REMOVED}]no match[/{THEME_REMOVED}]"
    )


def show_import_audit(
    results: ImportResults,
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> None:
    """Display rich audit summary for an import operation.

    Args:
        results: ImportResults with audit data.
        verbose: If True, includes the imported transactions section.
        interactive: If True, offers to expand truncated sections by keypress.
            An unattended run passes False and keeps the truncated view.
    """
    _show_stats_panel(results)
    sections = _build_audit_sections(results, verbose=verbose)
    TilingLayout([section.tile for section in sections]).render()

    # Also double check if current console context is interacable
    if interactive and active_console().is_interactive:
        _prompt_and_expand(sections, results)


def _show_stats_panel(results: ImportResults) -> None:
    """Display stats panel in compact mode with color-coding.

    Color scheme:
    - Read: green (adds transactions)
    - Merged: X red (removed) → Y green (added)
    - Rejected: red (removed), as invalid rows and both kinds of duplicate
    - Imported: green if matches tally, red with yellow tally if mismatch
    """
    parts: list[str] = []

    read_count = results.read_count()
    parts.append(f"[bold]Read:[/bold] [green]{read_count}[/green]")

    cancelled = results.cancelled_count()
    if cancelled > 0:
        parts.append(f"[bold]Cancelled:[/bold] [red]{cancelled}[/red]")

    merge_candidates = results.merge_candidates()
    merged_into = results.merged_into()
    if merge_candidates > 0:
        # X red → Y green (no spaces around the arrow, which is unstyled)
        parts.append(
            f"[bold]Merged:[/bold] [red]{merge_candidates}[/red]->"
            f"[green]{merged_into}[/green]",
        )

    rejected = [
        f"[red]{count}[/red] {label}"
        for count, label in (
            (results.invalid_count(), "invalid"),
            (results.intra_rejected_count(), "intra"),
            (results.db_rejected_count(), "db"),
        )
        if count > 0
    ]
    if rejected:
        parts.append(f"[bold]Rejected:[/bold] {', '.join(rejected)}")

    # Import summary with color coding
    imported = results.imported_count()
    expected_imported = results.expected_count()
    if results.tally_matches():
        parts.append(f"[bold]Imported:[/bold] [green]{imported}[/green]")
    else:
        parts.append(
            f"[bold]Imported:[/bold] [red]{imported}[/red] "
            f"[yellow]({expected_imported})[/yellow]",
        )

    stats_text = "  ".join(parts)
    console_panel(stats_text, title="Stats", style=TABLE_BORDER, expand=False)


def _section(  # noqa: PLR0917
    name: str,
    key: str,
    renderable: RenderableType,
    total: int,
    shown: int,
    show: Callable[[], None],
    *,
    is_full_width: bool = False,
) -> _AuditSection:
    """Build a section, offering to expand it only when it holds back some rows.

    Args:
        name: What the expansion hint and the expanded view call it.
        key: The key that expands it.
        renderable: The truncated content to tile.
        total: How many items there are.
        shown: How many of them the renderable shows.
        show: Shows every item.
        is_full_width: Print it below the columns rather than in them.

    Returns:
        The section.
    """
    return _AuditSection(
        name=name,
        tile=Tile(renderable, is_full_width=is_full_width),
        expansion=_Expansion(key, show) if total > shown else None,
    )


def _rows_section(
    name: str,
    key: str,
    rows: pd.DataFrame,
    max_rows: int,
    build: Callable[[pd.DataFrame, str], Table],
    *,
    is_full_width: bool = False,
) -> _AuditSection:
    """Build a section listing DataFrame rows, paged when it is expanded.

    Args:
        name: What the expansion hint and the expanded view call it.
        key: The key that expands it.
        rows: Every row of the section.
        max_rows: How many rows fit before it is truncated.
        build: Builds the table from the rows to show and its title.
        is_full_width: Print it below the columns rather than in them.

    Returns:
        The section.
    """
    total = len(rows)
    shown = min(total, max_rows)
    return _section(
        name,
        key,
        build(rows.head(shown), f"{name} ({total})"),
        total,
        shown,
        lambda: _page_rows(rows, name),
        is_full_width=is_full_width,
    )


def _build_audit_sections(
    results: ImportResults,
    *,
    verbose: bool = False,
) -> list[_AuditSection]:
    """Build the sections of the audit, in the order their hints are offered.

    Args:
        results: ImportResults with audit data.
        verbose: If True, includes the imported transactions section.

    Returns:
        The sections to display.
    """
    sections: list[_AuditSection] = []
    max_rows = available_height()

    if results.merge_events:
        total = len(results.merge_events)
        # For merge events, calculate how many events fit in max_rows
        # accounting for tree structure (1 merged row + N source rows per event)
        events_to_show: list[MergeEvent] = []
        rows_used = 0
        for event in results.merge_events:
            event_rows = 1 + len(event.source_rows)
            if rows_used + event_rows > max_rows and events_to_show:
                break
            events_to_show.append(event)
            rows_used += event_rows
        sections.append(
            _section(
                "Merged",
                "m",
                _merge_table(events_to_show, total),
                total,
                len(events_to_show),
                lambda: _show_in_full(
                    _merge_table(results.merge_events, len(results.merge_events)),
                ),
            ),
        )

    max_rows = available_height(table=True)

    if results.cancel_events:
        # Always shown in full, so there is nothing to expand.
        sections.append(
            _AuditSection("Cancelled", Tile(_cancel_table(results.cancel_events))),
        )

    if results.transform_events:
        total = len(results.transform_events)
        shown = min(total, max_rows)
        sections.append(
            _section(
                "Transforms",
                "t",
                _transform_table(results.transform_events[:shown], total),
                total,
                shown,
                lambda: _show_in_full(
                    _transform_table(
                        results.transform_events,
                        len(results.transform_events),
                    ),
                ),
            ),
        )

    sections.extend(
        _rows_section(name, key, rows, max_rows, build)
        for name, key, rows, build in (
            ("Invalid", "n", results.invalid_df, _invalid_table),
            ("Intra Duplicates", "i", results.intra_rejected_df, _dupes_table),
            ("DB Duplicates", "d", results.db_rejected_df, _dupes_table),
        )
        if not rows.empty
    )

    if verbose and results.stored:
        sections.append(_imported_section(results.stored, max_rows))

    return sections


def _imported_section(stored: list[Txn], max_rows: int) -> _AuditSection:
    """Build the section listing what an import stored, paged when it is expanded."""
    name = "Imported"
    total = len(stored)
    shown = min(total, max_rows)
    table = build_transactions_table(
        stored[:shown],
        f"{name} ({total})",
        shown,
        TransactionContext.IMPORT,
    )

    def show() -> None:
        page_transactions(
            stored,
            title=name,
            page_size=available_height(table=True, pages=True),
            context=TransactionContext.IMPORT,
        )

    return _section(name, "v", table, total, shown, show, is_full_width=True)


def _merge_table(
    events: list[MergeEvent],
    total: int,
) -> Table:
    """Build a table for merge events.

    Starts from parent transaction nodes to save horizontal space.
    """
    table = Table(
        title=f"Merged ({total})",
        show_header=False,
        border_style=THEME_MERGED,
        expand=False,
        box=None,
        padding=SNUG_PADDING,
    )
    table.add_column("tree")
    tree_content: list[str] = []
    for event in events:
        merged = _summary(event.merged_row, Column.Txn.TICKER)
        tree_content.append(f"[green]+ {merged}[/green]")
        for _, src_row in event.source_rows.iterrows():
            source = _summary(src_row, Column.Txn.TICKER)
            tree_content.append(f"  [red]{TREE_BRANCH} {source}[/red]")

    for line in tree_content:
        table.add_row(line)

    return table


def _cancel_table(events: list[CancelEvent]) -> Table:
    """Build a table for cancellations: each cancelling row and what it voided.

    Both rows are left out of the import; a cancellation with nothing in
    the file to void says so.
    """
    table = Table(
        title=f"Cancelled ({len(events)})",
        show_header=False,
        border_style=THEME_REMOVED,
        expand=False,
        box=None,
        padding=SNUG_PADDING,
    )
    table.add_column("tree")
    for event in events:
        table.add_row(
            f"[red]- {_summary(event.cancellation, Column.Txn.ACCOUNT)}[/red]",
        )
        if event.cancelled is None:
            table.add_row(
                f"  [yellow]{TREE_BRANCH} nothing in this file to void[/yellow]",
            )
        else:
            voided = _summary(event.cancelled, Column.Txn.ACCOUNT)
            table.add_row(f"  [red]{TREE_BRANCH} {voided}[/red]")
    return table


def _summary(row: Mapping[str, Any] | pd.Series, last: str) -> str:
    """Render a row as date|action|amount|`last`, for the merge and cancel panels."""
    return "|".join(
        escape(safe_str(row.get(column, "")))
        for column in (
            Column.Txn.TXN_DATE,
            Column.Txn.ACTION,
            Column.Txn.AMOUNT,
            last,
        )
    )


def _transform_table(
    events: list[TransformEvent],
    total: int,
) -> Table:
    """Build a table for transform events.

    Colours the New value by action colour when it is an Action, and limits
    Old/New width to 15.
    """
    table = Table(
        title=f"Transforms ({total})",
        show_header=True,
        header_style="bold",
        border_style=THEME_TRANSFORMS,
        expand=False,
        padding=SNUG_PADDING,
    )
    table.add_column("Field")
    table.add_column("Rows", justify="right", width=4)
    table.add_column("Old", max_width=15, overflow="ellipsis")
    table.add_column("New", max_width=15, overflow="ellipsis")

    for e in events:
        old_val = ",".join(map(str, e.old_values))
        new_val = str(e.new_value)
        # Try to color new_val if it's an Action enum value
        try:
            action = Action(new_val)
            new_val_color = TRANSACTION_COLORS.get(action, "white")
            new_val = f"[{new_val_color}]{new_val}[/{new_val_color}]"
        except ValueError:
            pass
        table.add_row(e.field_name, str(e.row_count), old_val, new_val)

    return table


def _invalid_table(
    df: pd.DataFrame,
    title: str,
) -> Table:
    """Build a table for invalid rows.

    Shows TxnDate, Action, Amount always, plus columns referenced in reason.
    Only show essential columns that are relevant to the rejection reason.
    """
    table = Table(
        title=title,
        show_header=True,
        header_style="bold",
        border_style=THEME_REMOVED,
        expand=False,
        padding=SNUG_PADDING,
    )

    cols_to_show: list[str] = [str(c) for c in _INVALID_BASE_COLUMNS]

    if Column.REJECTION_REASON in df.columns:
        for reason in df[Column.REJECTION_REASON].dropna().unique():
            reason_str = str(reason).upper()
            # Check if reason references a TXN_ESSENTIALS column
            for col in TXN_ESSENTIALS:
                col_str = str(col)
                if col_str.upper() in reason_str and col_str not in cols_to_show:
                    cols_to_show.append(col_str)

        # Always show rejection reason last
        cols_to_show.append(str(Column.REJECTION_REASON))

    for col in cols_to_show:
        max_w = 20 if col == str(Column.REJECTION_REASON) else 12
        table.add_column(str(col), overflow="ellipsis", no_wrap=True, max_width=max_w)

    for _, row in df.iterrows():
        table.add_row(*(safe_str(row.get(c, "")) for c in cols_to_show))

    return table


def _dupes_table(
    df: pd.DataFrame,
    title: str,
) -> Table:
    """Build a table for duplicate transactions.

    Only shows TXN_ESSENTIALS columns used for duplicate detection.
    Never shows SettleCalculated, SettleDate, Description, etc.
    """
    table = Table(
        title=title,
        show_header=True,
        header_style="bold",
        border_style=THEME_DUPES,
        expand=False,
        padding=SNUG_PADDING,
    )

    available_cols = [c for c in TXN_ESSENTIALS if c in df.columns]

    for col in available_cols:
        table.add_column(str(col), overflow="ellipsis", no_wrap=True, max_width=15)

    for _, row in df.iterrows():
        table.add_row(*(safe_str(row.get(c, "")) for c in available_cols))

    return table


def _prompt_and_expand(
    sections: list[_AuditSection],
    results: ImportResults,
) -> None:
    """Show expansion prompt and handle user inputs.

    Args:
        sections: The audit's sections.
        results: ImportResults for redisplay after expansion.
    """
    expansions = {
        s.expansion.key: s.expansion for s in sections if s.expansion is not None
    }
    if not expansions:
        return

    _show_key_hints(sections)

    while True:
        try:
            user_input = getch()
            if user_input == "q":
                break

            expansion = expansions.get(user_input)
            if expansion is not None:
                active_console().clear()
                expansion.show()
                _redisplay_audit(results, sections)
        except (KeyboardInterrupt, EOFError):
            break


def _show_key_hints(sections: list[_AuditSection]) -> None:
    """Display the expansion key hints prompt.

    Args:
        sections: The audit's sections, of which those that expand get a hint.
    """
    key_hints = " ".join(
        f"[bold]{s.expansion.key}[/bold]={s.name}"
        for s in sections
        if s.expansion is not None
    )
    console_print(
        f"\n[dim]Press {key_hints} to expand, [bold]q[/bold] to continue[/dim]",
    )


def _redisplay_audit(
    results: ImportResults,
    sections: list[_AuditSection],
) -> None:
    """Redisplay the audit after returning from an expansion.

    Args:
        results: ImportResults with audit data.
        sections: The audit's sections.
    """
    active_console().clear()
    _show_stats_panel(results)
    TilingLayout([section.tile for section in sections]).render()
    _show_key_hints(sections)


def _show_in_full(table: Table) -> None:
    """Print a table whole, and wait for a key before returning to the audit."""
    console_print(table)
    console_print("\n[dim]Press any key to return...[/dim]")
    getch()


def _page_rows(rows: pd.DataFrame, title: str) -> None:
    """Page through every row of a section."""
    page_source_rows(
        rows,
        title=title,
        page_size=available_height(table=True, pages=True),
        context=TransactionContext.IMPORT,
    )
