"""Tests for `folio update`: which stages run, and what a failure leaves behind."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pandas as pd
import pytest

from cli.commands.common import save_update_baseline
from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import txns, update_baselines
from domain import BrokerId, Grain
from services import BrokerError, BrokerFailure, PartFailure

from .fixtures.data_factory import create_transaction_data
from .fixtures.dataframe_cache import register_test_dataframe
from .helpers.cli import assert_in_output, assert_not_in_output, run_cli
from .helpers.folio import txn_total
from .helpers.seed import TSX_TICKER, seed_cash, seed_transaction

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from .fixtures.fake_broker import FakeBrokers
    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.context import TempContext

IBKR_FILE = "ibkr_TradeConfirmation_20260901_20261003.csv"
WS_ACCOUNT = "WS-TFSA"
WS_FILE = "ws_activities_20260901_20261003.xlsx"
OFFLINE = "offline"

# A failed import closes its audit entry with an error, under the runner.
pytestmark = pytest.mark.usefixtures("quiet_logging")


def _ibkr(kind: BrokerFailure, detail: str) -> BrokerError:
    return BrokerError(BrokerId.IBKR, kind, detail)


def _ws(kind: BrokerFailure, detail: str) -> BrokerError:
    return BrokerError(BrokerId.WS, kind, detail)


@pytest.mark.parametrize(
    ("ibkr_failure", "ibkr_error", "ws_error", "hint"),
    [
        (
            _ibkr(BrokerFailure.CREDENTIALS_EXPIRED, "IBKR API Error 1012"),
            None,
            None,
            "folio download -b ibkr --credentials",
        ),
        (
            _ibkr(BrokerFailure.BUSY, "Statement not ready after 10 attempts."),
            None,
            None,
            "wait a few minutes",
        ),
        (
            None,
            _ibkr(BrokerFailure.NOT_CONFIGURED, "No IBKR flex queries configured"),
            None,
            "Set up IBKR under brokers in config.yaml",
        ),
        (
            None,
            None,
            _ws(BrokerFailure.LOGIN_FAILED, "Login failed"),
            "folio download -b wealthsimple --credentials",
        ),
        (
            None,
            None,
            _ws(BrokerFailure.NOT_SAVED, "Permission denied"),
            "Close the file if another program has it open",
        ),
    ],
    ids=["ibkr-token-expired", "ibkr-busy", "ibkr-unset", "ws-login", "ws-not-saved"],
)
def test_failed_download_stops_and_cleans_up(
    temp_ctx: TempContext,
    brokers: FakeBrokers,
    ibkr_failure: BrokerError | None,
    ibkr_error: BrokerError | None,
    ws_error: BrokerError | None,
    hint: str,
) -> None:
    """A broker failure stops before import and removes only this run's files."""
    with temp_ctx() as ctx:
        config = ctx.config
        earlier = config.imports_path / "earlier_download.csv"
        earlier.touch()
        brokers.ibkr.files = (IBKR_FILE,)
        if ibkr_failure is not None:
            brokers.ibkr.failures = (PartFailure("CashActivity", ibkr_failure),)
        brokers.ibkr.error = ibkr_error
        brokers.wealthsimple.error = ws_error

        result = run_cli(cli_app, ["update"])

        assert result.exit_code == 1
        assert not (config.imports_path / IBKR_FILE).exists()
        assert earlier.exists()  # not this run's, and never imported
        assert_in_output("Update stopped at Download", result)
        assert_in_output("Removed 1 file(s) this run downloaded", result)
        assert_in_output(hint, result)
        assert_not_in_output("Folio workbook", result)
        # A broker that failed stops the run before the next is asked.
        ibkr_failed = ibkr_failure is not None or ibkr_error is not None
        assert len(brokers.wealthsimple.downloads) == (not ibkr_failed)


def test_failed_import_stops_and_keeps_file(temp_ctx: TempContext) -> None:
    """A file that cannot be imported stays put, with the way back spelled out."""
    with temp_ctx() as ctx:
        config = ctx.config
        broken = config.imports_path / IBKR_FILE
        broken.touch()  # nothing registered for it, so reading it fails

        result = run_cli(cli_app, ["update", "--resume"])

        assert result.exit_code == 1
        assert broken.exists()
        assert_in_output("Update stopped at Import", result)
        assert_in_output("folio update --resume", result)
        assert_in_output("folio download -b ibkr -f 2026-09-01 -t 2026-10-03", result)
        assert_not_in_output("Folio workbook", result)
        # A failed run moves nothing, so its rows still count as new next time.
        assert update_baselines.read() == []


