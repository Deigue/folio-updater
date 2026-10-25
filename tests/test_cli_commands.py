"""Tests for CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest
import ws_api
from rich.progress import Progress
from typer.testing import CliRunner

from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import create_txns_table, get_connection, get_row_count, get_rows
from domain import Column, Table
from services.ibkr_service import IBKRAuthenticationError
from term.console import active_console

from .fixtures.data_factory import create_transaction_data
from .fixtures.dataframe_cache import register_test_dataframe
from .fixtures.ibkr_mocking import (
    IBKRMockContext,
    get_default_mock_csv,
)
from .fixtures.wealthsimple_mocking import (
    WealthsimpleMockContext,
    create_mock_activities,
    get_expected_statement_txn_csv,
    get_expected_wealthsimple_csv,
    get_mock_activities,
    get_mock_statement_transactions,
    mock_account,
)
from .helpers.cli import (
    UNCONSTRAINED_WIDTH,
    assert_cli_success,
    assert_in_output,
    run_cli_with_config,
)
from .helpers.seed import (
    STATEMENT_PRICE,
    STATEMENT_TRADE_DATE,
    TSX_TICKER,
    TXN_DATE,
    seed_transaction,
    statement_buy,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .helpers.context import TempContext

runner = CliRunner()

EXPECTED_TRANSACTION_COUNT = 2
# A statement description names its symbol in at most five letters.
STATEMENT_TICKER = "STMTK"


pytestmark = pytest.mark.usefixtures("quiet_logging")


@pytest.fixture(autouse=True)
def patch_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture to patch Rich Progress to be non-transient and use test console."""
    original_init = Progress.__init__

    def new_init(self: Progress, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        kwargs["console"] = active_console()
        kwargs["transient"] = False  # Ensure progress bars are not cleared
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(Progress, "__init__", new_init)


def test_import_command(temp_ctx: TempContext) -> None:
    """Test import command default behavior."""
    with temp_ctx() as ctx:
        config = ctx.config
        txn_file = config.imports_path / "transactions.xlsx"
        create_transaction_data(txn_file, config.txn_sheet)
        cli_result = run_cli_with_config(config, cli_app, ["import"])
        assert_cli_success(cli_result)
        assert_in_output(
            f"{EXPECTED_TRANSACTION_COUNT} transactions imported",
            cli_result,
        )
        with get_connection() as conn:
            count = get_row_count(conn, Table.TXNS)
            assert count == EXPECTED_TRANSACTION_COUNT


def test_import_command_without_files_to_import(temp_ctx: TempContext) -> None:
    """Nothing in the imports folder is refused, naming what it looked for."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert not config.folio_path.exists()
        cli_result = run_cli_with_config(config, cli_app, ["import"])
        assert cli_result.exit_code == 1
        assert_in_output("No supported files found", cli_result)


@pytest.mark.real_parquet_export
def test_import_command_file(temp_ctx: TempContext) -> None:
    """Test import command with specific file option."""
    with temp_ctx() as ctx:
        config = ctx.config
        test_file = config.project_root / "test_import.xlsx"
        create_transaction_data(test_file)
        cli_result = run_cli_with_config(
            config,
            cli_app,
            ["import", "--file", str(test_file)],
        )
        assert_cli_success(cli_result)
        # Verify via stdout keywords, core functionality is tested elsewhere.
        assert_in_output(f"SUCCESS: {test_file.name}", cli_result)
        assert_in_output(
            f"{EXPECTED_TRANSACTION_COUNT} transactions imported",
            cli_result,
        )
        assert_in_output(
            f"Exported {EXPECTED_TRANSACTION_COUNT} transactions to Parquet",
            cli_result,
        )
        processed_folder = config.processed_path
        assert processed_folder.exists()
        assert (processed_folder / test_file.name).exists()
        assert not test_file.exists()


@pytest.mark.real_parquet_export
def test_import_command_directory(temp_ctx: TempContext) -> None:
    """Test import command with directory option."""
    with temp_ctx() as ctx:
        config = ctx.config
        import_dir = config.imports_path
        file1 = import_dir / "transactions1.xlsx"
        file2 = import_dir / "transactions2.xlsx"
        file_count: int = 2
        create_transaction_data(file1)
        create_transaction_data(file2)
        cli_result = run_cli_with_config(
            config,
            cli_app,
            ["import", "--dir", str(import_dir)],
        )
        assert_cli_success(cli_result)
        assert_in_output("Found 2 files to import", cli_result)
        txn_count = EXPECTED_TRANSACTION_COUNT * file_count
        assert_in_output(f"Total transactions imported: {txn_count}", cli_result)
        assert_in_output(f"Exported {txn_count} transactions to Parquet", cli_result)
        processed_folder = config.processed_path
        assert processed_folder.exists()
        assert (processed_folder / file1.name).exists()
        assert (processed_folder / file2.name).exists()
        assert not file1.exists()
        assert not file2.exists()


def test_importing_several_files_backs_the_folio_up_once(
    temp_ctx: TempContext,
) -> None:
    """Every file writes, but only the folio as it stood before the command is kept."""
    with temp_ctx(backup={"enabled": True}) as ctx:
        config = ctx.config
        seed_transaction()
        create_transaction_data(config.imports_path / "transactions1.xlsx")
        create_transaction_data(config.imports_path / "transactions2.xlsx")

        cli_result = run_cli_with_config(config, cli_app, ["import"])
        backups = sorted(p.name for p in config.backup_path.rglob("folio_*.db"))

    assert_in_output(
        f"Total transactions imported: {EXPECTED_TRANSACTION_COUNT * 2}",
        cli_result,
    )
    # The name ends in the count of transactions it holds: the one seeded.
    assert len(backups) == 1
    assert backups[0].endswith("_1.db")


def test_settle_info_command(temp_ctx: TempContext) -> None:
    """Test settle info command output."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        with get_connection() as conn:
            # Count transactions with SETTLE_CALCULATED = 1, this should represent
            # the total transactions that were auto-calculated.
            where = f'"{Column.Txn.SETTLE_CALCULATED}" = ?'
            params = [1]
            calculated_count = get_row_count(
                conn,
                Table.TXNS,
                where,
                params,
            )
        cli_result = run_cli_with_config(ctx.config, cli_app, ["settle-info"])
        assert_in_output(
            f"Calculated settlement dates: {calculated_count}",
            cli_result,
        )
        assert_cli_success(cli_result)


def _seed_statement_buy() -> pd.DataFrame:
    """Seed one buy, and return the statement row that settles it."""
    seed_transaction(ticker=STATEMENT_TICKER, amount="-1502.50", units="10")
    return pd.DataFrame(
        [
            {
                "date": "2025-08-18",
                "amount": -1502.50,
                "currency": "USD",
                "transaction": "BUY",
                "description": f"{STATEMENT_TICKER} - 10 SHARES {TXN_DATE}",
            },
        ],
    )


def test_settle_info_import_reads_the_named_file(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        statement_file = ctx.config.project_root / "test_statement.xlsx"
        register_test_dataframe(statement_file, _seed_statement_buy())

        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--import", "-f", str(statement_file)],
        )
        assert_cli_success(cli_result)
        assert_in_output(
            'Updated 1 settlement dates from "test_statement.xlsx"',
            cli_result,
        )


def test_settle_info_import_reads_the_statements_folder(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        statement_file = ctx.config.statements_path / "test_statement.xlsx"
        register_test_dataframe(statement_file, _seed_statement_buy())

        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--import"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Matched 1 out of 1 candidates.", cli_result)


def test_settle_info_import_creates_and_displays_transfer(
    temp_ctx: TempContext,
) -> None:
    """A TRFOUT statement row creates a transfer and shows the import summary."""
    with temp_ctx() as ctx:
        create_txns_table()
        transfer_df = pd.DataFrame(
            [
                {
                    "date": "2025-01-22",
                    "amount": -1000.0,
                    "currency": "CAD",
                    "transaction": "TRFOUTTF",
                    "description": (
                        "Tax-free money transfer out of the account "
                        "(executed at 2025-01-22)"
                    ),
                },
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202501.xlsx"
        register_test_dataframe(statement_file, transfer_df)

        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--import", "-f", str(statement_file)],
        )

        assert_cli_success(cli_result)
        assert_in_output(
            f"SUCCESS: {statement_file.name} - 1 transactions imported",
            cli_result,
        )

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        assert len(txns) == 1
        assert txns.iloc[0][Column.Txn.ACTION] == "TFR_OUT"
        assert txns.iloc[0][Column.Txn.ACCOUNT] == "WS-TFSA"


def test_settle_info_import_reports_skipped_cash_transfers(
    temp_ctx: TempContext,
) -> None:
    """A "Money transfer" row is skipped and reported, not imported as a transfer."""
    with temp_ctx() as ctx:
        create_txns_table()
        transfer_df = pd.DataFrame(
            [
                {
                    "date": "2025-07-11",
                    "amount": 500.0,
                    "currency": "CAD",
                    "transaction": "TRFIN",
                    "description": "Money transfer received (executed at 2025-07-11)",
                },
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202507.xlsx"
        register_test_dataframe(statement_file, transfer_df)

        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--import", "-f", str(statement_file)],
        )

        assert_cli_success(cli_result)
        assert_in_output("Skipped 1 cash transfer(s)", cli_result)

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        assert txns.empty


def test_settle_info_verbose_lists_every_candidate_row(
    temp_ctx: TempContext,
) -> None:
    """`--verbose` tables each statement row, so an unmatched one is visible."""
    with temp_ctx() as ctx:
        create_txns_table()
        seed_transaction(
            action="BUY",
            date=STATEMENT_TRADE_DATE,
            account="WS-TFSA",
            currency="CAD",
            ticker=f"{TSX_TICKER}.TO",
            amount=f"-{STATEMENT_PRICE}",
            price=STATEMENT_PRICE,
            units="1",
        )
        statement_df = pd.DataFrame(
            [
                statement_buy(),
                # Nothing was bought on this day, so it matches no transaction.
                statement_buy(
                    price="47.80",
                    traded="2026-04-21",
                    settled="2026-04-22",
                ),
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202604.xlsx"
        register_test_dataframe(statement_file, statement_df)

        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--import", "--verbose", "-f", str(statement_file)],
        )

        assert_cli_success(cli_result)
        assert_in_output("Settlement Matching", cli_result)
        assert_in_output("matched", cli_result)
        assert_in_output("no match", cli_result)


def test_settle_info_import_archives_every_statement(temp_ctx: TempContext) -> None:
    """Every statement read is archived, and a re-download reads as "Up to date".

    A statement is moved to the archive whether it updated anything or not, so
    the next run never reads it again. Downloading it once more brings it back:
    rows whose transactions are already settled must not look like misses, and
    the new copy is archived beside the old one rather than over it.
    """
    with temp_ctx() as ctx:
        create_txns_table()
        seed_transaction(
            action="BUY",
            date=STATEMENT_TRADE_DATE,
            account="WS-TFSA",
            currency="CAD",
            ticker=f"{TSX_TICKER}.TO",
            amount=f"-{STATEMENT_PRICE}",
            price=STATEMENT_PRICE,
            units="1",
        )
        statements_dir = ctx.config.statements_path

        register_test_dataframe(
            statements_dir / "ws_statement_WS-TFSA_202604.xlsx",
            pd.DataFrame([statement_buy()]),
        )
        register_test_dataframe(
            statements_dir / "ws_statement_WS-RRSP_202604.xlsx",
            pd.DataFrame([statement_buy(price="99.99")]),
        )

        first = run_cli_with_config(ctx.config, cli_app, ["settle-info", "--import"])
        assert_cli_success(first)
        assert_in_output("Success", first)
        assert_in_output("No updates", first)  # the RRSP statement matches nothing

        archive = ctx.config.statements_processed_path
        tfsa = "ws_statement_WS-TFSA_202604.xlsx"
        for name in (tfsa, "ws_statement_WS-RRSP_202604.xlsx"):
            assert (archive / name).exists()
            assert not (statements_dir / name).exists()

        register_test_dataframe(
            statements_dir / tfsa,
            pd.DataFrame([statement_buy()]),
        )
        second = run_cli_with_config(ctx.config, cli_app, ["settle-info", "--import"])
        assert_cli_success(second)
        assert_in_output("Found 1 statement file(s)", second)
        assert_in_output("Up to date", second)
        assert_in_output("are already settled", second)
        assert (archive / "ws_statement_WS-TFSA_202604_1.xlsx").exists()


def test_settle_info_import_refuses_the_demo(temp_ctx: TempContext) -> None:
    """No statement belongs to the demo: the way forward is your own import."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        statement = ctx.config.statements_path / "ws_statement_WS-TFSA_202604.xlsx"
        register_test_dataframe(
            statement,
            pd.DataFrame(
                [
                    statement_buy(),
                ],
            ),
        )

        cli_result = run_cli_with_config(ctx.config, cli_app, ["settle-info", "-i"])

        left_in_place = statement.exists()

    assert cli_result.exit_code == 1
    assert_in_output("is the demo folio", cli_result)
    assert_in_output("folio import", cli_result)
    assert left_in_place


def test_settle_info_verbose_requires_import(temp_ctx: TempContext) -> None:
    """`--verbose` describes an import, so it is refused on its own."""
    with temp_ctx() as ctx:
        create_txns_table()
        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "--verbose"],
        )

        assert cli_result.exit_code == 1
        assert_in_output("only works with", cli_result)


def _seed_ibkr_trade(_monkeypatch: pytest.MonkeyPatch) -> None:
    seed_transaction(account="IBKR-TFSA")


def _prompt_for_a_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_token(_self: object) -> str:
        msg = "No token found"
        raise IBKRAuthenticationError(msg)

    monkeypatch.setattr("services.ibkr_service.IBKRService.get_token", no_token)
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: "token-from-prompt")


def _prompt_for_a_new_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: "token-override")


FLEX_REPORT = {"brokers": {"ibkr": {"FlexReport": "abc123"}}}


@pytest.mark.parametrize(
    ("cli_args", "query_ids", "setup", "expected_output", "writes_csv"),
    [
        pytest.param(
            [],
            {
                "brokers": {
                    "ibkr": {
                        "FlexReport": "abc123",
                        "ActivityStatement": "efg456",
                    },
                },
            },
            None,
            "ActivityStatement: 3 lines received",
            True,
            id="default",
        ),
        pytest.param(
            [],
            None,
            _prompt_for_a_missing_token,
            "Flex token stored securely",
            False,
            id="set_token",
        ),
        pytest.param(
            ["-r", "ref123"],
            FLEX_REPORT,
            None,
            "ref123: Received",
            True,
            id="reference_code",
        ),
        pytest.param(
            [],
            None,
            None,
            "No transactions downloaded",
            False,
            id="no_queries",
        ),
        pytest.param(
            ["--from", "2025-10-01", "--to", "2025-10-21"],
            FLEX_REPORT,
            None,
            "FlexReport: 3 lines received",
            True,
            id="custom_dates",
        ),
        pytest.param(
            [],
            FLEX_REPORT,
            _seed_ibkr_trade,
            f"Using latest IBKR trade date: {TXN_DATE}",
            True,
            id="db_date",
        ),
        pytest.param(
            ["--credentials"],
            None,
            _prompt_for_a_new_token,
            "Setting new IBKR flex token",
            False,
            id="credentials_ibkr",
        ),
    ],
)
def test_ibkr_download_scenarios(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    cli_args: list[str],
    query_ids: dict | None,
    setup: Callable[[pytest.MonkeyPatch], None] | None,
    expected_output: str,
    *,
    writes_csv: bool,
) -> None:
    mock_csv_data = get_default_mock_csv() if writes_csv else ""
    with (
        temp_ctx(query_ids) as ctx,
        IBKRMockContext(monkeypatch, mock_csv_data) as ibkr_mock,
    ):
        if setup is not None:
            setup(monkeypatch)
        cli_result = run_cli_with_config(ctx.config, cli_app, ["download", *cli_args])

        assert_cli_success(cli_result)
        assert_in_output(expected_output, cli_result)
        if writes_csv:
            ibkr_mock.assert_csv_written(mock_csv_data)
        else:
            ibkr_mock.assert_no_csv_written()


@pytest.mark.parametrize(
    ("cli_args", "activities", "expected_csv", "expected_output"),
    [
        pytest.param(
            ["-b", "wealthsimple"],
            get_mock_activities(),
            get_expected_wealthsimple_csv(),
            "Retrieved 6 transactions",
            id="default",
        ),
        pytest.param(
            ["-b", "wealthsimple", "--from", "2025-10-01", "--to", "2025-10-21"],
            get_mock_activities(),
            get_expected_wealthsimple_csv(),
            "Retrieved 6 transactions",
            id="custom_dates",
        ),
        pytest.param(
            ["-b", "wealthsimple"],
            [],
            None,
            "No transactions downloaded",
            id="no_transactions",
        ),
        pytest.param(
            ["-b", "wealthsimple", "--statement", "-f", "2024-01-01"],
            get_mock_statement_transactions(),
            get_expected_statement_txn_csv(),
            "Retrieved 2 statement transactions",
            id="statement",
        ),
    ],
)
def test_wealthsimple_download_scenarios(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    cli_args: list[str],
    activities: list[dict[str, Any]],
    expected_csv: str | None,
    expected_output: str,
) -> None:
    with (
        temp_ctx() as ctx,
        WealthsimpleMockContext(monkeypatch, mock_activities=activities) as ws_mock,
    ):
        cli_result = run_cli_with_config(ctx.config, cli_app, ["download", *cli_args])

        assert_cli_success(cli_result)
        assert_in_output(expected_output, cli_result)
        if expected_csv is None:
            ws_mock.assert_no_csv_written()
        else:
            ws_mock.assert_csv_written(expected_csv)


def _download_wealthsimple_csv(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    accounts: list[dict[str, Any]],
    activities: list[dict[str, Any]],
) -> str:
    """Run a Wealthsimple download over the mock API and return the CSV it wrote."""
    mock_activities = create_mock_activities(activities)
    with (
        temp_ctx() as ctx,
        WealthsimpleMockContext(
            monkeypatch,
            mock_accounts=accounts,
            mock_activities=mock_activities,
        ) as ws_mock,
    ):
        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["download", "-b", "wealthsimple"],
        )
        assert_cli_success(cli_result)
        (content,) = ws_mock.written_csvs.values()
    return content


def test_wealthsimple_download_names_each_account_by_its_nickname(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any account type is recorded under its nickname, or its id without one."""
    accounts = [
        mock_account("test-tfsa-1", "WS-TFSA"),
        mock_account("test-rrsp-1", "WS-RRSP", "ca_rrsp"),
        mock_account("test-fhsa-1", None, "ca_fhsa"),
    ]
    contribution = {"type": "INTERNAL_TRANSFER", "sub_type": "DESTINATION"}
    content = _download_wealthsimple_csv(
        temp_ctx,
        monkeypatch,
        accounts,
        [
            {"account_id": "test-tfsa-1", **contribution},
            {"account_id": "test-rrsp-1", **contribution},
            {"account_id": "test-fhsa-1", **contribution},
        ],
    )

    recorded = [row.split(",")[-1] for row in content.splitlines()[1:]]
    assert recorded == ["WS-TFSA", "WS-RRSP", "test-fhsa-1"]


def test_wealthsimple_download_passes_an_amount_and_units_it_cannot_read_through(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An odd amount or units does not stop the download: the row is written as sent."""
    # The preview reads amounts as numbers, which is not what is under test.
    monkeypatch.setattr(
        "cli.commands.download.show_activities_preview",
        lambda *_args: None,
    )
    content = _download_wealthsimple_csv(
        temp_ctx,
        monkeypatch,
        [mock_account("test-tfsa-1", "WS-TFSA")],
        [
            {
                "account_id": "test-tfsa-1",
                "type": "DIY_BUY",
                "amount": "n/a",
                "asset_quantity": "two",
                "asset_symbol": "SPY",
            },
        ],
    )

    row = content.splitlines()[1]
    assert row == "2025-10-02,BUY,n/a,USD,,two,SPY,WS-TFSA"


def test_wealthsimple_download_refreshes_an_expired_access_token(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored session whose access token expired is renewed, not abandoned."""
    expired = ws_api.WSApiException(
        "Not Authorized",
        response={"errors": [{"extensions": {"code": "UNAUTHENTICATED"}}]},
    )
    with (
        temp_ctx() as ctx,
        WealthsimpleMockContext(
            monkeypatch,
            mock_activities=get_mock_activities(),
            token_error=expired,
        ) as ws_mock,
    ):
        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["download", "-b", "wealthsimple"],
        )

        assert_cli_success(cli_result)
        assert_in_output("Retrieved 6 transactions", cli_result)
        # Refused with the stored token, then accepted once it was cleared.
        assert ws_mock.token_attempts == ["stored-token", None]


def test_wealthsimple_statement_not_published_yet_is_not_an_error(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Early in a month the statement is not out: each account is skipped."""
    unpublished = ws_api.WSApiException(
        "Statement not found",
        response={"data": {"monthlyStatement": None}},
    )
    with (
        temp_ctx() as ctx,
        WealthsimpleMockContext(monkeypatch, statement_error=unpublished) as ws_mock,
    ):
        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["download", "-b", "wealthsimple", "--statement", "-f", "2024-01-01"],
        )

        assert_cli_success(cli_result)
        assert_in_output(
            "No statement transactions found for account WS-TFSA",
            cli_result,
        )
        assert_in_output("No statement transactions downloaded", cli_result)
        ws_mock.assert_no_csv_written()


def test_ibkr_download_waits_for_a_statement_that_is_not_ready(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A statement still being generated is asked for again until it arrives."""
    mock_csv_data = get_default_mock_csv()
    with (
        temp_ctx({"brokers": {"ibkr": {"FlexReport": "abc123"}}}) as ctx,
        IBKRMockContext(monkeypatch, mock_csv_data, not_ready_polls=2) as ibkr_mock,
    ):
        cli_result = run_cli_with_config(ctx.config, cli_app, ["download"])

        assert_cli_success(cli_result)
        assert_in_output("FlexReport: 3 lines received", cli_result)
        ibkr_mock.assert_csv_written(mock_csv_data)
        assert ibkr_mock.statement_requests == 3


def test_version_command(tmp_path: Path, temp_ctx: TempContext) -> None:
    """The paths reported are the folio's own, here the temp one."""
    with temp_ctx() as ctx:
        cli_result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["version"],
            width=UNCONSTRAINED_WIDTH,
        )
    assert_cli_success(cli_result)
    assert_in_output("folio-updater version:", cli_result)
    assert_in_output("application path:", cli_result)
    assert_in_output(f"config path: {tmp_path / 'config.yaml'}", cli_result)
    assert_in_output(f"folio path: {ctx.config.folio_path}", cli_result)
    assert_in_output(f"data path: {ctx.config.data_path}", cli_result)
    assert_in_output(f"backup path: {ctx.config.backup_path}", cli_result)
    for path in (ctx.config.folio_path, ctx.config.data_path, ctx.config.backup_path):
        assert path.is_relative_to(tmp_path)


@pytest.mark.parametrize(
    "command",
    ["import", "add", "download", "symbol", "acb", "dash", "quotes", "check", "edit"],
)
def test_short_help_flag(command: str) -> None:
    """Every command answers `-h` the same as `--help`."""
    short = runner.invoke(cli_app, [command, "-h"])
    long = runner.invoke(cli_app, [command, "--help"])
    assert short.exit_code == 0
    assert short.stdout == long.stdout
    assert "Usage" in short.stdout


@pytest.mark.parametrize("command", ["acb", "symbol"])
def test_bare_command_shows_help(command: str) -> None:
    """Commands that cannot do anything without arguments print their help."""
    result = runner.invoke(cli_app, [command])
    assert "Usage" in result.stdout
