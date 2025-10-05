"""Tests for the config module."""

import logging
from contextlib import _GeneratorContextManager
from pathlib import Path
from typing import Callable

import yaml

from app import bootstrap
from app.app_context import AppContext
from utils.config import Config

logger = logging.getLogger(__name__)


def test_default_config(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    # No yaml file exists, verify auto-creation logic
    config = Config.load(tmp_path)
    logger.debug("Auto-created config.yaml:\n%s", config)
    with Path.open(config.config_path) as f:
        config_yaml = yaml.safe_load(f)
        assert config_yaml == Config.DEFAULT_CONFIG
    config.config_path.unlink()

    # Load an empty config.yaml
    with temp_config() as ctx:
        assert ctx.config.config_path.exists()
        logger.debug("Empty Configuration:\n%s", ctx.config)

    # Load a default config.yaml
    with temp_config(Config.DEFAULT_CONFIG) as ctx:
        assert ctx.config.config_path.exists()
        logger.info("Default Configuration:\n%s", ctx.config)
        with Path.open(ctx.config.config_path) as f:
            config_yaml = yaml.safe_load(f)
            assert config_yaml == Config.DEFAULT_CONFIG


def test_relative_path_resolves(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config({"folio_path": "data/testfolio.xlsx"}) as ctx:
        config = ctx.config
        assert config.folio_path.is_absolute()
        assert "testfolio.xlsx" in str(config.folio_path)


def test_absolute_path_kept(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    absolute_path: Path = tmp_path / "absolute.xlsx"
    with temp_config({"folio_path": str(absolute_path)}) as ctx:
        assert ctx.config.folio_path == absolute_path


def test_bootstrap(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    # --- 1. Test problematic bootstrap  ---
    # Point folio_path to a folder that doesn't exist.
    bad_folio: Path = tmp_path / "nonexistent" / "bad.xlsx"
    with temp_config(
        {
            "folio_path": str(bad_folio),
            "log_level": "INVALID",
            "sheets": {"tickers": "Tickers"},
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
            assert new_config.tickers_sheet() == "TKR"
            assert new_config.transactions_sheet() == "TXNS"
            assert new_config.header_keywords["TxnDate"] == ["settledate"]
            logger.debug("This message is colorized!")
        finally:
            root_logger.setLevel(original_level)
            for handler in root_logger.handlers:
                if handler not in original_handlers:
                    root_logger.removeHandler(handler)
            for handler in original_handlers:
                if handler not in root_logger.handlers:
                    root_logger.addHandler(handler)  # pragma: no cover
