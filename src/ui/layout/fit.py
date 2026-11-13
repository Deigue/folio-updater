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

from exporters.sheet_spec import Fmt
from term.console import active_console
from ui.layout.columns import fmt_of, is_coarse, key_of
from ui.vocabulary import (
    ACCOUNT_MIN_WORD,
    ACCOUNT_SEPARATORS,
    ACCOUNT_WIDTH,
    DECIMAL_RUN,
    INTEGER_RUN,
    MAGNITUDES,
    MONEY_DECIMAL_RUN,
    MONEY_PRECISION,
    PERCENT_RUN,
    SHORT_ACTIONS,
    SHORT_HEADERS,
    SNUG_PADDING,
    TIGHT_PADDING,
)

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Sequence

    from rich.table import Column, Table

# A width no table will reach, used to ask Rich how wide one wants to be.
_UNBOUNDED_WIDTH = 10_000

# Money can give up its cents, then its scale. A count only has scale to give.
_MONEY_FMTS = frozenset({Fmt.MONEY, Fmt.MONEY_SIGNED, Fmt.LARGE})
_SCALED_FMTS = _MONEY_FMTS | {Fmt.COUNT}
# Per-unit prices carry more decimals than they must, down to cents.
_PRICE_FMTS = frozenset({Fmt.PRICE, Fmt.PRICE_SIGNED})
# The columns a shared currency can be folded into the header of.
_CURRENCY_HOST_FMTS = frozenset({Fmt.MONEY, Fmt.MONEY_SIGNED, *_PRICE_FMTS})


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
        drop_order: Column keys ordered by least to most important

    Returns:
        The keys actually dropped, in the order they went, so the caller can
        tell the reader what is missing.
    """
    dropped: list[str] = []
    for key in drop_order:
        if not overflow(table):
            return dropped
        for index in reversed(range(len(table.columns))):
            if key_of(table.columns[index]) == key:
                del table.columns[index]
                dropped.append(key)
    return dropped


def _rewrite(
    table: Table,
    wanted: Callable[[Column], bool],
    change: Callable[[str], str],
) -> None:
    """Rewrite every cell of the columns wanted, one cell at a time."""
    for column in table.columns:
        if not wanted(column):
            continue
        # Rich offers no public way to rewrite a built column's cells.
        cells = column._cells  # noqa: SLF001
        cells[:] = [change(str(cell)) for cell in cells]


def _of_fmt(*fmts: Fmt) -> Callable[[Column], bool]:
    """Pick the columns whose values read in any of these formats."""
    return lambda column: fmt_of(column) in fmts


def _shorten_action(text: str) -> str:
    """Swap an action for its code."""
    for action, code in SHORT_ACTIONS.items():
        text = text.replace(action, code)
    return text


def _shorten_actions(table: Table) -> None:
    """Swap each action for its code."""
    _rewrite(table, _of_fmt(Fmt.ACTION), _shorten_action)


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
    _rewrite(table, _of_fmt(Fmt.ACCOUNT), _shrink_account)


def _to_precision(places: int) -> Callable[[re.Match[str]], str]:
    """Re-render each number found inside a cell at `places` decimals."""

    def render(match: re.Match[str]) -> str:
        # Rounded before formatting, so a residue below the last place reads
        # 0.00 rather than -0.00.
        number = round(float(match.group().replace(",", "")), places) + 0.0
        return f"{number:,.{places}f}"

    return render


def _reduce_precision(table: Table) -> None:
    """Round prices, which carry more decimals than they must, down to cents."""
    to_cents = _to_precision(MONEY_PRECISION)
    _rewrite(
        table,
        _of_fmt(*_PRICE_FMTS),
        lambda text: DECIMAL_RUN.sub(to_cents, text),
    )


def _coarsen_percents(table: Table) -> None:
    """Drop the second decimal from the percentages that could spare it."""
    _rewrite(table, is_coarse, lambda text: PERCENT_RUN.sub(_to_one_decimal, text))


def _to_one_decimal(match: re.Match[str]) -> str:
    """Re-render one percentage found inside a cell at a single decimal."""
    number = float(match.group().rstrip("%").replace(",", ""))
    return f"{number:,.1f}%"


def _drop_cents(table: Table) -> None:
    """Round money columns to whole units.

    The cheapest information a wide table can give up: on a five-figure market
    value the cents are noise. Exact figures still in `--export` and
    in `folio acb`.
    """
    to_whole = _to_precision(0)
    _rewrite(
        table,
        _of_fmt(*_MONEY_FMTS),
        lambda text: MONEY_DECIMAL_RUN.sub(to_whole, text),
    )


def _use_magnitudes(table: Table) -> None:
    """Abbreviate large money figures and counts to K, M, B or T.

    The last thing tried before whole columns start disappearing: losing the
    scale of a number is worse than losing its cents, but better than losing
    the column entirely.
    """

    def scaled(text: str) -> str:
        text = MONEY_DECIMAL_RUN.sub(_to_magnitude, text)
        return INTEGER_RUN.sub(_to_magnitude, text)

    _rewrite(table, _of_fmt(*_SCALED_FMTS), scaled)


def _to_magnitude(match: re.Match[str]) -> str:
    """Re-render one number found inside a cell as K, M, B or T."""
    text = match.group()
    value = float(text.replace(",", ""))
    for limit, suffix in MAGNITUDES:
        if abs(value) >= limit:
            return f"{value / limit:,.1f}{suffix}"
    return text


def _fold_currency(table: Table) -> None:
    """Move a currency every row shares out of its column and into a header.

    Only the header changes: the column keeps its format and its key.
    """
    found = [
        index
        for index, column in enumerate(table.columns)
        if fmt_of(column) is Fmt.CURRENCY
    ]
    if not found:
        return

    shared = {str(cell).strip() for cell in table.columns[found[0]].cells}
    host = next(
        (column for column in table.columns if fmt_of(column) in _CURRENCY_HOST_FMTS),
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
        dropped: Keys of the columns given up entirely, least valuable first.
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
    and cells. Then fold currencies into headers and round prices to cents.
    Then coarse percentages lose a decimal, money loses its cents and finally
    money and counts lose their scale. Only then do we drop columns, on the
    caller's own priority order.

    Args:
        table: The table about to be printed. Adjusted in place.
        drop_order: Keys of the columns this table can give up as a last
            resort, least to most important. A formatted column's key is the
            header it was added with.

    Returns:
        The fitted table alongside the keys of the columns it had to give up.
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
        drop_order: Keys of the columns this table can give up as a last
            resort, least to most important.

    Returns:
        The same table, for printing inline.
    """
    return fit(table, drop_order).table
