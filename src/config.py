"""Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml


class Config:
    """Configuration for the folio."""

    DEFAULT_CONFIG: Mapping[str, Any] = MappingProxyType(
        {
            "folio_path": "data/folio.xlsx",
            "log_level": "ERROR",
            "sheets": {
                "tickers": "Tickers",
                "txns": "Txns",
            },
            "header_keywords": {
                "TxnDate": ["txndate", "transaction date", "date"],
                "Action": ["action", "type", "activity"],
                "Amount": ["amount", "value", "total"],
                "$": ["$", "currency", "curr"],
                "Price": ["price", "unit price", "share price"],
                "Units": ["units", "shares", "qty", "quantity"],
                "Ticker": ["ticker", "symbol", "stock"],
            },
        },
    )

    def __init__(
        self,
        settings: Mapping[str, Any] = DEFAULT_CONFIG,
        project_root: Path | None = None,
    ) -> None:
        """Initialize the Config object."""
        self._settings = settings
        self._config_path = Config._get_config_path(project_root)

        if project_root is None:
            project_root = Config._get_project_root()
        folio_path = Path(settings["folio_path"])
        if not folio_path.is_absolute():
            folio_path: Path = (project_root / settings["folio_path"]).resolve()
        self._folio_path: Path = folio_path

    @property
    def config_path(self) -> Path:
        """Get the path to the config.yaml file."""
        return self._config_path

    @property
    def folio_path(self) -> Path:
        """Get the folio path."""
        return self._folio_path

    @property
    def log_level(self) -> str:
        """Get the log level."""
        return self._settings["log_level"]

    @property
    def sheets(self) -> dict[str, str]:
        """Get the sheet mappings."""
        return self._settings["sheets"]

    @property
    def header_keywords(self) -> dict[str, list[str]]:
        """Get the header keywords mappings."""
        return self._settings["header_keywords"]

    def tickers_sheet(self) -> str:
        """Get the tickers sheet name."""
        return self._settings["sheets"]["tickers"]

    def transactions_sheet(self) -> str:
        """Get the transactions sheet name."""
        return self._settings["sheets"]["txns"]

    @classmethod
    def load(cls, project_root: Path | None = None) -> Config:
        """Load config.yaml from disk, creating it if it doesn't exist.

        Args:
            project_root: Optional Path to the project root. If None, uses the location
                of this file's parent directory.

        Returns:
            Config: The loaded configuration
        """
        config_yaml: Path = cls._get_config_path(project_root)
        configuration: dict[str, Any] = dict(cls.DEFAULT_CONFIG)
        if not config_yaml.exists():
            with Path.open(config_yaml, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    configuration,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                )
        else:
            with Path.open(config_yaml, "r", encoding="utf-8") as f:
                configuration = yaml.safe_load(f) or {}

        configuration = cls._validate_config(configuration)
        return cls(configuration, project_root)

    @staticmethod
    def _get_project_root() -> Path:
        return Path(__file__).resolve().parent.parent

    @staticmethod
    def _get_config_path(project_root: Path | None = None) -> Path:
        if project_root is None:
            project_root = Config._get_project_root()
        return project_root / "config.yaml"

    @staticmethod
    def _validate_config(settings: dict[str, Any]) -> dict[str, Any]:
        """Validate the loaded configuration against expected structure and values.

        Args:
            settings: Raw configuration dictionary to validate

        Returns:
            Validated configuration
        """
        validated: dict[str, Any] = dict(Config.DEFAULT_CONFIG)

        log_level = settings.get("log_level", validated["log_level"]).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level: str = validated["log_level"]
        validated["log_level"] = log_level

        if "folio_path" in settings:
            validated["folio_path"] = str(settings["folio_path"])

        if "sheets" in settings and isinstance(settings["sheets"], dict):
            validated["sheets"].update(settings["sheets"])

        if "header_keywords" in settings and isinstance(
            settings["header_keywords"],
            dict,
        ):
            validated["header_keywords"].update(
                {
                    k: v
                    for k, v in settings["header_keywords"].items()
                    if k in validated["header_keywords"]
                },
            )

        return validated

    def __str__(self) -> str:
        """Return a string representation of the Config object."""
        config_str = " Config Details:\n"
        config_str += f"  Config Path: {self.config_path}\n"
        config_str += f"  Folio Path: {self.folio_path}\n"
        config_str += f"  Log Level: {self.log_level}\n"
        config_str += f"  Sheets: {self.sheets}\n"
        config_str += f"  Header Keywords: {self.header_keywords}\n"
        return config_str
