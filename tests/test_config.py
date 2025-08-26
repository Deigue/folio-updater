"""Tests for the config module."""

import logging
from contextlib import _GeneratorContextManager
from pathlib import Path
from typing import Callable

import yaml
from src import bootstrap
from src.config import Config

logger = logging.getLogger(__name__)


def test_default_config(tmp_path: Path) -> None:
    expected_config = Path(tmp_path) / "config.yaml"
    assert not expected_config.exists()
    config: Config = Config.load(tmp_path)
    assert expected_config.exists()
    logger.info(config)

    with Path.open(expected_config) as f:
        config_yaml = yaml.safe_load(f)
    assert config_yaml == Config.DEFAULT_CONFIG
    expected_config.unlink()


def test_relative_path_resolves(
    temp_config: Callable[..., _GeneratorContextManager[Config, None, None]],
) -> None:
    with temp_config({"folio_path": "data/testfolio.xlsx"}) as config:
        assert config.folio_path.is_absolute()
        assert "testfolio.xlsx" in str(config.folio_path)


def test_absolute_path_kept(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[Config, None, None]],
) -> None:
    absolute_path: Path = tmp_path / "absolute.xlsx"
    with temp_config({"folio_path": str(absolute_path)}) as config:
        assert config.folio_path == absolute_path


def test_bootstrap(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[Config, None, None]],
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
    ) as config:
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
                    "log_level": "DEBUG",
                    "sheets": {"tickers": "TKR", "txns": "TXNS"},
                    "header_keywords": {"TxnDate": ["settledate"]},
                },
                f,
            )
        new_config: Config = bootstrap.reload_config(tmp_path)
        assert new_config.folio_path == good_folio
        assert new_config.log_level == "DEBUG"
        assert new_config.tickers_sheet() == "TKR"
        assert new_config.transactions_sheet() == "TXNS"
        assert new_config.header_keywords["TxnDate"] == ["settledate"]

        # --- 3. Ensure logging is configured ---
        logger.debug("This message is colorized!")
