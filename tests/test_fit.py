"""Tests for the table concede ladder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from exporters.sheet_spec import Fmt
from ui.layout.columns import FormattedColumn, add_column
from ui.layout.fit import fit, fit_table, overflow

from .helpers.console import capture_output

if TYPE_CHECKING:
    from collections.abc import Sequence


def _col(header: str, fmt: Fmt = Fmt.TEXT, *, coarse: bool = False) -> FormattedColumn:
    """Describe one column of a test table."""
    return FormattedColumn(header, fmt=fmt, coarse=coarse, no_wrap=True)


def _table(columns: Sequence[FormattedColumn], *rows: Sequence[str]) -> Table:
    """Build a table whose columns say how their values read."""
    table = Table(show_header=True)
    for column in columns:
        add_column(table, column)
    for row in rows:
        table.add_row(*row)
    return table


def _rendered(table: Table, width: int) -> str:
    """Render a table at a fixed width and hand back the plain text."""
    with capture_output(width) as capturing:
        capturing.console.print(table)
        return capturing.get_text()


_SYMBOL = _col("Symbol")
# Wide money columns that leave a narrow table no room without shrinking.
_MONEY_COLUMNS = (
    _col("Book", Fmt.MONEY),
    _col("Market", Fmt.MONEY),
    _col("Unreal", Fmt.MONEY_SIGNED),
    _col("Total", Fmt.MONEY_SIGNED),
)
_MONEY_CELLS = ("54,321.98", "76,543.21", "22,221.23", "23,010.45")


def test_a_table_that_already_fits_is_left_alone() -> None:
    with capture_output(200):
        result = fit(_table([_SYMBOL, _col("Book", Fmt.MONEY)], ["AAA", "1,234.56"]))

    assert result.dropped == ()
    assert result.note == ""
    assert [str(c.header) for c in result.table.columns] == ["Symbol", "Book"]


def test_blank_columns_go_without_being_asked() -> None:
    """A column nothing filled in is pure cost, whatever the width."""
    columns = [_SYMBOL, _col("Realized", Fmt.MONEY_SIGNED), _col("Book", Fmt.MONEY)]
    with capture_output(200):
        result = fit(_table(columns, ["AAA", "", "1,234.56"]))

    assert [str(c.header) for c in result.table.columns] == ["Symbol", "Book"]
    # No need to tell the caller a blank column disappeared.
    assert result.dropped == ()


def test_headers_shorten_before_anything_is_dropped() -> None:
    columns = [
        _SYMBOL,
        _col("Realized", Fmt.MONEY_SIGNED),
        _col("Unreal", Fmt.MONEY_SIGNED),
        _col("Market", Fmt.MONEY),
        _col("Change%", Fmt.PERCENT_SIGNED),
    ]
    row = ["AAA", "1,234.56", "2,345.67", "3,456.78", "1.23%"]

    with capture_output(36):
        result = fit(_table(columns, row), drop_order=("Realized",))

    rendered = [str(c.header) for c in result.table.columns]
    # The column survived; only its header gave ground.
    assert "Rlzd" in rendered
    assert result.dropped == ()


def test_a_day_percentage_keeps_both_decimals() -> None:
    columns = [
        _SYMBOL,
        _col("Change%", Fmt.PERCENT_SIGNED),
        _col("PnL%", Fmt.PERCENT_SIGNED),
        *_MONEY_COLUMNS,
    ]
    row = ["AAA", "-0.07%", "6.18%", *_MONEY_CELLS]

    with capture_output(50):
        result = fit(_table(columns, row))

    rendered = _rendered(result.table, 50)
    assert "-0.07%" in rendered, "a day move lost the decimal that carries it"
    assert "6.18%" in rendered


def test_a_cumulative_percentage_gives_up_its_second_decimal() -> None:
    columns = [
        _SYMBOL,
        _col("Unreal%", Fmt.PERCENT_SIGNED, coarse=True),
        _col("Total%", Fmt.PERCENT_SIGNED, coarse=True),
        *_MONEY_COLUMNS,
    ]
    row = ["AAA", "27.38%", "28.45%", *_MONEY_CELLS]

    with capture_output(50):
        result = fit(_table(columns, row))

    rendered = _rendered(result.table, 50)
    # 27.4% says everything 27.38% did about a holding kept for years.
    assert "27.4%" in rendered
    assert "27.38%" not in rendered


def test_money_gives_up_its_cents_before_a_column_goes() -> None:
    with capture_output(42):
        result = fit(
            _table([_SYMBOL, *_MONEY_COLUMNS], ["AAA", *_MONEY_CELLS]),
            drop_order=("Total",),
        )

    rendered = _rendered(result.table, 42)
    assert "54,322" in rendered
    assert "54,321.98" not in rendered
    # Every column survived: cents were enough to pay for the fit.
    assert result.dropped == ()


def test_money_gives_up_its_scale_before_a_column_goes() -> None:
    with capture_output(30):
        result = fit(
            _table([_SYMBOL, *_MONEY_COLUMNS], ["AAA", *_MONEY_CELLS]),
            drop_order=("Total",),
        )

    rendered = _rendered(result.table, 30)
    assert "K" in rendered


def test_ids_and_dates_are_never_abbreviated() -> None:
    columns = [
        _col("TxnId", Fmt.ID),
        _col("SettleDate", Fmt.DATE),
        _col("TxnDate", Fmt.DATE),
        _col("Amount", Fmt.MONEY_SIGNED),
        _col("Book", Fmt.MONEY),
    ]
    row = ["4,100", "2025-03-04", "2025-03-06", "54,321.98", "76,543.21"]

    with capture_output(30):
        result = fit(_table(columns, row))

    rendered = _rendered(result.table, 60)
    assert "4,100" in rendered
    assert "2025-03-04" in rendered
    assert "2025-03-06" in rendered


def test_a_price_keeps_its_cents_while_money_loses_them() -> None:
    """A per-unit price is small enough that its cents are the point."""
    columns = [_SYMBOL, _col("Last", Fmt.PRICE), *_MONEY_COLUMNS]

    with capture_output(50):
        result = fit(_table(columns, ["AAA", "295.45", *_MONEY_CELLS]))

    rendered = _rendered(result.table, 50)
    assert "295.45" in rendered


def test_a_rate_keeps_its_decimals_on_a_narrow_table() -> None:
    columns = [_SYMBOL, *_MONEY_COLUMNS, _col("Rate", Fmt.RATE)]

    with capture_output(30):
        result = fit(_table(columns, ["SPY", *_MONEY_CELLS, "1.3817"]))

    assert "1.3817" in _rendered(result.table, 80)


def test_a_folded_price_keeps_its_decimals_on_a_narrow_table() -> None:
    columns = [
        _SYMBOL,
        _col("Price", Fmt.PRICE),
        _col("$", Fmt.CURRENCY),
        *_MONEY_COLUMNS,
    ]
    table = _table(
        columns,
        ["SPY", "0.0731", "CAD", *_MONEY_CELLS],
        ["MSFT", "0.0468", "CAD", *_MONEY_CELLS],
    )

    with capture_output(30):
        result = fit(table)

    rendered = _rendered(result.table, 80)
    assert "Price CAD" in rendered
    assert "0.07" in rendered, "the folded price lost its cents"


def test_a_date_keeps_its_value_on_a_narrow_table() -> None:
    columns = [_col("Settles", Fmt.DATE), *_MONEY_COLUMNS]

    with capture_output(30):
        result = fit(_table(columns, ["2026-02-17", *_MONEY_CELLS]))

    assert "2026-02-17" in _rendered(result.table, 80)


def test_a_description_keeps_its_numbers_on_a_narrow_table() -> None:
    columns = [_col("Description"), *_MONEY_COLUMNS]

    with capture_output(30):
        result = fit(_table(columns, ["DIV ON 2400 SHS", *_MONEY_CELLS]))

    assert "DIV ON 2400 SHS" in _rendered(result.table, 80)


def test_columns_are_dropped_last_and_in_the_order_given() -> None:
    columns = [
        _SYMBOL,
        _col("Name"),
        _col("Book", Fmt.MONEY),
        _col("Folio%", Fmt.PERCENT),
        _col("Market", Fmt.MONEY),
    ]
    row = ["AAA", "A Long Company Name Inc", "54,321.98", "3.21%", "76,543.21"]

    with capture_output(28):
        result = fit(_table(columns, row), drop_order=("Name", "Book", "Folio%"))

    assert result.dropped[0] == "Name", "the least useful column went first"
    assert "Name" not in [str(c.header) for c in result.table.columns]


def test_a_folded_column_is_still_dropped_by_its_header() -> None:
    columns = [_SYMBOL, _col("Price", Fmt.PRICE), _col("$", Fmt.CURRENCY), _col("Name")]
    row = ["SPY", "512.3391", "CAD", "A Very Long Exchange Traded Fund Name"]

    # Too narrow even for "Price CAD" once the name has gone.
    with capture_output(14):
        result = fit(_table(columns, row), drop_order=("Name", "Price"))

    assert result.dropped == ("Name", "Price")
    assert "Price CAD" not in [str(c.header) for c in result.table.columns]


def test_the_reader_is_told_what_went_missing() -> None:
    columns = [
        _SYMBOL,
        _col("Name"),
        _col("Book", Fmt.MONEY),
        _col("Market", Fmt.MONEY),
    ]
    row = ["AAA", "A Long Company Name Inc", "54,321.98", "76,543.21"]

    with capture_output(26):
        result = fit(_table(columns, row), drop_order=("Name", "Book"))

    assert result.dropped
    assert "hidden" in result.note
    assert "widen the window" in result.note
    for header in result.dropped:
        assert header in result.note


def test_the_note_is_singular_for_one_column() -> None:
    columns = [_SYMBOL, _col("Name"), _col("Market", Fmt.MONEY)]
    row = ["AAA", "A Very Long Company Name Incorporated", "76,543.21"]

    with capture_output(30):
        result = fit(_table(columns, row), drop_order=("Name",))

    assert result.dropped == ("Name",)
    assert "see it." in result.note


def test_fit_table_still_returns_just_the_table() -> None:
    """The plain form every existing caller uses."""
    with capture_output(200):
        table = fit_table(_table([_SYMBOL, _col("Book", Fmt.MONEY)], ["AAA", "1.00"]))

    assert isinstance(table, Table)


def test_overflow_reports_zero_for_a_table_that_fits() -> None:
    with capture_output(200):
        assert overflow(_table([_SYMBOL], ["AAA"])) == 0


def test_overflow_measures_the_overrun() -> None:
    columns = [_col(f"Column{index}") for index in range(12)]
    with capture_output(40):
        assert overflow(_table(columns, ["x"] * 12)) > 0
