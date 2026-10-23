"""Rendering what an import did.

The audit view reports: what was read, cancelled, merged, transformed and rejected.
Panels are measured into blocks and tiled, and each can be expanded by keypress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.markup import escape
from rich.table import Table

from domain import (
    TXN_ESSENTIALS,
    Action,
    Column,
    SettlementOutcome,
    TransactionContext,
)
from term.console import (
    active_console,
    console_panel,
    console_print,
    console_rule,
    get_symbol,
)
from term.keys import getch
from term.size import available_height
from ui.format import safe_str
from ui.layout.tiles import Block, TilingLayout
from ui.transaction_table import build_transactions_table, page_transactions
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
)
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pandas as pd

    from models import (
        CancelEvent,
        FileImport,
        ImportResults,
        MergeEvent,
        SettlementMatch,
        StatementImportResult,
        TransformEvent,
    )

# Minimum columns always shown for invalid rows
_INVALID_BASE_COLUMNS = [Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.AMOUNT]


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
        icon = get_symbol("success")
        color = "green"
        status = "SUCCESS"
    else:
        icon = get_symbol("warning")
        color = "yellow"
        status = "NO DATA"

    console_print(
        f"{icon}[{color}]{status}[/{color}]: "
        f"[bold]{filename}[/bold] - "
        f"[bright_white]{imported_count}[/bright_white] transactions imported "
        f"([dim]{total_count} total in database[/dim])",
    )


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
                "Settle Updates": result.settlement_updates,
                "Unplaced": len(result.settlement_unplaced()),
                "Transfers": result.transfers_created(),
                "Rejected": result.transfers_rejected,
                "Status": _statement_status(result),
            }
            for name, result in statements
        ],
        title="Statement Import Summary",
        max_rows=20,
    )


def _statement_status(result: StatementImportResult) -> str:
    """Label one statement's row in the summary table, symbol included."""
    if result.settlement_updates > 0 or result.transfers_created() > 0:
        return f"{get_symbol('success')}Success"
    if result.settlement_already_settled() and not result.settlement_unplaced():
        return f"{get_symbol('info')}Up to date"
    return f"{get_symbol('warning')}No updates"


