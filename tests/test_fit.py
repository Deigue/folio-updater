"""Tests for the table concede ladder."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from rich.table import Table

from ui.layout.fit import fit, fit_table, overflow
from ui.vocabulary import CENTLESS_EXEMPT_HEADERS, ROUNDABLE_HEADERS, SHORT_HEADERS

from .helpers.console import capture_output

if TYPE_CHECKING:
    from collections.abc import Sequence


def _table(headers: Sequence[str], *rows: Sequence[str]) -> Table:
    """Build a plain table with the given headers and rows."""
    table = Table(show_header=True)
    for header in headers:
        table.add_column(header, no_wrap=True)
    for row in rows:
        table.add_row(*row)
    return table


def _rendered(table: Table, width: int) -> str:
    """Render a table at a fixed width and hand back the plain text."""
    with capture_output(width) as capturing:
        capturing.console.print(table)
        return capturing.get_text()


def test_a_table_that_already_fits_is_left_alone() -> None:
    with capture_output(200):
        result = fit(_table(["Symbol", "Book"], ["AAA", "1,234.56"]))

    assert result.dropped == ()
    assert result.note == ""
    assert [str(c.header) for c in result.table.columns] == ["Symbol", "Book"]


def test_blank_columns_go_without_being_asked() -> None:
    """A column nothing filled in is pure cost, whatever the width."""
    with capture_output(200):
        result = fit(_table(["Symbol", "Realized", "Book"], ["AAA", "", "1,234.56"]))

    assert [str(c.header) for c in result.table.columns] == ["Symbol", "Book"]
    # Dont need to tell caller a blank column dissapeared.
    assert result.dropped == ()


def test_headers_shorten_before_anything_is_dropped() -> None:
    headers = ["Symbol", "Realized", "Unreal", "Market", "Change%"]
    row = ["AAA", "1,234.56", "2,345.67", "3,456.78", "1.23%"]

    with capture_output(36):
        result = fit(_table(headers, row), drop_order=("Realized",))

    rendered = [str(c.header) for c in result.table.columns]
    # The column survived; only its header gave ground.
    assert "Rlzd" in rendered
    assert result.dropped == ()


def test_a_day_percentage_keeps_both_decimals() -> None:
    headers = ["Symbol", "Change%", "PnL%", "Book", "Market", "Unreal", "Total"]
    row = [
        "AAA",
        "-0.03%",
        "5.42%",
        "88,520.56",
        "128,225.31",
        "39,704.75",
        "40,556.85",
    ]

    with capture_output(50):
        result = fit(_table(headers, row))

    rendered = _rendered(result.table, 50)
    assert "-0.03%" in rendered, "a day move lost the decimal that carries it"
    assert "5.42%" in rendered


def test_a_cumulative_percentage_gives_up_its_second_decimal() -> None:
    headers = ["Symbol", "Unreal%", "Total%", "Book", "Market", "Unreal", "Total"]
    row = [
        "AAA",
        "44.85%",
        "45.82%",
        "88,520.56",
        "128,225.31",
        "39,704.75",
        "4,055.85",
    ]

    with capture_output(50):
        result = fit(_table(headers, row))

    rendered = _rendered(result.table, 50)
    # 44.9% says everything 44.85% did about a position held for years.
    assert "44.9%" in rendered
    assert "44.85%" not in rendered


def test_money_gives_up_its_cents_before_a_column_goes() -> None:
    headers = ["Symbol", "Book", "Market", "Unreal", "Total"]
    row = ["AAA", "88,520.56", "128,225.31", "39,704.75", "40,556.85"]

    with capture_output(42):
        result = fit(_table(headers, row), drop_order=("Total",))

    rendered = _rendered(result.table, 42)
    assert "88,521" in rendered
    assert "88,520.56" not in rendered
    # Every column survived: cents were enough to pay for the fit.
    assert result.dropped == ()


def test_money_gives_up_its_scale_before_a_column_goes() -> None:
    headers = ["Symbol", "Book", "Market", "Unreal", "Total"]
    row = ["AAA", "88,520.56", "128,225.31", "39,704.75", "40,556.85"]

    with capture_output(30):
        result = fit(_table(headers, row), drop_order=("Total",))

    rendered = _rendered(result.table, 30)
    assert "K" in rendered


def test_a_price_keeps_its_cents_while_money_loses_them() -> None:
    """A per-unit price is small enough that its cents are the point."""
    headers = ["Symbol", "Last", "Book", "Market", "Unreal", "Total"]
    row = ["AAA", "295.45", "88,520.56", "128,225.31", "39,704.75", "40,556.85"]

    with capture_output(50):
        result = fit(_table(headers, row))

    rendered = _rendered(result.table, 50)
    assert "295.45" in rendered


def test_columns_are_dropped_last_and_in_the_order_given() -> None:
    headers = ["Symbol", "Name", "Book", "Folio%", "Market"]
    row = ["AAA", "A Long Company Name Inc", "88,520.56", "4.34%", "128,225.31"]

    with capture_output(28):
        result = fit(_table(headers, row), drop_order=("Name", "Book", "Folio%"))

    assert result.dropped[0] == "Name", "the least useful column went first"
    assert "Name" not in [str(c.header) for c in result.table.columns]


def test_the_reader_is_told_what_went_missing() -> None:
    headers = ["Symbol", "Name", "Book", "Market"]
    row = ["AAA", "A Long Company Name Inc", "88,520.56", "128,225.31"]

    with capture_output(26):
        result = fit(_table(headers, row), drop_order=("Name", "Book"))

    assert result.dropped
    assert "hidden" in result.note
    assert "widen the window" in result.note
    for header in result.dropped:
        assert header in result.note


def test_the_note_is_singular_for_one_column() -> None:
    headers = ["Symbol", "Name", "Market"]
    row = ["AAA", "A Very Long Company Name Incorporated", "128,225.31"]

    with capture_output(30):
        result = fit(_table(headers, row), drop_order=("Name",))

    assert result.dropped == ("Name",)
    assert "see it." in result.note


def test_fit_table_still_returns_just_the_table() -> None:
    """The plain form every existing caller uses."""
    with capture_output(200):
        table = fit_table(_table(["Symbol", "Book"], ["AAA", "1.00"]))

    assert isinstance(table, Table)


def test_overflow_reports_zero_for_a_table_that_fits() -> None:
    with capture_output(200):
        assert overflow(_table(["Symbol"], ["AAA"])) == 0


def test_overflow_measures_the_overrun() -> None:
    headers = [f"Column{index}" for index in range(12)]
    with capture_output(40):
        assert overflow(_table(headers, ["x"] * 12)) > 0


@pytest.mark.parametrize("headers", [ROUNDABLE_HEADERS, CENTLESS_EXEMPT_HEADERS])
def test_every_exempt_header_carries_its_short_form(headers: frozenset[str]) -> None:
    """Otherwise the rule lapses the moment `_shorten_headers` runs.

    This is the invariant behind `with_short_forms`, asserted directly so a
    new column added to either set cannot reintroduce the bug.
    """
    for name in headers:
        short = SHORT_HEADERS.get(name)
        if short is not None:
            assert short in headers, f"{name} is exempt but {short} is not"
