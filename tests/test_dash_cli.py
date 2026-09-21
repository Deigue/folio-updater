"""Tests for `folio dash` and `folio quotes`."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from app import bootstrap
from cli.main import app
from services.quotes_service import QuotesService, RefreshResult
from services.symbols import SymbolResolver

from .helpers.cli import (
    UNCONSTRAINED_WIDTH,
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from config import Config

    from .test_types import TempContext

FX = {
    "2025-08-14": "1.2500",
    "2025-08-15": "1.2500",
    "2025-08-18": "1.2500",
    "2025-08-19": "1.2500",
    "2025-08-20": "1.2500",
}


def _seed_two_types() -> None:
    """One USD holding in a TFSA and another in a non-registered account."""
    seed_fx(FX)
    seed_transaction(
        action="CONTRIBUTION",
        account="WS-TFSA",
        ticker=None,
        currency="CAD",
        amount="10000",
        price=None,
        units=None,
        date="2025-08-14",
    )
    seed_transaction(
        account="WS-TFSA",
        ticker="TESTTKR",
        amount="-1000",
        price="100",
        units="10",
    )
    seed_transaction(
        account="WS-PERSONAL",
        ticker="OTHER",
        amount="-400",
        price="40",
        units="10",
    )


def test_dash_renders_a_holding_and_a_total(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_in_output("Portfolio", result)
        assert_in_output("2 held", result)


def test_dash_offline_still_renders_cost_base(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "--offline"])

        assert_cli_success(result)
        # Units and average cost need no quote at all.
        assert_in_output("TESTTKR", result)
        assert_in_output("nothing cached", result)
        assert_in_output("unpriced", result)


def test_dash_narrows_to_an_account_type(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-t", "tfsa"])

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_not_in_output("OTHER", result)


def test_dash_type_all_is_the_whole_portfolio(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-t", "all"])

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_in_output("OTHER", result)


def test_dash_narrows_to_one_account(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-a", "WS-TFSA"])

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_not_in_output("OTHER", result)


_REFUSED_REQUESTS = [
    pytest.param(
        ["dash", "-t", "tfsa", "-a", "WS-TFSA"],
        ["not both"],
        id="type-and-account-together",
    ),
    pytest.param(["dash", "-t", "nonsense"], ["Unknown account type"], id="bad-type"),
    pytest.param(
        ["dash", "--currency", "JPY"],
        ["Unknown currency"],
        id="uncovertible-currency",
    ),
    pytest.param(
        ["dash", "--by-type", "-t", "tfsa"],
        ["--by-type"],
        id="by-type-plus-a-narrowing-flag",
    ),
    pytest.param(
        ["dash", "-s", "nonsense"],
        # The message also names what could have been used instead.
        ["Cannot sort by 'nonsense'", "market"],
        id="unknown-sort-column",
    ),
    pytest.param(
        ["quotes", "--refresh", "--clear"],
        ["Pick one"],
        id="two-quote-actions-at-once",
    ),
]


@pytest.mark.parametrize(("argv", "phrases"), _REFUSED_REQUESTS)
def test_an_unusable_request_is_refused(
    temp_ctx: TempContext,
    argv: list[str],
    phrases: list[str],
) -> None:
    """Each of these fails on the arguments alone, whatever the folio holds."""
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, argv)

        assert result.exit_code == 1
        for phrase in phrases:
            assert_in_output(phrase, result)


def test_by_type_renders_more_than_one_panel(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "--by-type"])

        assert_cli_success(result)
        assert_in_output("TFSA", result)
        assert_in_output("NON-REGISTERED", result)


def test_wide_adds_the_name_column(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        narrow = run_cli_with_config(
            ctx.config,
            app,
            ["dash"],
            width=UNCONSTRAINED_WIDTH,
        )
        wide = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--wide"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(wide)
        # The quote's short name only appears once the column is offered.
        assert "Test Ticker Inc" in wide.plain_output
        assert "Test Ticker Inc" not in narrow.plain_output


def test_dash_sorts_by_a_named_column(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "-s", "symbol"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        rows = result.plain_output
        assert rows.index("OTHER") < rows.index("TESTTKR")


def test_dash_reverses_a_sort(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "-s", "symbol", "-r"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        rows = result.plain_output
        assert rows.index("TESTTKR") < rows.index("OTHER")


def test_a_narrow_window_says_which_columns_are_hidden(
    temp_ctx: TempContext,
) -> None:
    """Printed above the table, so a reader paging a long report sees it."""
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash"], width=60)

        assert_cli_success(result)
        assert_in_output("hidden", result)
        assert_in_output("widen the window", result)


def test_a_wide_window_hides_nothing_and_says_nothing(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_not_in_output("hidden", result)


def test_a_folio_that_never_sold_loses_the_realized_column(
    temp_ctx: TempContext,
) -> None:
    """Zeros blank themselves, so `_drop_blank` takes the column for free."""
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_not_in_output("Realized", result)


def test_a_folio_that_sold_keeps_the_realized_column(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        seed_transaction(
            action="SELL",
            account="WS-TFSA",
            ticker="TESTTKR",
            amount="750",
            price="150",
            units="5",
            date="2025-08-18",
        )

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_in_output("Realized", result)


def test_dash_shows_an_aggregate_closed_row(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        # Sell every unit: the TFSA's only position is now fully closed.
        seed_transaction(
            action="SELL",
            account="WS-TFSA",
            ticker="TESTTKR",
            amount="1500",
            price="150",
            units="10",
            date="2025-08-18",
        )

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--type", "tfsa"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_in_output("Closed", result)
        assert_not_in_output("TESTTKR", result)


def test_dash_show_closed_breaks_out_each_position(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        seed_transaction(
            action="SELL",
            account="WS-TFSA",
            ticker="TESTTKR",
            amount="1500",
            price="150",
            units="10",
            date="2025-08-18",
        )

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--type", "tfsa", "--show-closed"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_in_output("closed", result)


def test_dash_exports_a_file(temp_ctx: TempContext, tmp_path: Path) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        target = tmp_path / "holdings.csv"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--export", str(target)],
        )

        assert_cli_success(result)
        assert target.exists()
        contents = target.read_text(encoding="utf-8")
        assert "TESTTKR" in contents
        # The reported table, not the attribute names behind it.
        assert "market_value" not in contents


def _seed_mixed_currency() -> None:
    """Seed a USD holding and a CAD holding in the same account, both funded.

    The USD book value is 1,000 USD, which is 1,250 CAD at the seeded rate of
    1.25, so the two figures tell native and converted apart unambiguously.
    """
    seed_fx(FX)
    for currency, amount in (("CAD", "10000"), ("USD", "10000")):
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency=currency,
            amount=amount,
            price=None,
            units=None,
            date="2025-08-14",
        )
    seed_transaction(
        account="WS-TFSA",
        ticker="TESTTKR",
        currency="USD",
        amount="-1000",
        price="100",
        units="10",
    )
    seed_transaction(
        account="WS-TFSA",
        ticker="CADCO.TO",
        currency="CAD",
        amount="-2000",
        price="200",
        units="10",
    )


def test_a_mixed_folio_subtotals_each_currency(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_mixed_currency()

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        # One subtotal per currency group...
        assert result.plain_output.count("1 held") == 2
        # ...closed by the one converted figure on the page, which the grand
        # total row and the footnote both label with its currency.
        assert_in_output("Total", result)
        assert_in_output("(CAD) converted at USDCAD", result)


def test_a_mixed_folio_keeps_each_price_in_its_own_currency(
    temp_ctx: TempContext,
) -> None:
    """The same holding's book value, native versus forced to CAD.

    It cost 1,000 USD, which is 1,250 CAD at the seeded rate. Comparing the two
    runs is what proves the row is native rather than converted: the CAD figure
    still appears in the *grand total* of a native run, so its mere presence
    proves nothing.
    """
    with temp_ctx() as ctx:
        _seed_mixed_currency()

        native = run_cli_with_config(
            ctx.config,
            app,
            ["dash"],
            width=UNCONSTRAINED_WIDTH,
        )
        forced = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--currency", "CAD"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(native)
        assert_cli_success(forced)
        # The USD book appears natively and is gone once everything converts.
        assert "1,000.00" in native.plain_output
        assert "1,000.00" not in forced.plain_output


def test_a_single_currency_folio_draws_no_grand_total(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
        )
        seed_transaction(
            ticker="CADCO",
            currency="CAD",
            amount="-1000",
            price="100",
            units="10",
        )

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert result.plain_output.count("1 held") == 1
        # Nothing was converted, so repeating the same total would be noise
        # and there is no rate to disclose.
        assert_not_in_output("converted at USDCAD", result)


def test_a_single_usd_folio_promotes_total_percent_and_discloses_it(
    temp_ctx: TempContext,
) -> None:
    """A USD-only account still divides `Total%` by its (CAD) net deposits.

    The account itself never converts: cash and holdings both stay in USD.
    Only the `Total%` cell borrows CAD to pair with net deposits, which is why
    that promotion needs its own disclosure even though nothing else on the
    page was touched.
    """
    with temp_ctx() as ctx:
        seed_fx(FX)
        # Funded in CAD (net deposits are tracked in CAD only, see
        # `_net_deposited`), then the whole balance bought a USD security, so
        # the account itself holds and prices everything in USD.
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
        )
        seed_transaction(
            account="WS-TFSA",
            ticker="TESTTKR",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert result.plain_output.count("1 held") == 1
        assert_in_output("Total% (CAD) converted at USDCAD", result)


def test_forcing_cad_converts_and_ungroups(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_mixed_currency()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--currency", "CAD"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        # Both holdings land in a single CAD group, so there is one subtotal
        # and no separate grand total to draw.
        assert result.plain_output.count("held") == 1
        assert_in_output("2 held", result)
        assert_not_in_output("converted at USDCAD", result)
        # The USD holding's 1,000 USD book value, converted at 1.25.
        assert "1,250.00" in result.plain_output


def test_forcing_usd_on_a_mixed_folio_refuses_a_total_percent(
    temp_ctx: TempContext,
) -> None:
    """`Total%` measures the whole pool, and `-c USD` is showing part of it.

    Net deposits covers every dollar put in, including the CAD that bought the
    holding this view has dropped, so no ratio against it describes what is on
    screen. The cell says nothing rather than something else.
    """
    with temp_ctx() as ctx:
        _seed_mixed_currency()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--currency", "USD"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_in_output("1 held", result)
        assert_in_output("Total% is blank", result)
        assert_in_output("1 CAD position(s) are hidden", result)
        # Nothing is converted on a USD-only view, so there is no rate to state.
        assert_not_in_output("converted at USDCAD", result)


def test_forcing_usd_on_a_usd_only_pool_keeps_its_total_percent(
    temp_ctx: TempContext,
) -> None:
    """Nothing is hidden, so the return reads exactly as it does natively."""
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
        )
        seed_transaction(
            account="WS-TFSA",
            ticker="TESTTKR",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        forced = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--currency", "USD"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(forced)
        assert_not_in_output("Total% is blank", forced)
        # The ratio is taken on the CAD leg either way, so it is disclosed as
        # the borrowed figure it is rather than read as a USD one.
        assert_in_output("Total% (CAD) converted at USDCAD", forced)


def test_dash_exports_an_excel_file(temp_ctx: TempContext, tmp_path: Path) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        target = tmp_path / "holdings.xlsx"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--export", str(target)],
        )

        assert_cli_success(result)
        assert target.exists()
        # The sheet is named after the scope that was reported.
        assert load_workbook(target).sheetnames == ["Portfolio"]


def test_dash_refuses_an_export_format_it_cannot_write(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        target = tmp_path / "holdings.pdf"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--export", str(target)],
        )

        assert result.exit_code == 1
        assert_in_output(".xlsx", result)
        assert not target.exists()


def test_by_type_exports_a_sheet_for_every_type(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """`--by-type --export` used to print the dashboard and write nothing."""
    with temp_ctx() as ctx:
        _seed_two_types()
        target = tmp_path / "by-type.xlsx"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--by-type", "--export", str(target)],
        )

        assert_cli_success(result)
        assert load_workbook(target).sheetnames == ["NON-REGISTERED", "TFSA"]


def test_by_type_flattens_into_one_csv(temp_ctx: TempContext, tmp_path: Path) -> None:
    """A CSV holds one table, so the pools run together under a Pool column."""
    with temp_ctx() as ctx:
        _seed_two_types()
        target = tmp_path / "by-type.csv"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--by-type", "--export", str(target)],
        )

        assert_cli_success(result)
        lines = target.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("Pool,Symbol")
        pools = {line.split(",")[0] for line in lines[1:] if line.split(",")[1:2]}
        assert {"TFSA", "NON-REGISTERED"} <= pools


def test_an_export_without_a_suffix_becomes_a_workbook(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--export", str(tmp_path / "holdings")],
        )

        assert_cli_success(result)
        assert (tmp_path / "holdings.xlsx").exists()


def test_an_exported_cell_keeps_its_full_precision(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """Formats decide what is shown; the cell still holds every digit."""
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="USD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
        )
        seed_transaction(
            account="WS-TFSA",
            ticker="TESTTKR",
            currency="USD",
            amount="-1000.123456789",
            price="100.0123456789",
            units="10",
        )
        target = tmp_path / "holdings.xlsx"

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--export", str(target)],
        )

        assert_cli_success(result)
        sheet = load_workbook(target)["Portfolio"]
        headers = [cell.value for cell in sheet[1]]
        avg = sheet.cell(row=2, column=headers.index("Avg") + 1)
        # Displayed at four decimals, stored with every one the engine computed.
        assert float(avg.value) == pytest.approx(100.0123456789)


def test_dash_accepts_native_currency(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "--currency", "native"],
        )

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)


def test_by_type_says_so_when_nothing_is_held(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )

        result = run_cli_with_config(ctx.config, app, ["dash", "--by-type"])

        assert_cli_success(result)
        assert_in_output("No open positions", result)


def test_dash_says_so_when_a_pool_holds_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-a", "NOSUCHACCT"])

        assert_cli_success(result)
        assert_in_output("No open positions", result)


def test_dash_reports_negative_cash_as_an_alert(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        # A buy with nothing funding it.
        seed_transaction(ticker="TESTTKR", currency="CAD", amount="-1000", units="10")

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert_in_output("Cash is negative", result)
        assert_in_output("folio check", result)


def test_dash_badges_a_pool_whose_units_are_wrong(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
        )
        seed_transaction(ticker="TESTTKR", amount="-1000", price="100", units="10")
        # Selling more than was ever held drives units negative.
        seed_transaction(
            action="SELL",
            ticker="TESTTKR",
            amount="2500",
            price="125",
            units="20",
            date="2025-08-18",
        )

        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert_in_output("known to be wrong", result)
        assert_in_output("OVERSELL", result)


def test_the_panel_shows_gross_contributions_beside_the_net(
    temp_ctx: TempContext,
) -> None:
    """Both lines, because they answer different questions.

    `Contributions` is what was put in and stays put once money comes back out;
    `Net Deposited` is what of it the pool still holds. A pool with a withdrawal
    is the case where the two figures separate.
    """
    with temp_ctx() as ctx:
        _seed_two_types()
        seed_transaction(
            action="WITHDRAWAL",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="-2500",
            price=None,
            units=None,
            date="2025-08-19",
        )

        result = run_cli_with_config(
            ctx.config,
            app,
            ["dash", "-a", "WS-TFSA"],
            width=UNCONSTRAINED_WIDTH,
        )

        assert_cli_success(result)
        assert_in_output("Contributions", result)
        assert_in_output("10,000.00", result)
        assert_in_output("Net Deposited", result)
        assert_in_output("7,500.00", result)


def test_the_room_row_shows_for_a_registered_pool(temp_ctx: TempContext) -> None:
    with temp_ctx(contribution_room={"TFSA": {2025: 7000}}) as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-t", "tfsa"])

        assert_cli_success(result)
        assert_in_output("Room 2025", result)
        assert_in_output("7,000.00", result)


def test_the_room_row_is_absent_for_a_non_registered_pool(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx(contribution_room={"TFSA": {2025: 7000}}) as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["dash", "-t", "nreg"])

        assert_cli_success(result)
        assert_not_in_output("Room", result)


def test_the_freshness_line_ages_both_caches(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        first = run_cli_with_config(ctx.config, app, ["dash"])
        assert_cli_success(first)
        assert "acb computed just now" in first.plain_output

        second = run_cli_with_config(ctx.config, app, ["dash"])
        assert_cli_success(second)
        # The cost base was cached by the first run; both ages are disclosed.
        assert "acb cached" in second.plain_output
        assert "quotes cached" in second.plain_output


def test_refresh_returns_the_cost_base_to_freshly_computed(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        run_cli_with_config(ctx.config, app, ["dash"])

        refreshed = run_cli_with_config(ctx.config, app, ["dash", "--refresh"])

        assert_cli_success(refreshed)
        assert "acb computed just now" in refreshed.plain_output


def test_dash_on_an_empty_folio_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        result = run_cli_with_config(ctx.config, app, ["dash"])

        assert_cli_success(result)
        assert_in_output("No transactions", result)


def test_quotes_list_renders_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        run_cli_with_config(ctx.config, app, ["quotes", "--refresh"])

        result = run_cli_with_config(ctx.config, app, ["quotes"])

        assert_cli_success(result)
        assert_in_output("TESTTKR", result)
        assert_in_output("Cached Quotes", result)


def test_quotes_list_on_an_empty_cache_points_at_refresh(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["quotes"])

        assert_cli_success(result)
        assert_in_output("No quotes cached", result)


def test_quotes_refresh_reports_what_it_fetched(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(ctx.config, app, ["quotes", "--refresh"])

        assert_cli_success(result)
        assert_in_output("Fetched 2 quote(s)", result)


def test_quotes_clear_empties_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()
        run_cli_with_config(ctx.config, app, ["quotes", "--refresh"])

        cleared = run_cli_with_config(ctx.config, app, ["quotes", "--clear"])

        assert_cli_success(cleared)
        assert QuotesService.cached() == {}


def test_quotes_refresh_with_nothing_held_says_so(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )

        result = run_cli_with_config(ctx.config, app, ["quotes", "--refresh"])

        assert_cli_success(result)
        assert_in_output("No open positions to price", result)


def test_quotes_refresh_discloses_the_throttling_fallback(
    temp_ctx: TempContext,
) -> None:
    """A daily close is not an intraday price, so the run has to say so."""
    with temp_ctx() as ctx:
        _seed_two_types()
        throttled = RefreshResult(fetched=2, used_fallback=True)

        with patch.object(QuotesService, "refresh", return_value=throttled):
            result = run_cli_with_config(ctx.config, app, ["quotes", "--refresh"])

        assert_cli_success(result)
        assert_in_output("throttling", result)
        assert_in_output("daily closes", result)


def test_quotes_can_be_narrowed_to_one_ticker(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        _seed_two_types()

        result = run_cli_with_config(
            ctx.config,
            app,
            ["quotes", "--refresh", "-t", "TESTTKR"],
        )

        assert_cli_success(result)
        assert_in_output("Fetched 1 quote(s)", result)


def test_the_symbol_override_reaches_the_provider(temp_ctx: TempContext) -> None:
    """A configured override must survive the whole config-to-fetch path."""
    with temp_ctx(quotes={"symbol_overrides": {"TESTTKR": "OTHER"}}) as ctx:
        _seed_two_types()
        _assert_override_applies(ctx.config)


def _assert_override_applies(config: Config) -> None:
    """Check the resolver built from config carries the override through."""
    bootstrap.reload_config(config.project_root)
    resolver = SymbolResolver([], config.quotes_symbol_overrides)
    assert resolver.yahoo_symbol("TESTTKR") == "OTHER"
