"""Rendering what an import did.

The audit view reports: what was read, merged, transformed, excluded and rejected.
Panels are measured into blocks and tiled, and each can be expanded by keypress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table

from ui.console import active_console, console_panel, console_print, get_symbol
from ui.format import safe_str
from ui.keys import getch
from ui.layout.terminal import available_height
from ui.layout.tiles import Block, TilingLayout
from ui.theme import (
    SNUG_PADDING,
    THEME_DUPES,
    THEME_EXCLUDED,
    THEME_MERGED,
    THEME_TRANSFORMS,
    TRANSACTION_COLORS,
)
from ui.views.transactions import TransactionDisplay, page_transactions
from utils import TXN_ESSENTIALS, Action, Column, TransactionContext

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

    from models import ImportResults

# Minimum columns always shown for exclusions
EXCLUSION_BASE_COLUMNS = [Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.AMOUNT]


class ImportDisplay:
    """Rich display for an import run: its stats panel and audit blocks."""

    def __init__(self) -> None:
        """Hold a transaction renderer for the blocks that show rows."""
        self._txns = TransactionDisplay()

    def show_import_summary(
        self,
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

    def show_import_audit(
        self,
        results: ImportResults,
        *,
        verbose: bool = False,
    ) -> None:
        """Display rich audit summary for an import operation.

        Args:
            results: ImportResults with audit data.
            verbose: If True, includes imported transactions block.
        """
        self._show_stats_panel(results)
        blocks = self._build_audit_blocks(results, verbose=verbose)

        if blocks:
            layout = TilingLayout(blocks)
            layout.render()

        # Handle expandable blocks if needed.
        self._prompt_and_expand_blocks(blocks, results)

    def _show_stats_panel(self, results: ImportResults) -> None:
        """Display stats panel in compact mode with color-coding.

        Color scheme:
        - Read: green (adds transactions)
        - Merged: X red (removed) → Y green (added)
        - Excluded: red (removed)
        - Dupes Rejected: red (removed)
        - Imported: green if matches tally, red with yellow tally if mismatch
        """
        parts: list[str] = []

        read_count = results.read_count()
        parts.append(f"[bold]Read:[/bold] [green]{read_count}[/green]")

        merge_candidates = results.merge_candidates()
        merged_into = results.merged_into()
        if merge_candidates > 0:
            # X red → Y green (no spaces around arrow, arrow in white)
            parts.append(
                f"[bold]Merged:[/bold] [red]{merge_candidates}[/red]->"
                f"[green]{merged_into}[/green]",
            )

        excluded = results.excluded_count()
        if excluded > 0:
            parts.append(f"[bold]Excluded:[/bold] [red]{excluded}[/red]")

        intra = results.intra_rejected_count()
        db = results.db_rejected_count()
        if intra > 0 or db > 0:
            dupe_parts = []
            if intra > 0:
                dupe_parts.append(f"[red]{intra}[/red] intra")
            if db > 0:
                dupe_parts.append(f"[red]{db}[/red] db")
            parts.append(f"[bold]Dupes Rejected:[/bold] {', '.join(dupe_parts)}")

        # Calculate expected tally (net change from merges + exclusions)
        merge_delta = merged_into - merge_candidates
        expected_imported = read_count + merge_delta - excluded - intra - db

        # Import summary with color coding
        imported = results.imported_count()
        if imported == expected_imported:
            parts.append(f"[bold]Imported:[/bold] [green]{imported}[/green]")
        else:
            parts.append(
                f"[bold]Imported:[/bold] [red]{imported}[/red] "
                f"[yellow]({expected_imported})[/yellow]",
            )

        stats_text = "  ".join(parts)
        console_panel(stats_text, title="Stats", style="bright_blue", expand=False)

    def _build_audit_blocks(
        self,
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
            events_to_show: list[Any] = []
            rows_used = 0
            for event in results.merge_events:
                event_rows = 1 + len(event.source_rows)
                if rows_used + event_rows > max_rows and events_to_show:
                    break
                events_to_show.append(event)
                rows_used += event_rows
            shown = len(events_to_show)
            panel = self._build_merge_panel(events_to_show, total)
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

        if results.transform_events:
            total = len(results.transform_events)
            shown = min(total, max_rows)
            panel = self._build_transform_panel(
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

        if not results.excluded_df.empty:
            total = len(results.excluded_df)
            shown = min(total, max_rows)
            panel = self._build_excluded_panel(
                results.excluded_df.head(shown),
                total,
            )
            blocks.append(
                Block.create(
                    name="Excluded",
                    key="e",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="excluded",
                    data=results.excluded_df,
                ),
            )

        if not results.intra_rejected_df.empty:
            total = len(results.intra_rejected_df)
            shown = min(total, max_rows)
            panel = self._build_dupes_panel(
                results.intra_rejected_df.head(shown),
                f"Intra Dupes ({total})",
            )
            blocks.append(
                Block.create(
                    name="Intra Dupes",
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
            panel = self._build_dupes_panel(
                results.db_rejected_df.head(shown),
                f"DB Dupes ({total})",
            )
            blocks.append(
                Block.create(
                    name="DB Dupes",
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
            panel = self._txns.transactions_table(
                results.final_df.head(shown),
                f"Imported ({total})",
                shown,
                TransactionContext.IMPORT,
                show=False,
            )
            if panel is None:
                return blocks
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
        self,
        events: list[Any],
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
            merged_summary = (
                f"[green]+ {event.merged_row.get(Column.Txn.TXN_DATE, '')}|"
                f"{event.merged_row.get(Column.Txn.ACTION, '')}|"
                f"{event.merged_row.get(Column.Txn.AMOUNT, '')}|"
                f"{event.merged_row.get(Column.Txn.TICKER, '')}[/green]"
            )
            tree_content.append(merged_summary)
            for _, src_row in event.source_rows.iterrows():
                src_summary = (
                    f"  [red]└ {src_row.get(Column.Txn.TXN_DATE, '')}|"
                    f"{src_row.get(Column.Txn.ACTION, '')}|"
                    f"{src_row.get(Column.Txn.AMOUNT, '')}|"
                    f"{src_row.get(Column.Txn.TICKER, '')}[/red]"
                )
                tree_content.append(src_summary)

        for line in tree_content:
            table.add_row(line)

        return table

    def _build_transform_panel(
        self,
        events: list[Any],
        total: int,
    ) -> Table:
        """Build a table for transform events.

        Colors Field column by action color, limits Old/New width to 15.
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

    def _build_excluded_panel(
        self,
        df: pd.DataFrame,
        total: int,
    ) -> Table:
        """Build a table for excluded transactions.

        Shows TxnDate, Action, Amount always, plus columns referenced in reason.
        Only show essential columns that are relevant to the exclusion reason.
        """
        table = Table(
            title=f"Excluded ({total})",
            show_header=True,
            header_style="bold",
            border_style=THEME_EXCLUDED,
            expand=False,
            padding=SNUG_PADDING,
        )

        cols_to_show: list[str] = [str(c) for c in EXCLUSION_BASE_COLUMNS]

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
        self,
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
        self,
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

        self._show_block_key_hints(expandable)

        while True:
            try:
                user_input = getch()
                if user_input == "q":
                    break

                for block in expandable:
                    if user_input == block.key:
                        self._expand_block(block)
                        self._redisplay_audit_blocks(results, blocks)
                        break
            except (KeyboardInterrupt, EOFError):
                break

    def _show_block_key_hints(self, expandable: list[Block]) -> None:
        """Display the expansion key hints prompt for blocks.

        Args:
            expandable: List of expandable blocks.
        """
        key_hints = " ".join(f"[bold]{b.key}[/bold]={b.name}" for b in expandable)
        console_print(
            f"\n[dim]Press {key_hints} to expand, [bold]q[/bold] to continue[/dim]",
        )

    def _redisplay_audit_blocks(
        self,
        results: ImportResults,
        blocks: list[Block],
    ) -> None:
        """Redisplay the audit panel after returning from expansion.

        Args:
            results: ImportResults with audit data.
            blocks: List of Blocks to display.
        """
        active_console().clear()
        self._show_stats_panel(results)
        if blocks:
            layout = TilingLayout(blocks)
            layout.render()

        expandable = [b for b in blocks if b.expandable]
        if expandable:
            self._show_block_key_hints(expandable)

    def _expand_block(self, block: Block) -> None:
        """Expand a block to show full data with pagination.

        Args:
            block: The Block to expand.
        """
        active_console().clear()

        if block.data_type == "merge":
            panel = self._build_merge_panel(block.data, block.total)
            console_print(panel)
            console_print("\n[dim]Press any key to return...[/dim]")
            getch()

        elif block.data_type == "transform":
            panel = self._build_transform_panel(block.data, block.total)
            console_print(panel)
            console_print("\n[dim]Press any key to return...[/dim]")
            getch()

        elif block.data_type in ("excluded", "dupes", "dataframe"):
            rows = available_height(table=True, pages=True)
            page_transactions(
                block.data,
                title=block.name,
                page_size=rows,
                context=TransactionContext.IMPORT,
            )
