"""Tests for `folio update`: which stages run, and what a failure leaves behind."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from cli.main import app as cli_app
from datagen import ensure_data_exists
from services import WealthsimpleServiceError
from services.wealthsimple_service import WealthsimpleAuthenticationError

from .fixtures.data_factory import create_transaction_data
from .fixtures.dataframe_cache import register_test_dataframe
from .helpers.cli import assert_in_output, assert_not_in_output, run_cli_with_config
from .helpers.folio import txn_total
from .helpers.seed import TSX_TICKER, seed_cash, seed_transaction

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import MagicMock

    from .helpers.context import TempContext

IBKR_FILE = "ibkr_TradeConfirmation_20260901_20261003.csv"
WS_ACCOUNT = "WS-TFSA"
WS_FILE = "ws_activities_20260901_20261003.xlsx"
OFFLINE = "offline"


def _no_call(*_args: object, **_kwargs: object) -> None:
    """Stand in for a stage that must not run."""
    msg = "this download should not have run"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("ibkr_failures", "ws_error", "hint"),
    [
        (
            ["CashActivity: IBKR API Error 1012: Token has expired."],
            None,
            "folio download -b ibkr --credentials",
        ),
        (
            ["CashActivity: IBKR API Error 1019: Statement generation in progress."],
            None,
            "wait a few minutes",
        ),
        (
            [],
            "Login failed to authenticate",
            "folio download -b wealthsimple --credentials",
        ),
    ],
    ids=["ibkr-token-expired", "ibkr-busy", "ws-login"],
)
def test_failed_download_stops_and_cleans_up(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    ibkr_failures: list[str],
    ws_error: str | None,
    hint: str,
) -> None:
    """A broker failure stops before import and removes only this run's files."""
    with temp_ctx() as ctx:
        config = ctx.config
        earlier = config.imports_path / "earlier_download.csv"
        earlier.touch()

        def ibkr(*_args: object) -> list[str]:
            (config.imports_path / IBKR_FILE).write_text("partial")
            return ibkr_failures

        def wealthsimple(*_args: object) -> None:
            raise WealthsimpleAuthenticationError(ws_error)

        monkeypatch.setattr("cli.commands.update.download_ibkr", ibkr)
        monkeypatch.setattr(
            "cli.commands.update.download_wealthsimple",
            wealthsimple if ws_error else _no_call,
        )

        result = run_cli_with_config(config, cli_app, ["update"])

        assert result.exit_code == 1
        assert not (config.imports_path / IBKR_FILE).exists()
        assert earlier.exists()  # not this run's, and never imported
        assert_in_output("Update stopped at Download", result)
        assert_in_output("Removed 1 file(s) this run downloaded", result)
        assert_in_output(hint, result)
        assert_not_in_output("Folio workbook", result)


def test_failed_import_stops_and_keeps_file(temp_ctx: TempContext) -> None:
    """A file that cannot be imported stays put, with the way back spelled out."""
    with temp_ctx() as ctx:
        config = ctx.config
        broken = config.imports_path / IBKR_FILE
        broken.touch()  # nothing registered for it, so reading it fails

        result = run_cli_with_config(config, cli_app, ["update", "--resume"])

        assert result.exit_code == 1
        assert broken.exists()
        assert_in_output("Update stopped at Import", result)
        assert_in_output("folio update --resume", result)
        assert_in_output("folio download -b ibkr -f 2026-09-01 -t 2026-10-03", result)
        assert_not_in_output("Folio workbook", result)


