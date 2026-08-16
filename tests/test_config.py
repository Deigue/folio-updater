"""Tests for the config module."""

import logging
from pathlib import Path

import yaml

from app import bootstrap
from utils.config import Config
from utils.constants import FeeConvention

from .test_types import TempContext

logger = logging.getLogger(__name__)


def test_default_config(tmp_path: Path, temp_ctx: TempContext) -> None:
    # No yaml file exists, verify auto-creation logic
    config = Config.load(tmp_path)
    logger.debug("Auto-created config.yaml:\n%s", config)
    with Path.open(config.config_path) as f:
        config_yaml = yaml.safe_load(f)
        assert config_yaml == Config.DEFAULT_CONFIG
    config.config_path.unlink()

    # Load an empty config.yaml
    with temp_ctx() as ctx:
        assert ctx.config.config_path.exists()
        logger.debug("Empty Configuration:\n%s", ctx.config)

    # Load a default config.yaml
    with temp_ctx(Config.DEFAULT_CONFIG) as ctx:
        assert ctx.config.config_path.exists()
        logger.debug("Default Configuration:\n%s", ctx.config)
        with Path.open(ctx.config.config_path) as f:
            config_yaml = yaml.safe_load(f)
            assert config_yaml == Config.DEFAULT_CONFIG


def test_backup_settings_are_read_from_config(temp_ctx: TempContext) -> None:
    # An explicit backup section is honoured as given.
    with temp_ctx({"backup": {"enabled": True, "max_backups": 3}}) as ctx:
        assert ctx.config.backup_enabled
        assert ctx.config.max_backups == 3

    # Default: conftest disables backups for all, max_backups=50
    with temp_ctx() as ctx:
        assert not ctx.config.backup_enabled
        assert ctx.config.max_backups == 50


def test_relative_path_resolves(temp_ctx: TempContext) -> None:
    with temp_ctx({"folio_path": "data/testfolio.xlsx"}) as ctx:
        config = ctx.config
        assert config.folio_path.is_absolute()
        assert "testfolio.xlsx" in str(config.folio_path)


def test_absolute_path_kept(tmp_path: Path, temp_ctx: TempContext) -> None:
    absolute_path: Path = tmp_path / "absolute.xlsx"
    with temp_ctx({"folio_path": str(absolute_path)}) as ctx:
        assert ctx.config.folio_path == absolute_path


def test_bootstrap(tmp_path: Path, temp_ctx: TempContext) -> None:
    # --- 1. Test problematic bootstrap  ---
    # Point folio_path to a folder that doesn't exist.
    bad_folio: Path = tmp_path / "nonexistent" / "bad.xlsx"
    with temp_ctx(
        {
            "folio_path": str(bad_folio),
            "log_level": 3,
            "sheets": {"tickers": "Tickers", "txns": None, "unknown_sheet": "Nope"},
            "header_ignore": "notalist",
            "brokers": "notadict",
            "backup": {"max_backups": True},
            "duplicate_approval": {"column_name": None, "approval_value": 1},
            "header_keywords": {
                "TxnDate": ["txndate", "transaction date", "date"],
                "Action": ["action", "type", "activity"],
                "Amount": ["amount", "value", "total"],
                "$": ["$", "currency", "curr"],
                "Price": ["price", "unit price", "share price"],
                "Units": ["units", "shares", "qty", "quantity"],
                "Ticker": ["ticker", "symbol", "stock"],
                "InvalidKeyword": ["invalid"],
            },
        },
    ) as ctx:
        config = ctx.config
        assert config.log_level == "ERROR"  # Defaults to ERROR on bad value
        assert not config.header_keywords.__contains__("InvalidKeyword")
        assert config.header_ignore == []
        assert config.brokers == Config.DEFAULT_CONFIG["brokers"]
        assert config.txn_sheet == "Txns"
        assert "unknown_sheet" not in config.sheets
        assert config.max_backups == 50  # A bool is not a backup count.
        assert config.duplicate_approval_column == "Duplicate"
        assert config.duplicate_approval_value == "1"
        good_folio: Path = tmp_path / "good.xlsx"
        # --- 2. Test reload_config updates config ---
        config_yaml: Path = config.config_path
        assert config_yaml.exists()
        with Path.open(config_yaml, mode="w") as f:
            yaml.safe_dump(
                {
                    "folio_path": str(good_folio),
                    "log_level": "INFO",
                    "sheets": {"tickers": "TKR", "txns": "TXNS"},
                    "header_keywords": {"TxnDate": ["settledate"]},
                },
                f,
            )

        root_logger: logging.Logger = logging.getLogger()
        original_level: int = root_logger.level
        original_handlers = list(root_logger.handlers)
        try:
            new_config: Config = bootstrap.reload_config(tmp_path)
            assert new_config.folio_path == good_folio
            assert new_config.log_level == "INFO"
            assert new_config.tkr_sheet == "TKR"
            assert new_config.txn_sheet == "TXNS"
            assert new_config.header_keywords["TxnDate"] == ["settledate"]
            logger.debug("This message is colorized!")
        finally:
            root_logger.setLevel(original_level)
            for handler in root_logger.handlers:
                if handler not in original_handlers:  # pragma: no cover
                    root_logger.removeHandler(handler)
            for handler in original_handlers:
                if handler not in root_logger.handlers:
                    root_logger.addHandler(handler)  # pragma: no cover


