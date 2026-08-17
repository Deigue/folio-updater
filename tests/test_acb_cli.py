"""Tests for `folio acb` and `folio symbol`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from cli.commands.acb import (
    _FALL,
    _RISE,
    AcbView,
    _arrow,
    _format,
    _units,
    movements,
)
from cli.console import supports_unicode
from cli.main import app
from engine.cache import fingerprint, load_or_build
from engine.frames import scope_column
from utils.constants import Column, Scope

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from .test_types import TempContext

FX = {
    "2025-08-14": "1.3500",
    "2025-08-15": "1.3600",
    "2025-08-18": "1.3700",
}


def seed_cad_holding(account: str = "IBKR-PERSONAL", ticker: str = "RY.TO") -> None:
    """Seed a plain CAD buy-then-sell in one account."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-1000",
        price="10",
        units="100",
    )
    seed_transaction(
        action="SELL",
        date="2025-08-15",
        settle_date="2025-08-15",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="600",
        price="12",
        units="-50",
    )


def seed_usd_holding(account: str = "IBKR-PERSONAL", ticker: str = "MSFT") -> None:
    """Seed a USD buy, so the `_USD` column family has something in it."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="USD",
        ticker=ticker,
        amount="-1000",
        price="100",
        units="10",
    )


def seed_superficial_loss(
    account: str = "IBKR-PERSONAL",
    ticker: str = "RY.TO",
) -> None:
    """Seed a loss sale followed by a repurchase inside the CRA window."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-1000",
        price="10",
        units="100",
    )
    seed_transaction(
        action="SELL",
        date="2025-08-15",
        settle_date="2025-08-15",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="400",
        price="8",
        units="-50",
    )
    seed_transaction(
        action="BUY",
        date="2025-08-18",
        settle_date="2025-08-18",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-400",
        price="8",
        units="50",
    )


def test_acb_renders_a_buildup(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("RY.TO", result)
    assert_in_output("non-registered", result)
    assert_in_output("BUY", result)
    assert_in_output("SELL", result)
    # Money is asserted against the frame in test_acb_replay instead
    assert_in_output("Held", result)
    assert_in_output("ACB", result)


def test_acb_suppresses_usd_columns_for_a_cad_holding(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_not_in_output("USD", result)
    assert_not_in_output("Rate", result)


def test_acb_shows_both_currencies_for_a_usd_holding(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "MSFT"])
    assert_cli_success(result)
    # Headers wrap when both variants render, so the USD family shows as a
    # second header line rather than an inline suffix.
    assert_in_output("USD", result)
    assert_in_output("Rate", result)
    assert_in_output("1.3500", result)  # the settle-date rate that converted it


def test_acb_currency_usd_against_a_cad_holding_is_an_error(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--currency", "USD"],
        )
    assert result.exit_code == 1
    assert_in_output("Nothing here is USD-denominated", result)


def test_acb_reports_the_conversion_basis(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "MSFT"])
    assert_in_output("settle date", result)


def test_acb_account_scope(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding(account="IBKR-TFSA")
        seed_transaction(
            action="BUY",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="WS-TFSA",
            currency="CAD",
            ticker="RY.TO",
            amount="-5000",
            price="10",
            units="500",
        )
        single = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--account", "IBKR-TFSA"],
        )
        pooled = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--type", "tfsa"],
        )
    assert_cli_success(single)
    assert_cli_success(pooled)
    assert_in_output("IBKR-TFSA", single)
    assert_in_output("tfsa", pooled)


