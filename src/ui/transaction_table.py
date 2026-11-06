"""Render different variants of transaction tables, shared by every report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.table import Table

from domain import Column
from domain.numeric import dec, q2
from term.console import console_print
from ui.cells import money, style
from ui.format import decimals, safe_str
from ui.layout.fit import fit_table
from ui.layout.paging import page_frame, page_title
from ui.vocabulary import (
    CALCULATED_SETTLEMENT,
    GAIN,
    LOSS,
    MONEY_PRECISION,
    PRICE_PRECISION,
    TABLE_BORDER,
    TABLE_HEADER,
    THEME_TRANSFORMS,
    TRANSACTION_COLORS,
    UNIT_PRECISION,
    TransactionContext,
)

if TYPE_CHECKING:
    from collections.abc import Container, Sequence

    import pandas as pd
    from rich.console import JustifyMethod


@dataclass(frozen=True)
class _TxnColumn:
    """How a standard transaction column is rendered in a table."""

    max_width: int
    justify: JustifyMethod = "left"
    style: str | None = None


# Standard transaction columns, in display order. Column definitions and row
# cells are both derived from this, so the two can never drift apart.
_TXN_COLUMN_SPECS: dict[str, _TxnColumn] = {
    Column.Txn.TXN_ID: _TxnColumn(max_width=6, style="dim"),
    Column.Txn.SETTLE_DATE: _TxnColumn(max_width=10),
    Column.Txn.TXN_DATE: _TxnColumn(max_width=10),
    Column.Txn.ACTION: _TxnColumn(max_width=12),
    Column.Txn.AMOUNT: _TxnColumn(max_width=12, justify="right"),
    Column.Txn.CURRENCY: _TxnColumn(max_width=4),
    Column.Txn.PRICE: _TxnColumn(max_width=10, justify="right"),
    Column.Txn.UNITS: _TxnColumn(max_width=10, justify="right"),
    Column.Txn.TICKER: _TxnColumn(max_width=12),
    Column.Txn.ACCOUNT: _TxnColumn(max_width=15),
    Column.Txn.FEE: _TxnColumn(max_width=8, justify="right"),
}

# Shown only outside the import context, where TxnIds do not exist yet.
_ID_COLUMNS = (Column.Txn.TXN_ID, Column.Txn.SETTLE_DATE)

# Room an inline "old -> new" diff needs beyond the column's normal width.
_DIFF_EXTRA_WIDTH = 6

# Optional Description Column
_DESCRIPTION = "Description"

# Columns with special handling: the rest of a frame's columns are non-standard.
_KNOWN_COLUMNS = {*_TXN_COLUMN_SPECS, Column.Txn.SETTLE_CALCULATED}

_NOTHING_TO_SHOW = "[yellow]No transactions to display[/yellow]"


def _ordered_columns(
    display_df: pd.DataFrame,
    context: TransactionContext,
) -> list[str]:
    """List the columns a transaction table shows, in display order.

    Args:
        display_df: DataFrame being displayed
        context: Context to determine which columns to show

    Returns:
        Standard columns for the context, followed by any non-standard
        columns present in the DataFrame (GENERAL context only).
    """
    columns = [
        column
        for column in _TXN_COLUMN_SPECS
        if context != TransactionContext.IMPORT or column not in _ID_COLUMNS
    ]
    if context != TransactionContext.GENERAL:
        return columns

    columns.extend(str(col) for col in display_df.columns if col not in _KNOWN_COLUMNS)
    return columns


def _txn_drop_order(display_df: pd.DataFrame, context: TransactionContext) -> list[str]:
    """List what columns a transactions table may drop in order.

    Args:
        display_df: DataFrame being displayed.
        context: Context deciding which columns show.

    Returns:
        Headers in the order they may be dropped.
    """
    optional = [
        column
        for column in _ordered_columns(display_df, context)
        if column not in _TXN_COLUMN_SPECS
    ]
    ordered = [column for column in optional if column != _DESCRIPTION]
    ordered.extend(column for column in optional if column == _DESCRIPTION)
    ordered.append(str(Column.Txn.FEE))
    return ordered


def _money_cell(value: object, *, blank_zero: bool) -> str:
    """Render a raw `Txns` money value, blank when the transaction has none."""
    if not safe_str(value):
        return ""
    return money(dec(value), blank_zero=blank_zero)


def _amount_cell(value: object) -> str:
    """Render Amount, coloured by whether cash came in or went out.

    The sign is read after rounding to cents, so a residue below a cent is
    neither cash in nor cash out.
    """
    text = _money_cell(value, blank_zero=False)
    cents = q2(dec(value))
    colour = GAIN if cents > 0 else LOSS if cents < 0 else "white"
    return style(text, colour)


def build_transactions_table(
    df: pd.DataFrame,
    title: str | None = None,
    max_rows: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> Table:
    """Build transactions as a Rich table with color coding, fitted to the terminal.

    Args:
        df: DataFrame containing transaction data, not empty
        title: Optional title for the table
        max_rows: Maximum number of rows to include
        context: Transaction context to control column visibility

    Returns:
        The fitted table.
    """
    display_df = df.head(max_rows)

    table = Table(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
        show_lines=False,
    )

    _add_table_columns(
        display_df,
        table,
        context,
    )
    _add_table_rows(
        display_df,
        table,
        context,
    )
    return fit_table(table, _txn_drop_order(display_df, context))


def show_transactions(
    df: pd.DataFrame,
    title: str | None = None,
    max_rows: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Print transactions in a Rich table, saying so when there are none.

    Args:
        df: DataFrame containing transaction data
        title: Optional title for the table
        max_rows: Maximum number of rows to display
        context: Transaction context to control column visibility
    """
    if df.empty:
        console_print(_NOTHING_TO_SHOW)
        return

    console_print(build_transactions_table(df, title, max_rows, context))

    if len(df) > max_rows:
        console_print(
            f"\n[dim]... showing first {max_rows} of {len(df)} transactions[/dim]",
        )