def test_cost_base_defaults(temp_ctx: TempContext) -> None:
    """The naming convention handles the common case, so nothing is required."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert config.account_map == {}
        assert config.account_naming_convention is True
        assert config.account_fee_default == "auto"
        assert config.auto_getfx is True
        assert config.display_currency == "CAD"
        assert config.acb_parquet.name == "acb.parquet"


def test_accounts_map_accepts_both_forms(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "WS-PERSONAL": "NON_REGISTERED",
                "QT-TFSA": {"type": "TFSA", "amount_includes_fees": True},
            },
        },
    }
    with temp_ctx(overrides) as ctx:
        assert ctx.config.account_types == {
            "WS-PERSONAL": "NON_REGISTERED",
            "QT-TFSA": "TFSA",
        }
        assert ctx.config.fee_convention_for("QT-TFSA") is FeeConvention.INCLUDED
        assert ctx.config.fee_convention_for("WS-PERSONAL") is FeeConvention.AUTO


def test_unparseable_account_entries_are_dropped(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "GOOD-TFSA": "TFSA",
                "BAD": "NOT_A_TYPE",
                "ODD-RRSP": {"type": "RRSP", "amount_includes_fees": "maybe"},
            },
        },
    }
    with temp_ctx(overrides) as ctx:
        assert "BAD" not in ctx.config.account_map
        assert ctx.config.account_types["GOOD-TFSA"] == "TFSA"
        # The bad fee value is dropped, but the usable type survives.
        assert ctx.config.account_types["ODD-RRSP"] == "RRSP"
        assert ctx.config.fee_convention_for("ODD-RRSP") is FeeConvention.AUTO


def test_cost_basis_and_display_blocks(temp_ctx: TempContext) -> None:
    overrides = {
        "cost_basis": {"auto_getfx": False},
        "display": {"currency": "usd"},
    }
    with temp_ctx(overrides) as ctx:
        assert ctx.config.auto_getfx is False
        assert ctx.config.display_currency == "USD"


def test_invalid_display_currency_falls_back(temp_ctx: TempContext) -> None:
    with temp_ctx({"display": {"currency": "GBP"}}) as ctx:
        assert ctx.config.display_currency == "CAD"


def test_fee_default_of_false_is_honoured(temp_ctx: TempContext) -> None:
    """`false` is a real convention here, not an absent value."""
    with temp_ctx({"accounts": {"defaults": {"amount_includes_fees": False}}}) as ctx:
        assert ctx.config.account_fee_default == "false"
        assert ctx.config.fee_convention_for("ANY-TFSA") is FeeConvention.EXCLUDED

    # An unrecognised spelling leaves the default in place.
    with temp_ctx({"accounts": {"defaults": {"amount_includes_fees": "maybe"}}}) as ctx:
        assert ctx.config.account_fee_default == "auto"
