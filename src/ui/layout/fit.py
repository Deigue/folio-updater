"""Fitting a Rich table to the terminal, giving up the least that it can.

The concede mechanism cheapest-first: padding, then header and cell text,
then folding a shared currency into its header, then rounding, and only as a
last resort dropping whole columns. Each stage is remeasured, so a table that
fits after squeezing padding never loses a column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.measure import Measurement
from rich.padding import Padding

from ui.console import active_console
from ui.theme import (
    ACCOUNT_HEADERS,
    ACCOUNT_MIN_WORD,
    ACCOUNT_SEPARATORS,
    ACCOUNT_WIDTH,
    ACTION_HEADERS,
    CENTLESS_EXEMPT_HEADERS,
    COARSE_PERCENT_HEADERS,
    CURRENCY_HEADERS,
    CURRENCY_HOSTS,
    DECIMAL_RUN,
    INTEGER_RUN,
    MAGNITUDES,
    MONEY_DECIMAL_RUN,
    MONEY_PRECISION,
    PERCENT_RUN,
    ROUNDABLE_HEADERS,
    SHORT_ACTIONS,
    SHORT_HEADERS,
    SNUG_PADDING,
    TIGHT_PADDING,
)

if TYPE_CHECKING:  # pragma: no cover
    import re
    from collections.abc import Sequence

    from rich.table import Table

# A width no table will reach, used to ask Rich how wide one wants to be.
_UNBOUNDED_WIDTH = 10_000


def overflow(table: Table) -> int:
    """If terminal is overflowing, report by how many characters.

    Args:
        table: The table about to be printed.

    Returns:
        Characters by which the table overruns the terminal, or zero if it
        fits.
    """
    active = active_console()
    # Measured unbounded max vs actual terminal width.
    roomy = active.options.update(max_width=_UNBOUNDED_WIDTH)
    wanted = Measurement.get(active, roomy, table).maximum
    return max(wanted - active.width, 0)


def _snug(table: Table) -> None:
    """Leave a cell a space on its right only, rather than either side."""
    table.padding = Padding.unpack(SNUG_PADDING)


def _tight(table: Table) -> None:
    """Run the columns flush against their borders."""
    table.padding = Padding.unpack(TIGHT_PADDING)


def _drop_blank(table: Table) -> None:
    """Drop columns that no row on this page fills in.

    Judged against the rows being rendered, which is all a paged table ever
    shows at once, so the scan cost is negligible.
    """
    for index in reversed(range(len(table.columns))):
        cells = table.columns[index].cells
        if not any(str(cell).strip() for cell in cells):
            del table.columns[index]


def _drop_columns(table: Table, drop_order: Sequence[str]) -> list[str]:
    """Drop columns, least valuable first, until the table fits.

    The last resort, which sacrifices information to fit useful content.

    Args:
        table: The table about to be printed.
        drop_order: Column headers ordered by least to most important

    Returns:
        The headers actually dropped, in the order they went, so the caller can
        tell the reader what is missing.
    """
    dropped: list[str] = []
    for header in drop_order:
        if not overflow(table):
            return dropped
        wanted = {header, SHORT_HEADERS.get(header, header)}
        for index in reversed(range(len(table.columns))):
            if str(table.columns[index].header) in wanted:
                del table.columns[index]
                dropped.append(header)
    return dropped


def _shorten_actions(table: Table) -> None:
    """Swap each action for its code."""
    for column in table.columns:
        if str(column.header) not in ACTION_HEADERS:
            continue

        cells = column._cells  # noqa: SLF001
        for index, cell in enumerate(cells):
            text = str(cell)
            for action, code in SHORT_ACTIONS.items():
                text = text.replace(action, code)
            cells[index] = text


def _shrink_account(name: str) -> str:
    """Wear an account name down to a shorter width.

    Args:
        name: The account name as stored.

    Returns:
        The name at no more than `ACCOUNT_WIDTH` characters.
    """
    if len(name) <= ACCOUNT_WIDTH:  # already short
        return name

    tokens = ACCOUNT_SEPARATORS.split(name)
    words = list(range(0, len(tokens), 2))
    if len(words) == 1:
        return name[:ACCOUNT_WIDTH]

    while len("".join(tokens)) > ACCOUNT_WIDTH:
        longest = max(words, key=lambda index: len(tokens[index]))
        if len(tokens[longest]) <= ACCOUNT_MIN_WORD:
            break
        tokens[longest] = tokens[longest][:-1]
    return "".join(tokens)


def _shrink_accounts(table: Table) -> None:
    """Shorten every account name in the table, where one is on show."""
    for column in table.columns:
        if str(column.header) not in ACCOUNT_HEADERS:
            continue
        # Rich offers no public way to rewrite a built column's cells.
        cells = column._cells  # noqa: SLF001
        cells[:] = [_shrink_account(str(cell)) for cell in cells]


def _reduce_precision(table: Table) -> None:
    """Round the columns that carry more decimals than they must down to cents."""
    for column in table.columns:
        if str(column.header) not in ROUNDABLE_HEADERS:
            continue
        cells = column._cells  # noqa: SLF001
        cells[:] = [DECIMAL_RUN.sub(_to_cents, str(cell)) for cell in cells]


def _to_cents(match: re.Match[str]) -> str:
    """Re-render one number found inside a cell at cent precision."""
    return f"{float(match.group().replace(',', '')):,.{MONEY_PRECISION}f}"


def _coarsen_percents(table: Table) -> None:
    """Drop the second decimal from the percentages that could spare it."""
    for column in table.columns:
        if str(column.header) not in COARSE_PERCENT_HEADERS:
            continue
        cells = column._cells  # noqa: SLF001
        cells[:] = [PERCENT_RUN.sub(_to_one_decimal, str(cell)) for cell in cells]


def _to_one_decimal(match: re.Match[str]) -> str:
    """Re-render one percentage found inside a cell at a single decimal."""
    number = float(match.group().rstrip("%").replace(",", ""))
    return f"{number:,.1f}%"


def _drop_cents(table: Table) -> None:
    """Round money columns to whole units.

    The cheapest information a wide table can give up: on a five-figure market
    value the cents are noise. Exact figures still in `--export` and
    in `folio acb`. Percentages and per-unit prices are exempt.
    """
    for column in table.columns:
        if str(column.header) in CENTLESS_EXEMPT_HEADERS:
            continue
        cells = column._cells  # noqa: SLF001
        cells[:] = [MONEY_DECIMAL_RUN.sub(_to_whole, str(cell)) for cell in cells]


def _to_whole(match: re.Match[str]) -> str:
    """Re-render one number found inside a cell at whole-unit precision."""
    return f"{float(match.group().replace(',', '')):,.0f}"


def _use_magnitudes(table: Table) -> None:
    """Abbreviate large money figures to K, M or B.

    The last thing tried before whole columns start disappearing: losing the
    scale of a number is worse than losing its cents, but better than losing
    the column entirely.
    """
    for column in table.columns:
        if str(column.header) in CENTLESS_EXEMPT_HEADERS:
            continue
        cells = column._cells  # noqa: SLF001
        cells[:] = [MONEY_DECIMAL_RUN.sub(_to_magnitude, str(cell)) for cell in cells]
        cells[:] = [INTEGER_RUN.sub(_to_magnitude, str(cell)) for cell in cells]


def _to_magnitude(match: re.Match[str]) -> str:
    """Re-render one number found inside a cell as K, M or B."""
    text = match.group()
    value = float(text.replace(",", ""))
    for limit, suffix in MAGNITUDES:
        if abs(value) >= limit:
            return f"{value / limit:,.1f}{suffix}"
    return text


def _fold_currency(table: Table) -> None:
    """Move a currency every row shares out of its column and into a header."""
    found = [
        index
        for index, column in enumerate(table.columns)
        if str(column.header) in CURRENCY_HEADERS
    ]
    if not found:
        return

    shared = {str(cell).strip() for cell in table.columns[found[0]].cells}
    host = next(
        (column for column in table.columns if str(column.header) in CURRENCY_HOSTS),
        None,
    )
    if len(shared) != 1 or host is None:
        return
    currency = shared.pop()
    if not currency:
        return

    host.header = f"{host.header} {currency}"
    del table.columns[found[0]]


def _shorten_headers(table: Table) -> None:
    """Swap in the short form of every header that has one."""
    for column in table.columns:
        short = SHORT_HEADERS.get(str(column.header))
        if short is not None:
            column.header = short


@dataclass(frozen=True)
class FitResult:
    """A fitted table and what fitting it cost.

    Attributes:
        table: The table, adjusted in place and ready to print.
        dropped: Headers given up entirely, least valuable first.
    """

    table: Table
    dropped: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        """A one-line disclosure of what is missing, or empty when nothing is."""
        if not self.dropped:
            return ""
        missing = ", ".join(self.dropped)
        return (
            f"[dim]{missing} hidden - widen the window to see "
            f"{'them' if len(self.dropped) > 1 else 'it'}.[/dim]"
        )


def fit(table: Table, drop_order: Sequence[str] = ()) -> FitResult:
    """Fit a table smartly to the terminal, reporting at what cost.

    Empty columns are dropped first. Then we squeeze padding, shorten headers
    and cells. Then fold currencies into headers and round figures to cents.
    Then coarse percentages lose a decimal, money loses its cents and finally
    its scale. Only then do we drop columns, on the caller's own priority
    order.

    Args:
        table: The table about to be printed. Adjusted in place.
        drop_order: Headers this table can give up as last resort, least->most important

    Returns:
        The fitted table alongside the headers it had to give up.
    """
    _drop_blank(table)
    for concede in (
        _snug,
        _tight,
        _shorten_headers,
        _shorten_actions,
        _shrink_accounts,
        _fold_currency,
        _reduce_precision,
        _coarsen_percents,
        _drop_cents,
        _use_magnitudes,
    ):
        if not overflow(table):
            return FitResult(table)
        concede(table)
    return FitResult(table, tuple(_drop_columns(table, drop_order)))


def fit_table(table: Table, drop_order: Sequence[str] = ()) -> Table:
    """Fit a table to the terminal, giving up the least that it can.

    The plain form, for callers with nowhere to put a disclosure. Use `fit`
    where the reader can be told what went missing.

    Args:
        table: The table about to be printed. Adjusted in place.
        drop_order: Headers this table can give up as last resort, least->most important

    Returns:
        The same table, for printing inline.
    """
    return fit(table, drop_order).table