def build_changes_table(
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed_columns: Sequence[str],
    title: str | None = "Pending Changes",
) -> Table:
    """Build a pending edit as full transactions with inline before/after.

    Args:
        before: Transactions as they are now, not empty.
        after: The same transactions, positionally aligned, as they would
            be once the edit is applied.
        changed_columns: Columns the edit touches.
        title: Optional title for the table.

    Returns:
        The fitted table.
    """
    context = TransactionContext.GENERAL
    columns = _ordered_columns(before, context)
    changed = [column for column in changed_columns if column in columns]

    table = Table(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=THEME_TRANSFORMS,
        show_lines=False,
    )
    _add_table_columns(before, table, context, widen=set(changed))

    for position in range(len(before)):
        old_cells = _row_cells(before.iloc[position], columns)
        new_cells = _row_cells(after.iloc[position], columns)
        row_data = []
        for column in columns:
            old = old_cells[column]
            new = new_cells[column]
            if column in changed and old != new:
                row_data.append(
                    f"[{LOSS}]{old}[/{LOSS}] [dim]->[/dim] [{GAIN}]{new}[/{GAIN}]",
                )
            else:
                row_data.append(old)
        table.add_row(*row_data)
    return fit_table(table, _txn_drop_order(before, context))


def show_changes(
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed_columns: Sequence[str],
    title: str | None = "Pending Changes",
) -> None:
    """Print a pending edit as full transactions with inline before/after.

    Args:
        before: Transactions as they are now.
        after: The same transactions, positionally aligned, as they would
            be once the edit is applied.
        changed_columns: Columns the edit touches.
        title: Optional title for the table.
    """
    if before.empty:
        console_print(_NOTHING_TO_SHOW)
        return

    console_print(build_changes_table(before, after, changed_columns, title))


def _add_table_columns(
    display_df: pd.DataFrame,
    table: Table,
    context: TransactionContext,
    widen: Container[str] = (),
) -> None:
    """Add columns to transaction table based on context.

    Args:
        display_df: DataFrame containing transaction data
        table: Rich Table to add columns to
        context: Context to determine which columns to show
        widen: Columns needing extra width to hold an inline before/after
            diff, and which therefore wrap instead of truncating
    """
    for column in _ordered_columns(display_df, context):
        spec = _TXN_COLUMN_SPECS.get(column)
        if spec is None:  # non-standard column, handled below
            continue
        width = spec.max_width
        if column in widen:
            # Make room for an inline "old -> new" diff.
            table.add_column(
                column,
                style=spec.style,
                justify=spec.justify,
                no_wrap=False,
                min_width=width + _DIFF_EXTRA_WIDTH,
                max_width=width * 2 + _DIFF_EXTRA_WIDTH,
            )
        else:
            table.add_column(
                column,
                style=spec.style,
                justify=spec.justify,
                no_wrap=True,
                max_width=width,
            )

    if context != TransactionContext.GENERAL:
        return

    # Add any additional (non-standard) columns present in the DataFrame
    for col in display_df.columns:
        if col not in _KNOWN_COLUMNS:
            table.add_column(
                str(col),
                no_wrap=col not in widen,
                max_width=15 if col not in widen else 33,
                overflow="ellipsis" if col not in widen else "fold",
            )