def test_an_update_over_the_demo_stops_until_you_replace_it(
    temp_ctx: TempContext,
) -> None:
    """Real downloads are not mixed into the demo: with no yes, nothing moves."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()
        before = txn_total()
        download = config.imports_path / WS_FILE
        create_transaction_data(download)

        result = run_cli_with_config(config, cli_app, ["update", "--resume"])

        after = txn_total()
        kept = download.exists()

    assert result.exit_code == 1
    assert_in_output("Update stopped at Import", result)
    assert_in_output("folio update --resume, and answer yes", result)
    assert after == before
    assert kept


@pytest.mark.parametrize(
    ("statement_error", "named"),
    [(OFFLINE, OFFLINE), ("", "WealthsimpleServiceError")],
    ids=["with-a-message", "without-a-message"],
)
def test_clean_run_goes_through_every_stage(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    statement_error: str,
    named: str,
) -> None:
    """Downloads that succeed carry on through every stage to the workbook.

    The statement download failing is a concern, not a stop, and names what
    failed even when the error has nothing to say for itself.
    """
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction(account=WS_ACCOUNT)  # a calculated date from a past month
        seed_cash("TFR_OUT", amount="-100", account=WS_ACCOUNT)

        def ibkr(*_args: object) -> list[str]:
            return []

        def wealthsimple(*_args: object) -> None:
            create_transaction_data(config.imports_path / WS_FILE)

        def offline_statement(_month_start: str) -> list[Path]:
            raise WealthsimpleServiceError(statement_error)

        monkeypatch.setattr("cli.commands.update.download_ibkr", ibkr)
        monkeypatch.setattr("cli.commands.update.download_wealthsimple", wealthsimple)
        monkeypatch.setattr(
            "cli.commands.update.download_ws_statement",
            offline_statement,
        )

        result = run_cli_with_config(config, cli_app, ["update"])

        assert result.exit_code == 0
        assert config.folio_path.exists()
        assert (config.processed_path / WS_FILE).exists()
        assert_in_output("New transactions (2)", result)
        assert_in_output("statement download failed", result)
        assert_in_output(f"{named}. Its settlement dates stay", result)
        # No later run clears a warning, so it is expected rather than pending.
        expected = result.plain_output.partition("Expected, nothing to do")[2]
        assert "only warned: folio check" in expected
        assert_in_output("Folio workbook:", result)
        assert_in_output("Update finished with", result)


def test_offline_imports_what_waits_and_touches_no_network(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    quotes_fetch: MagicMock,
    boc_fetch: MagicMock,
) -> None:
    """Offline, nothing is downloaded or fetched, yet the run still finishes."""
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction(
            account=WS_ACCOUNT,
            ticker=f"{TSX_TICKER}.TO",
            currency="CAD",
        )  # a calculated date from a past month, with no statement on hand
        create_transaction_data(config.imports_path / WS_FILE)
        for download in (
            "download_ibkr",
            "download_wealthsimple",
            "download_ws_statement",
        ):
            monkeypatch.setattr(f"cli.commands.update.{download}", _no_call)
        # The rates a folio already holds are all an offline run can convert with.
        run_cli_with_config(config, cli_app, ["getfx"])
        boc_fetch.reset_mock()

        result = run_cli_with_config(config, cli_app, ["update", "--offline"])

        assert result.exit_code == 0
        assert (config.processed_path / WS_FILE).exists()
        assert config.folio_path.exists()
        assert not quotes_fetch.called
        assert not boc_fetch.called
        assert_in_output("statement not fetched offline", result)


def test_statement_on_hand_is_imported_not_fetched(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A month waiting in the statements folder is imported, not fetched.

    August's statement is on disk: it confirms one date, leaves another stuck,
    and is archived once read. July's is fetched and is not out yet.
    """
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction(
            account=WS_ACCOUNT,
            ticker=f"{TSX_TICKER}.TO",
            currency="CAD",
        )
        seed_transaction(account=WS_ACCOUNT, date="2025-08-14")  # no statement row
        seed_transaction(account=WS_ACCOUNT, date="2025-07-15")
        statement = config.statements_path / f"ws_statement_{WS_ACCOUNT}_202508.csv"
        register_test_dataframe(
            statement,
            pd.DataFrame(
                {
                    "date": ["2025-08-18"],
                    "amount": ["-1502.50"],
                    "currency": ["CAD"],
                    "transaction": ["BUY"],
                    "description": [
                        (
                            f"{TSX_TICKER} - Test Co: Bought 10.0000 shares "
                            "(executed at 2025-08-15)"
                        ),
                    ],
                },
            ),
        )
        fetched: list[str] = []

        def unpublished(month_start: str) -> list[Path]:
            fetched.append(month_start)
            return []

        monkeypatch.setattr(
            "cli.commands.update.download_ws_statement",
            unpublished,
        )

        result = run_cli_with_config(config, cli_app, ["update", "--resume"])

        assert result.exit_code == 0
        assert fetched == ["2025-07-01"]
        assert not statement.exists()
        assert (config.statements_processed_path / statement.name).exists()
        assert_in_output("Nothing new to import", result)
        assert_in_output("Settlement dates confirmed (1)", result)
        assert_in_output("settlement date(s) the statements could not confirm", result)
        assert_in_output("2025-07 statement not published yet", result)