def test_a_database_error_stops_the_import_with_the_way_back(
    temp_ctx: TempContext,
) -> None:
    """The file waits for a retry, instead of the error ending the whole update."""
    with temp_ctx() as ctx:
        config = ctx.config
        waiting = config.imports_path / WS_FILE
        create_transaction_data(waiting)

        with patch.object(
            txns,
            "append",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            result = run_cli(cli_app, ["update", "--resume"])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert waiting.exists()
        assert_in_output("Update stopped at Import", result)
        assert_in_output("database is locked", result)
        assert_in_output("folio update --resume", result)


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

        result = run_cli(cli_app, ["update", "--resume"])

        after = txn_total()
        kept = download.exists()

    assert result.exit_code == 1
    assert_in_output("Update stopped at Import", result)
    assert_in_output("folio update --resume, and answer yes", result)
    assert after == before
    assert kept


@pytest.mark.parametrize(
    ("statement_error", "named"),
    [(OFFLINE, OFFLINE), ("", "unexpected response")],
    ids=["with-a-message", "without-a-message"],
)
def test_clean_run_goes_through_every_stage(
    temp_ctx: TempContext,
    brokers: FakeBrokers,
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

        brokers.wealthsimple.files = (WS_FILE,)
        brokers.wealthsimple.write = create_transaction_data
        brokers.wealthsimple.statement_error = BrokerError(
            BrokerId.WS,
            BrokerFailure.UNEXPECTED_RESPONSE,
            statement_error,
        )

        result = run_cli(cli_app, ["update"])

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
        folio = next(b for b in update_baselines.read() if b.grain is Grain.FOLIO)
        assert folio.txn_count == txn_total()


def test_an_update_of_an_empty_folio_saves_no_baseline(temp_ctx: TempContext) -> None:
    """With nothing to value there is nothing to count changes from."""
    with temp_ctx():
        result = run_cli(cli_app, ["update", "--offline"])

        assert result.exit_code == 0
        assert update_baselines.read() == []
        assert_not_in_output("baseline saved", result)


def test_offline_imports_what_waits_and_touches_no_network(
    temp_ctx: TempContext,
    brokers: FakeBrokers,
    quote_provider: FakeQuoteProvider,
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
        # The rates a folio already holds are all an offline run can convert with.
        run_cli(cli_app, ["getfx"])
        boc_fetch.reset_mock()

        result = run_cli(cli_app, ["update", "--offline"])

        assert result.exit_code == 0
        assert (config.processed_path / WS_FILE).exists()
        assert config.folio_path.exists()
        assert not quote_provider.calls
        assert not boc_fetch.called
        assert brokers.ibkr.downloads == brokers.wealthsimple.downloads == []
        assert brokers.wealthsimple.statement_months == []
        assert_in_output("statement not fetched offline", result)


def test_a_second_update_reports_what_changed_since_the_first(
    temp_ctx: TempContext,
) -> None:
    """The deltas count from the earlier baseline, which the run then replaces."""
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction(account=WS_ACCOUNT, ticker=f"{TSX_TICKER}.TO", currency="CAD")
        run_cli(cli_app, ["getfx"])
        save_update_baseline()
        create_transaction_data(config.imports_path / WS_FILE)

        result = run_cli(cli_app, ["update", "--offline"])

        assert result.exit_code == 0
        assert_in_output("Since the last update", result)
        assert_in_output("+2 txns", result)
        folio = next(b for b in update_baselines.read() if b.grain is Grain.FOLIO)
        assert folio.txn_count == txn_total()


def test_statement_on_hand_is_imported_not_fetched(
    temp_ctx: TempContext,
    brokers: FakeBrokers,
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
        result = run_cli(cli_app, ["update", "--resume"])

        assert result.exit_code == 0
        assert brokers.wealthsimple.statement_months == ["2025-07"]
        assert not statement.exists()
        assert (config.statements_processed_path / statement.name).exists()
        assert_in_output("Nothing new to import", result)
        assert_in_output("Settlement dates confirmed (1)", result)
        assert_in_output("settlement date(s) the statements could not confirm", result)
        assert_in_output("2025-07 statement not published yet", result)