def _row_cells(row: pd.Series, columns: Sequence[str]) -> dict[str, str]:
    """Render one transaction into display strings, keyed by column.

    Args:
        row: A single transaction.
        columns: Columns to render, as given by `_ordered_columns`.

    Returns:
        Mapping of column name to its formatted cell text.
    """
    action = row.get(Column.Txn.ACTION, "")
    action_color = TRANSACTION_COLORS.get(action, "white")

    # Color-code SettleDate based on whether it was calculated
    settle_date_str = escape(safe_str(row.get(Column.Txn.SETTLE_DATE, "")))
    settle_color = (
        CALCULATED_SETTLEMENT if row.get(Column.Txn.SETTLE_CALCULATED) else GAIN
    )

    settle_display = f"[{settle_color}]{settle_date_str}[/{settle_color}]"
    special: dict[str, str] = {
        Column.Txn.SETTLE_DATE: settle_display,
        Column.Txn.ACTION: f"[{action_color}]{action}[/{action_color}]",
        Column.Txn.AMOUNT: _amount_cell(row.get(Column.Txn.AMOUNT)),
        # Blank when there is no fee, or less than a cent of one.
        Column.Txn.FEE: _money_cell(row.get(Column.Txn.FEE), blank_zero=True),
        Column.Txn.PRICE: decimals(
            row.get(Column.Txn.PRICE, ""),
            PRICE_PRECISION,
            MONEY_PRECISION,
        ),
        Column.Txn.UNITS: decimals(row.get(Column.Txn.UNITS, ""), UNIT_PRECISION),
    }

    return {
        column: special.get(
            column,
            escape(safe_str(row.get(column, ""))).replace("\n", " "),
        )
        for column in columns
    }


def _add_table_rows(
    display_df: pd.DataFrame,
    table: Table,
    context: TransactionContext,
) -> None:
    """Add rows to transaction table with conditional formatting.

    Args:
        display_df: DataFrame with rows to display
        table: Rich Table to add rows to
        context: Context to determine which columns to show
    """
    columns = _ordered_columns(display_df, context)
    for _, row in display_df.iterrows():
        cells = _row_cells(row, columns)
        table.add_row(*(cells[column] for column in columns))


def page_transactions(
    df: pd.DataFrame,
    title: str = "Transactions",
    page_size: int | None = None,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Display transactions with paging support for large datasets.

    This function displays transaction data in pageable format, allowing
    navigation through large transaction lists without overwhelming the console.

    Args:
        df: DataFrame containing transaction data
        title: Title for the transaction display
        page_size: Number of transactions per page. If None, calculated dynamically
            based on available console height
        context: Transaction context to specify column visibility
    """

    def render(start: int, end: int) -> None:
        page_df = df.iloc[start:end]
        show_transactions(
            page_df,
            title=page_title(title, start, end, len(df)),
            max_rows=len(page_df),
            context=context,
        )

    page_frame(len(df), title, page_size, render)


def page_changes(
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed_columns: Sequence[str],
    title: str = "Pending Changes",
    page_size: int | None = None,
) -> None:
    """Display a pending edit as a paged before/after view.

    Args:
        before: Transactions as they are now
        after: The same transactions, positionally aligned, once edited
        changed_columns: Columns the edit touches
        title: Title for the display
        page_size: Rows per page. If None, calculated from console height
    """

    def render(start: int, end: int) -> None:
        show_changes(
            before.iloc[start:end],
            after.iloc[start:end],
            changed_columns,
            title=page_title(title, start, end, len(before)),
        )

    page_frame(len(before), title, page_size, render)