def show_settlement_detail(
    filename: str,
    matches: list[SettlementMatch],
) -> None:
    """Show every statement row that was weighed for a settlement date.

    Matched rows carry the transaction they updated. Rows that matched
    nothing, or matched too much to choose, are the ones worth reading:
    without this they are visible only in the importer log.

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
    if match.outcome is SettlementOutcome.MATCHED:
        return f"{get_symbol('success')}[{THEME_SUCCESS}]matched[/{THEME_SUCCESS}]"
    if match.outcome is SettlementOutcome.ALREADY_SETTLED:
        return f"{get_symbol('info')}[dim]settled[/dim]"
    if match.outcome is SettlementOutcome.AMBIGUOUS:
        return f"{get_symbol('warning')}[yellow]ambiguous ({match.candidates})[/yellow]"
    return f"{get_symbol('warning')}[{THEME_REMOVED}]no match[/{THEME_REMOVED}]"


def show_import_audit(
    results: ImportResults,
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> None:
    """Display rich audit summary for an import operation.

    Args:
        results: ImportResults with audit data.
        verbose: If True, includes imported transactions block.
        interactive: If True, offers to expand truncated blocks by keypress.
            An unattended run passes False and keeps the truncated view.
    """
    _show_stats_panel(results)
    blocks = _build_audit_blocks(results, verbose=verbose)

    if blocks:
        layout = TilingLayout(blocks)
        layout.render()

    if interactive:
        _prompt_and_expand_blocks(blocks, results)


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


def _build_audit_blocks(
    results: ImportResults,
    *,
    verbose: bool = False,
) -> list[Block]:
    """Build Blocks for displaying audit information.

    Args:
        results: ImportResults with audit data.
        verbose: If True, includes imported transactions block.

    Returns:
        List of Blocks to display.
    """
    blocks: list[Block] = []
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
        shown = len(events_to_show)
        panel = _build_merge_panel(events_to_show, total)
        blocks.append(
            Block.create(
                name="Merged",
                key="m",
                panel=panel,
                total=total,
                shown=shown,
                data_type="merge",
                data=results.merge_events,
            ),
        )

    max_rows = available_height(table=True)

    blocks.extend(_cancel_blocks(results.cancel_events))

    if results.transform_events:
        total = len(results.transform_events)
        shown = min(total, max_rows)
        panel = _build_transform_panel(
            results.transform_events[:shown],
            total,
        )
        blocks.append(
            Block.create(
                name="Transforms",
                key="t",
                panel=panel,
                total=total,
                shown=shown,
                data_type="transform",
                data=results.transform_events,
            ),
        )

    if not results.invalid_df.empty:
        total = len(results.invalid_df)
        shown = min(total, max_rows)
        panel = _build_invalid_panel(
            results.invalid_df.head(shown),
            total,
        )
        blocks.append(
            Block.create(
                name="Invalid",
                key="n",
                panel=panel,
                total=total,
                shown=shown,
                data_type="invalid",
                data=results.invalid_df,
            ),
        )

    if not results.intra_rejected_df.empty:
        total = len(results.intra_rejected_df)
        shown = min(total, max_rows)
        panel = _build_dupes_panel(
            results.intra_rejected_df.head(shown),
            f"Intra Duplicates ({total})",
        )
        blocks.append(
            Block.create(
                name="Intra Duplicates",
                key="i",
                panel=panel,
                total=total,
                shown=shown,
                data_type="dupes",
                data=results.intra_rejected_df,
            ),
        )

    if not results.db_rejected_df.empty:
        total = len(results.db_rejected_df)
        shown = min(total, max_rows)
        panel = _build_dupes_panel(
            results.db_rejected_df.head(shown),
            f"DB Duplicates ({total})",
        )
        blocks.append(
            Block.create(
                name="DB Duplicates",
                key="d",
                panel=panel,
                total=total,
                shown=shown,
                data_type="dupes",
                data=results.db_rejected_df,
            ),
        )

    if verbose and not results.final_df.empty:
        total = len(results.final_df)
        shown = min(total, max_rows)
        panel = build_transactions_table(
            results.final_df.head(shown),
            f"Imported ({total})",
            shown,
            TransactionContext.IMPORT,
        )
        blocks.append(
            Block.create(
                name="Imported",
                key="v",
                panel=panel,
                total=total,
                shown=shown,
                data_type="dataframe",
                data=results.final_df,
                full_width=True,
            ),
        )

    return blocks


def _build_merge_panel(
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
            tree_content.append(f"  [red]└ {source}[/red]")

    for line in tree_content:
        table.add_row(line)

    return table


def _cancel_blocks(events: list[CancelEvent]) -> list[Block]:
    """Build the block for cancellations, always shown in full, if any."""
    if not events:
        return []
    return [
        Block.create(
            name="Cancelled",
            key="c",
            panel=_build_cancel_panel(events),
            total=len(events),
            shown=len(events),
            data_type="cancel",
            data=events,
        ),
    ]


def _build_cancel_panel(events: list[CancelEvent]) -> Table:
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
            table.add_row("  [yellow]└ nothing in this file to void[/yellow]")
        else:
            table.add_row(
                f"  [red]└ {_summary(event.cancelled, Column.Txn.ACCOUNT)}[/red]",
            )
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


def _build_transform_panel(
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


def _build_invalid_panel(
    df: pd.DataFrame,
    total: int,
) -> Table:
    """Build a table for invalid rows.

    Shows TxnDate, Action, Amount always, plus columns referenced in reason.
    Only show essential columns that are relevant to the rejection reason.
    """
    table = Table(
        title=f"Invalid ({total})",
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
        table.add_column(str(col), overflow="ellipsis", max_width=max_w)

    for _, row in df.iterrows():
        row_vals = [safe_str(row.get(c, ""))[:15] for c in cols_to_show]
        table.add_row(*row_vals)

    return table


def _build_dupes_panel(
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
        table.add_column(str(col), overflow="ellipsis", max_width=15)

    for _, row in df.iterrows():
        row_vals = [safe_str(row.get(c, ""))[:15] for c in available_cols]
        table.add_row(*row_vals)

    return table


def _prompt_and_expand_blocks(
    blocks: list[Block],
    results: ImportResults,
) -> None:
    """Show expansion prompt and handle user inputs.

    Args:
        blocks: List of Blocks that are available.
        results: ImportResults for redisplay after expansion.
    """
    expandable = [b for b in blocks if b.expandable]
    if not expandable:
        return

    _show_block_key_hints(expandable)

    while True:
        try:
            user_input = getch()
            if user_input == "q":
                break

            for block in expandable:
                if user_input == block.key:
                    _expand_block(block)
                    _redisplay_audit_blocks(results, blocks)
                    break
        except (KeyboardInterrupt, EOFError):
            break


def _show_block_key_hints(expandable: list[Block]) -> None:
    """Display the expansion key hints prompt for blocks.

    Args:
        expandable: List of expandable blocks.
    """
    key_hints = " ".join(f"[bold]{b.key}[/bold]={b.name}" for b in expandable)
    console_print(
        f"\n[dim]Press {key_hints} to expand, [bold]q[/bold] to continue[/dim]",
    )


def _redisplay_audit_blocks(
    results: ImportResults,
    blocks: list[Block],
) -> None:
    """Redisplay the audit panel after returning from expansion.

    Args:
        results: ImportResults with audit data.
        blocks: List of Blocks to display.
    """
    active_console().clear()
    _show_stats_panel(results)
    if blocks:
        layout = TilingLayout(blocks)
        layout.render()

    expandable = [b for b in blocks if b.expandable]
    if expandable:
        _show_block_key_hints(expandable)


def _expand_block(block: Block) -> None:
    """Expand a block to show full data with pagination.

    Args:
        block: The Block to expand.
    """
    active_console().clear()

    if block.data_type == "merge":
        panel = _build_merge_panel(block.data, block.total)
        console_print(panel)
        console_print("\n[dim]Press any key to return...[/dim]")
        getch()

    elif block.data_type == "transform":
        panel = _build_transform_panel(block.data, block.total)
        console_print(panel)
        console_print("\n[dim]Press any key to return...[/dim]")
        getch()

    elif block.data_type in ("invalid", "dupes", "dataframe"):
        rows = available_height(table=True, pages=True)
        page_transactions(
            block.data,
            title=block.name,
            page_size=rows,
            context=TransactionContext.IMPORT,
        )
