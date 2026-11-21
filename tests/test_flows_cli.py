"""Tests for `folio flows`: each pool's flows by week, month or year."""

from __future__ import annotations

import csv
import re
from typing import TYPE_CHECKING

import pytest

from cli.main import app

from .helpers.cli import UNCONSTRAINED_WIDTH, assert_in_output, run_cli
from .helpers.seed import seed_cash, seed_fx, seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from .helpers.context import TempContext

_FX = dict.fromkeys(
    ("2025-05-06", "2025-05-20", "2025-06-03", "2025-06-10", "2025-06-17"),
    "1.30",
)


def _seed_two_months() -> None:
    """Fund a TFSA across two months, trading in the second, and fund an RRSP."""
    seed_fx(_FX)
    for day, amount in (("2025-05-06", "800"), ("2025-05-20", "700")):
        seed_cash(
            "CONTRIBUTION",
            amount=amount,
            account="WS-TFSA",
            date=day,
            settle_date=day,
        )
    seed_transaction(
        account="WS-TFSA",
        ticker="SPY",
        currency="CAD",
        amount="-900",
        price="90",
        units="10",
        date="2025-06-03",
        settle_date="2025-06-03",
    )
    seed_cash(
        "DIVIDEND",
        amount="4.5",
        account="WS-TFSA",
        date="2025-06-17",
        settle_date="2025-06-17",
    )
    seed_cash(
        "CONTRIBUTION",
        amount="300",
        account="WS-RRSP",
        date="2025-06-10",
        settle_date="2025-06-10",
    )


def _table_cells(output: str) -> list[list[str]]:
    """Read each table line's non-blank cells, in order."""
    lines = (line.strip() for line in output.splitlines())
    rows = ([c for c in re.split(r"\s*[│|]\s*|\s{2,}", line) if c] for line in lines)
    return [cells for cells in rows if len(cells) > 1]


def _table_rows(output: str) -> dict[str, list[str]]:
    """Read each table row's cells, keyed by its first."""
    return {cells[0]: cells[1:] for cells in _table_cells(output)}


def test_a_pool_lists_each_month_newest_first_with_its_totals(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_two_months()

        result = run_cli(app, ["flows", "-t", "tfsa"], width=UNCONSTRAINED_WIDTH)

    assert result.exit_code == 0
    rows = _table_rows(result.plain_output)
    assert rows["2025-06"] == ["900.00", "4.50", "1"]
    assert rows["2025-05"] == ["1,500.00"]
    assert rows["Total"] == ["1,500.00", "900.00", "4.50", "1"]
    assert result.plain_output.index("2025-06") < result.plain_output.index("2025-05")


def test_an_export_writes_each_period_and_the_totals(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx():
        _seed_two_months()
        target = tmp_path / "flows.csv"

        result = run_cli(app, ["flows", "-t", "tfsa", "-e", str(target)])

        # The temporary folio, export included, is cleared once its context ends.
        with target.open(encoding="utf-8", newline="") as handle:
            written = {
                row["Period"]: row for row in csv.DictReader(handle) if row["Period"]
            }

    assert result.exit_code == 0
    assert written["2025-06"]["Bought"] == "900.00"
    assert written["2025-06"]["Trades"] == "1"
    assert written["2025-05"]["Contributions"] == "1,500.00"
    assert written["Total"]["Dividends"] == "4.50"


def test_a_year_is_named_by_its_year(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_two_months()

        result = run_cli(
            app,
            ["flows", "-a", "WS-RRSP", "--period", "year"],
            width=UNCONSTRAINED_WIDTH,
        )

    assert result.exit_code == 0
    assert _table_rows(result.plain_output)["2025"] == ["300.00"]


def test_an_unknown_period_is_refused(temp_ctx: TempContext) -> None:
    with temp_ctx():
        result = run_cli(app, ["flows", "-p", "quarter"])

    assert result.exit_code == 1
    assert_in_output("Unknown period 'quarter'", result)


def test_a_pool_with_no_flows_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_two_months()

        result = run_cli(app, ["flows", "-t", "fhsa"])

    assert result.exit_code == 0
    assert_in_output("FHSA has no flows to show.", result)


def test_by_type_lists_each_types_flows_under_each_period(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_two_months()

        result = run_cli(app, ["flows", "-b"], width=UNCONSTRAINED_WIDTH)

    assert result.exit_code == 0
    output = result.plain_output
    # Each type's line, whatever period or total heads it.
    lines = [
        [cell for cell in cells if not re.fullmatch(r"\d{4}-\d{2}|Total", cell)]
        for cells in _table_cells(output)
        if {"RRSP", "TFSA"} & set(cells)
    ]
    assert lines == [
        ["RRSP", "300.00"],
        ["TFSA", "900.00", "4.50", "1"],
        ["TFSA", "1,500.00"],
        ["RRSP", "300.00"],
        ["TFSA", "1,500.00", "900.00", "4.50", "1"],
    ]
    assert output.index("2025-06") < output.index("2025-05") < output.index("Total")


@pytest.mark.parametrize("pool", [["-t", "tfsa"], ["-a", "WS-TFSA"]])
def test_by_type_takes_no_pool(temp_ctx: TempContext, pool: list[str]) -> None:
    with temp_ctx():
        result = run_cli(app, ["flows", "-b", *pool])

    assert result.exit_code == 1
    assert_in_output("--by-type shows every type", result)


def test_a_by_type_export_names_each_rows_type(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx():
        _seed_two_months()
        target = tmp_path / "types.csv"

        result = run_cli(app, ["flows", "-b", "-e", str(target)])

        with target.open(encoding="utf-8", newline="") as handle:
            written = [
                (row["Period"], row["Pool"], row["Contributions"])
                for row in csv.DictReader(handle)
                if row["Period"]
            ]

    assert result.exit_code == 0
    assert written == [
        ("2025-06", "RRSP", "300.00"),
        ("2025-06", "TFSA", ""),
        ("2025-05", "TFSA", "1,500.00"),
        ("Total", "RRSP", "300.00"),
        ("Total", "TFSA", "1,500.00"),
    ]


def test_by_type_on_an_empty_folio_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx():
        result = run_cli(app, ["flows", "-b"])

    assert result.exit_code == 0
    assert_in_output("No account type has flows to show.", result)