def test_acb_folio_scope(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding(account="IBKR-TFSA")
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO", "--folio"])
    assert_cli_success(result)
    assert_in_output("portfolio", result)


def test_acb_unknown_type_errors(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--type", "banana"],
        )
    assert result.exit_code == 1
    assert_in_output("Unknown account type", result)


def test_acb_hides_income_rows_unless_all(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_transaction(
            action="DIVIDEND",
            date="2025-08-18",
            settle_date="2025-08-18",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="25",
            price=None,
            units=None,
        )
        without = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
        with_all = run_cli_with_config(ctx.config, app, ["acb", "RY.TO", "--all"])
    assert_not_in_output("DIVIDEND", without)
    assert_in_output("DIVIDEND", with_all)


def test_acb_year_filter(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--year", "2020"],
        )
    assert_cli_success(result)
    assert_in_output("No RY.TO transactions", result)


def test_acb_summary(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_usd_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_in_output("ACB summary", result)
    assert_in_output("RY.TO", result)
    assert_in_output("MSFT", result)


def test_acb_summary_shows_usd_columns_only_when_something_is_usd(
    temp_ctx: TempContext,
) -> None:
    """One USD holding earns the `_USD` columns; a CAD-only pool does not."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        cad_only = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_usd_holding()
        mixed = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(cad_only)
    assert_cli_success(mixed)
    assert_not_in_output("USD", cad_only)
    assert_in_output("USD", mixed)


def test_acb_summary_blanks_a_closed_positions_zeros(temp_ctx: TempContext) -> None:
    """A fully closed position reports its gain, not three columns of zero."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        # Sell the remaining 50 units, taking the position to exactly zero.
        seed_transaction(
            action="SELL",
            date="2025-08-18",
            settle_date="2025-08-18",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="700",
            price="14",
            units="-50",
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_in_output("RY.TO", result)
    # Units, ACB and Avg all land on exactly zero here; only the gain survives.
    assert_not_in_output("0.0000", result)


def test_acb_summary_sinks_closed_positions_to_the_bottom(
    temp_ctx: TempContext,
) -> None:
    """What is still held comes first; history goes underneath."""
    with temp_ctx() as ctx:
        # AAA closes out entirely; ZZZ is still held. Alphabetically AAA leads,
        # so any ordering that keeps it on top has ignored the position.
        seed_cad_holding(ticker="AAA")
        seed_transaction(
            action="SELL",
            date="2025-08-18",
            settle_date="2025-08-18",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="AAA",
            amount="700",
            price="14",
            units="-50",
        )
        seed_cad_holding(ticker="ZZZ")
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    output = result.plain_output
    assert output.index("ZZZ") < output.index("AAA")


def test_acb_summary_usd_only_drops_cad_holdings(temp_ctx: TempContext) -> None:
    """A CAD holding asked for USD figures alone renders as a blank row."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_usd_holding()
        both = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
        usd = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "--summary", "--currency", "USD"],
        )
    assert_cli_success(both)
    assert_cli_success(usd)
    # The CAD holding belongs in the default view
    assert_in_output("RY.TO", both)
    assert_in_output("MSFT", both)
    # ... but missing when only USD columns are rendered.
    assert_not_in_output("RY.TO", usd)
    assert_in_output("MSFT", usd)


def test_acb_summary_cad_keeps_usd_holdings(temp_ctx: TempContext) -> None:
    """The CAD view is the tax view, so a USD holding still belongs in it."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_usd_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "--summary", "--currency", "CAD"],
        )
    assert_cli_success(result)
    assert_in_output("RY.TO", result)
    assert_in_output("MSFT", result)


def test_acb_requires_a_symbol_without_summary_or_export(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb"])
    assert result.exit_code == 1
    assert_in_output("SYMBOL is required", result)


def test_acb_export_writes_a_file(temp_ctx: TempContext, tmp_path: Path) -> None:
    target = tmp_path / "acb.csv"
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--export", str(target)],
        )
        assert_cli_success(result)
        # Asserted inside the context: `temp_ctx` sweeps *.csv on the way out.
        assert target.exists()
        assert "AcctACB" in target.read_text(encoding="utf-8")


def test_acb_flags_a_seeded_oversell(temp_ctx: TempContext) -> None:
    """A diagnostic reaches the table and the footer, and still exits zero.

    Reporting a problem is not the same as failing: `folio acb` is a reporting
    command, and gating a build on the folio's health belongs to `folio check`.
    """
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="SELL",
            date="2025-08-15",
            settle_date="2025-08-15",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="600",
            price="12",
            units="-50",
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("OVERSELL", result)


def test_acb_flags_a_superficial_loss_suspect(temp_ctx: TempContext) -> None:
    """A loss sale followed by a repurchase in the CRA window is highlighted."""
    with temp_ctx() as ctx:
        seed_superficial_loss()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("SUPERFICIAL_LOSS_SUSPECT", result)
    assert_in_output("⚠" if supports_unicode() else "!", result)


def test_acb_summary_omits_superficial_loss_suspect(temp_ctx: TempContext) -> None:
    """The pooled summary can't act on a per-lot flag, so it stays out of it.

    Left in, `SUPERFICIAL_LOSS_SUSPECT` would attach to a whole symbol with no
    way to say which sale it was on -- the per-transaction buildup is where it
    belongs.
    """
    with temp_ctx() as ctx:
        seed_superficial_loss()
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_not_in_output("SUPERFICIAL_LOSS_SUSPECT", result)


def test_acb_reports_freshness(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        first = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
        second = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_in_output("computed just now", first)
    assert_in_output("cached", second)


# --- cache -------------------------------------------------------------------


def test_cache_hit_and_invalidation(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        built = load_or_build()
        assert built.computed_at is None
        assert built.result is not None

        cached = load_or_build()
        assert cached.from_cache
        assert len(cached.frame) == len(built.frame)

        # A new transaction moves the fingerprint.
        before = fingerprint()
        seed_transaction(
            action="BUY",
            date="2025-08-18",
            settle_date="2025-08-18",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="-200",
            price="20",
            units="10",
        )
        assert fingerprint() != before
        rebuilt = load_or_build()
        assert not rebuilt.from_cache
        assert len(rebuilt.frame) == len(built.frame) + 1
        assert ctx.config.acb_parquet.exists()


def test_refresh_rebuilds_a_valid_cache(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        load_or_build()
        assert load_or_build().from_cache
        assert not load_or_build(refresh=True).from_cache
        assert ctx is not None


def test_config_change_invalidates_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        plain = fingerprint()
        assert ctx is not None
    overrides = {"accounts": {"map": {"IBKR-PERSONAL": "CORPORATE"}}}
    with temp_ctx(overrides):
        seed_cad_holding()
        assert fingerprint() != plain


# --- folio symbol --------------------------------------------------------------


def test_symbol_add_list_delete(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        added = run_cli_with_config(
            ctx.config,
            app,
            ["symbol", "--add", "SPLG", "SPYM", "2025-10-31"],
        )
        listed = run_cli_with_config(ctx.config, app, ["symbol", "--list"])
        deleted = run_cli_with_config(ctx.config, app, ["symbol", "--delete", "SPLG"])
        empty = run_cli_with_config(ctx.config, app, ["symbol", "--list"])

    assert_cli_success(added)
    assert_in_output("SPYM", listed)
    assert_cli_success(deleted)
    assert_in_output("No ticker aliases", empty)


def test_acb_resolves_a_renamed_symbol(temp_ctx: TempContext) -> None:
    """Rows written before the rename pool under the new name."""
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="BUY",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="SPLG",
            amount="-1000",
            price="10",
            units="100",
        )
        run_cli_with_config(
            ctx.config,
            app,
            ["symbol", "--add", "SPLG", "SPYM", "2025-10-31"],
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "SPLG", "--refresh"])
    assert_cli_success(result)
    assert_in_output("SPYM", result)


def test_acb_currency_cad_forces_one_variant(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "MSFT", "--currency", "CAD"],
        )
    assert_cli_success(result)
    assert_not_in_output("Rate", result)


def test_acb_currency_usd_forces_one_variant(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "MSFT", "--currency", "USD"],
        )
    assert_cli_success(result)
    assert_in_output("Rate", result)


def test_acb_export_to_parquet(temp_ctx: TempContext, tmp_path: Path) -> None:
    target = tmp_path / "acb_export.parquet"
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--export", str(target)],
        )
        assert_cli_success(result)
        assert target.exists()


def test_acb_on_an_empty_folio(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("No transactions", result)


def test_acb_summary_with_no_holdings(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="CONTRIBUTION",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker=None,
            amount="5000",
            price=None,
            units=None,
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_in_output("No holdings", result)


def test_acb_skips_the_fx_fetch_when_auto_getfx_is_off(temp_ctx: TempContext) -> None:
    with temp_ctx({"cost_basis": {"auto_getfx": False}}) as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)


# --- table presentation -----------------------------------------------------------
#
# Display only: the replay still walks trade dates, and none of these change a
# reported number.


def seed_dated_holding() -> None:
    """Seed a buy whose settle date falls in the month after its trade date."""
    seed_fx({**FX, "2025-09-02": "1.3800"})
    seed_transaction(
        action="BUY",
        date="2025-08-29",
        settle_date="2025-09-02",
        account="IBKR-PERSONAL",
        currency="CAD",
        ticker="RY.TO",
        amount="-1000",
        price="10",
        units="100",
    )


def test_the_date_column_shows_the_settle_date(temp_ctx: TempContext) -> None:
    """The settle date is the one the cash and the FX rate belong to."""
    with temp_ctx() as ctx:
        seed_dated_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("Settle", result)
    assert_in_output("2025-09-02", result)
    assert_not_in_output("2025-08-29", result)


def test_rows_run_newest_first(temp_ctx: TempContext) -> None:
    """Reverse chronological, the way adjustedcostbase.ca lists them."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    output = result.plain_output
    # The sale settles after the purchase, so it has to come first.
    assert output.index("SELL") < output.index("BUY")


def test_meaningless_zeros_are_blanked() -> None:
    """A buy realizes no gain; printing 0.00 on every such row is noise."""
    assert _format(0.0, 2, blank_zero=True) == ""
    assert _format(-0.0, 2, blank_zero=True) == ""
    assert _units(0.0, blank_zero=True) == ""
    assert _format(0.0, 2) == "0.00"
    assert _format(1000.0, 2, blank_zero=True) == "1,000.00"


def test_a_negative_zero_never_renders_as_a_debit() -> None:
    assert _format(-0.0, 2) == "0.00"


def test_the_average_cost_arrow_tracks_the_chronological_move(
    temp_ctx: TempContext,
) -> None:
    """Arrows are computed oldest-first, so they survive the newest-first flip."""
    with temp_ctx() as ctx:
        seed_fx(FX)
        for day, price, units in (("14", "10", "100"), ("15", "20", "100")):
            seed_transaction(
                action="BUY",
                date=f"2025-08-{day}",
                settle_date=f"2025-08-{day}",
                account="IBKR-PERSONAL",
                currency="CAD",
                ticker="RY.TO",
                amount=f"-{int(price) * int(units)}",
                price=price,
                units=units,
            )
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    # Average cost rose from 10 to 15, and the arrow belongs on the later row.
    # Newest first, so the row whose average rose is the first of the two.
    lines = [ln for ln in result.plain_output.splitlines() if "BUY" in ln]
    assert len(lines) == 2
    assert _RISE in lines[0]
    assert _RISE not in lines[1]


def test_movements_track_each_currency_separately() -> None:
    """A rate move can push the CAD average one way and the USD one the other."""
    frame = pd.DataFrame(
        {
            str(Column.Txn.TXN_ID): [1, 2],
            scope_column(Scope.ACCOUNT, "Avg"): [100.0, 110.0],
            scope_column(Scope.ACCOUNT, "Avg_USD"): [80.0, 70.0],
        },
    )
    view = AcbView(Scope.ACCOUNT, "IBKR-PERSONAL", "IBKR-PERSONAL")
    moves = movements(frame, view)
    assert moves[("Avg", 2)] == 1
    assert moves[("Avg_USD", 2)] == -1
    # The first row has nothing to compare against.
    assert ("Avg", 1) not in moves


def test_movements_track_the_running_position() -> None:
    frame = pd.DataFrame(
        {
            str(Column.Txn.TXN_ID): [1, 2, 3],
            scope_column(Scope.ACCOUNT, "Units"): [100.0, 150.0, 90.0],
        },
    )
    view = AcbView(Scope.ACCOUNT, "IBKR-PERSONAL", "IBKR-PERSONAL")
    moves = movements(frame, view)
    assert moves[("Units", 2)] == 1
    assert moves[("Units", 3)] == -1


def test_a_measure_that_holds_still_is_not_marked() -> None:
    """A split leaves the cost base alone; nothing should suggest it moved."""
    frame = pd.DataFrame(
        {
            str(Column.Txn.TXN_ID): [1, 2],
            scope_column(Scope.ACCOUNT, "Units"): [100.0, 100.0],
        },
    )
    view = AcbView(Scope.ACCOUNT, "IBKR-PERSONAL", "IBKR-PERSONAL")
    assert movements(frame, view) == {}


def test_only_the_arrow_is_tinted_and_green_still_means_up() -> None:
    """The figure stays plain: a falling average is not automatically good."""
    assert _arrow(1) == f"[green]{_RISE}[/green]"
    assert _arrow(-1) == f"[red]{_FALL}[/red]"
    assert _arrow(None) == ""
    assert _arrow(0) == ""
