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
    from collections.abc import Container, Mapping, Sequence

    import pandas as pd
    from rich.console import JustifyMethod

    from models import TxnFields

    type _Row = Mapping[str, object]


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

# Columns with special handling: any other column a row carries is non-standard.
_KNOWN_COLUMNS = {*_TXN_COLUMN_SPECS, Column.Txn.SETTLE_CALCULATED}

_NOTHING_TO_SHOW = "[yellow]No transactions to display[/yellow]"


def _txn_rows(txns: Sequence[TxnFields]) -> list[_Row]:
    """Read transactions as rows of cells, keyed by column."""
    return [txn.to_row() for txn in txns]


def _source_rows(df: pd.DataFrame) -> list[_Row]:
    """Read a frame's rows as rows of cells, keyed by column."""
    return [
        {str(column): value for column, value in record.items()}
        for record in df.to_dict("records")
    ]


def _extra_columns(rows: Sequence[_Row]) -> list[str]:
    """List the non-standard columns the rows carry, in first-seen order."""
    seen: dict[str, None] = {}
    for row in rows:
        seen.update(dict.fromkeys(key for key in row if key not in _KNOWN_COLUMNS))
    return list(seen)


def _ordered_columns(
    rows: Sequence[_Row],
    context: TransactionContext,
) -> list[str]:
    """List the columns a transaction table shows, in display order.

    Args:
        rows: The rows being displayed.
        context: Context to determine which columns to show

    Returns:
        Standard columns for the context, followed by any non-standard
        columns the rows carry (GENERAL context only).
    """
    columns = [
        column
        for column in _TXN_COLUMN_SPECS
        if context != TransactionContext.IMPORT or column not in _ID_COLUMNS
    ]
    if context != TransactionContext.GENERAL:
        return columns

    columns.extend(_extra_columns(rows))
    return columns


def _txn_drop_order(rows: Sequence[_Row], context: TransactionContext) -> list[str]:
    """List what columns a transactions table may drop in order.

    Args:
        rows: The rows being displayed.
        context: Context deciding which columns show.

    Returns:
        Headers in the order they may be dropped.
    """
    optional = [
        column
        for column in _ordered_columns(rows, context)
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


def _build_table(
    rows: Sequence[_Row],
    title: str | None,
    max_rows: int,
    context: TransactionContext,
) -> Table:
    """Build rows as a Rich table with color coding, fitted to the terminal."""
    shown = rows[:max_rows]

    table = Table(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
        show_lines=False,
    )

    _add_table_columns(shown, table, context)
    _add_table_rows(shown, table, context)
    return fit_table(table, _txn_drop_order(shown, context))


def build_source_rows_table(
    df: pd.DataFrame,
    title: str | None = None,
    max_rows: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> Table:
    """Build rows held in a frame as a Rich table, fitted to the terminal."""
    return _build_table(_source_rows(df), title, max_rows, context)


def _show(
    rows: Sequence[_Row],
    title: str | None,
    max_rows: int,
    context: TransactionContext,
) -> None:
    """Print rows in a Rich table, saying so when there are none."""
    if not rows:
        console_print(_NOTHING_TO_SHOW)
        return

    console_print(_build_table(rows, title, max_rows, context))

    if len(rows) > max_rows:
        console_print(
            f"\n[dim]... showing first {max_rows} of {len(rows)} transactions[/dim]",
        )


def show_transactions(
    txns: Sequence[TxnFields],
    title: str | None = None,
    max_rows: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Print transactions in a Rich table, saying so when there are none.

    Args:
        txns: The transactions.
        title: Optional title for the table
        max_rows: Maximum number of rows to display
        context: Transaction context to control column visibility
    """
    _show(_txn_rows(txns), title, max_rows, context)


def show_source_rows(
    df: pd.DataFrame,
    title: str | None = None,
    max_rows: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Print rows held in a frame in a Rich table, saying so when there are none."""
    _show(_source_rows(df), title, max_rows, context)


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
    before_rows, after_rows = _source_rows(before), _source_rows(after)
    columns = _ordered_columns(before_rows, context)
    changed = [column for column in changed_columns if column in columns]

    table = Table(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=THEME_TRANSFORMS,
        show_lines=False,
    )
    _add_table_columns(before_rows, table, context, widen=set(changed))

    for old_row, new_row in zip(before_rows, after_rows, strict=True):
        old_cells = _row_cells(old_row, columns)
        new_cells = _row_cells(new_row, columns)
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
    return fit_table(table, _txn_drop_order(before_rows, context))


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
    rows: Sequence[_Row],
    table: Table,
    context: TransactionContext,
    widen: Container[str] = (),
) -> None:
    """Add columns to transaction table based on context.

    Args:
        rows: The rows being displayed.
        table: Rich Table to add columns to
        context: Context to determine which columns to show
        widen: Columns needing extra width to hold an inline before/after
            diff, and which therefore wrap instead of truncating
    """
    for column in _ordered_columns(rows, context):
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

    # Add any additional (non-standard) columns the rows carry
    for col in _extra_columns(rows):
        table.add_column(
            col,
            no_wrap=col not in widen,
            max_width=15 if col not in widen else 33,
            overflow="ellipsis" if col not in widen else "fold",
        )


def _row_cells(row: _Row, columns: Sequence[str]) -> dict[str, str]:
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
    rows: Sequence[_Row],
    table: Table,
    context: TransactionContext,
) -> None:
    """Add rows to transaction table with conditional formatting.

    Args:
        rows: The rows to display.
        table: Rich Table to add rows to
        context: Context to determine which columns to show
    """
    columns = _ordered_columns(rows, context)
    for row in rows:
        cells = _row_cells(row, columns)
        table.add_row(*(cells[column] for column in columns))


def _page(
    rows: Sequence[_Row],
    title: str,
    page_size: int | None,
    context: TransactionContext,
) -> None:
    """Display rows a page at a time."""

    def render(start: int, end: int) -> None:
        page = rows[start:end]
        _show(page, page_title(title, start, end, len(rows)), len(page), context)

    page_frame(len(rows), title, page_size, render)


def page_transactions(
    txns: Sequence[TxnFields],
    title: str = "Transactions",
    page_size: int | None = None,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Display transactions with paging support for large datasets.

    Args:
        txns: The transactions.
        title: Title for the transaction display
        page_size: Number of transactions per page. If None, calculated dynamically
            based on available console height
        context: Transaction context to specify column visibility
    """
    _page(_txn_rows(txns), title, page_size, context)


def page_source_rows(
    df: pd.DataFrame,
    title: str = "Transactions",
    page_size: int | None = None,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Display rows held in a frame a page at a time."""
    _page(_source_rows(df), title, page_size, context)


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
