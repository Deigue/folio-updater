"""Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

import yaml


class Config:
    """Configuration for the folio."""

    DEFAULT_CONFIG: ClassVar[MappingProxyType[str, Any]] = MappingProxyType(
        {
            "folio_path": "data/folio.xlsx",
            "db_path": "data/folio.db",
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
                "Account": ["account", "alias", "account id"],
            },
            "header_ignore": [],
            "duplicate_approval": {
                "column_name": "Duplicate",
                "approval_value": "OK",
            },
        },
    )

    def __init__(
        self,
        project_root: Path,
        settings: dict[str, Any],
    ) -> None:
        """Initialize the Config object."""
        self._settings = settings
        self._project_root = project_root
        self._config_path = Config._get_config_path(project_root)
        folio_path = Path(settings["folio_path"])
        if not folio_path.is_absolute():
            folio_path: Path = (project_root / settings["folio_path"]).resolve()
        self._folio_path: Path = folio_path
        db_path = Path(settings["db_path"])
        if not db_path.is_absolute():  # pragma: no branch
            db_path: Path = (project_root / settings["db_path"]).resolve()
        self._db_path: Path = db_path

    @property
    def config_path(self) -> Path:
        """Get the path to the config.yaml file."""
        return self._config_path

    @property
    def folio_path(self) -> Path:
        """Get the folio path."""
        return self._folio_path

    @property
    def db_path(self) -> Path:
        """Get the database path."""
        return self._db_path

    @property
    def project_root(self) -> Path:
        """Get the project root directory."""
        return self._project_root

    @property
    def log_level(self) -> str:
        """Get the log level."""
        return self._settings["log_level"]

    @property
    def sheets(self) -> dict[str, str]:  # pragma: no cover
        """Get the sheet mappings."""
        return self._settings["sheets"]

    @property
    def header_keywords(self) -> dict[str, list[str]]:
        """Get the header keywords mappings."""
        return self._settings["header_keywords"]

    @property
    def header_ignore(self) -> list[str]:
        """Get the list of column names to ignore during import."""
        return self._settings.get("header_ignore", [])

    @property
    def duplicate_approval_column(self) -> str:
        """Get the name of the column used to approve duplicate transactions."""
        duplicate_config = self._settings.get("duplicate_approval", {})
        return duplicate_config.get("column_name", "Duplicate")

    @property
    def duplicate_approval_value(self) -> str:
        """Get the value that indicates duplicate transaction approval."""
        duplicate_config = self._settings.get("duplicate_approval", {})
        return duplicate_config.get("approval_value", "OK")

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
        if project_root is not None:
            resolved_root = project_root
        else:
            resolved_root = Config.get_default_root_directory()  # pragma: no cover

        config_yaml: Path = cls._get_config_path(resolved_root)
        configuration: dict[str, Any] = deepcopy(dict(cls.DEFAULT_CONFIG))
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
        return cls(resolved_root, configuration)

    @staticmethod
    def get_default_root_directory() -> Path:
        """Get the project root directory by searching upwards for markers."""
        current = Path(__file__).resolve().parent
        while current != current.parent:
            # Check for common project markers (e.g., .git for Git repos)
            if (current / ".git").exists() or (current / "config.yaml").exists():
                return current
            current = current.parent
        # Fallback: If no marker found, use relative path.
        return Path(__file__).resolve().parent.parent.parent  # pragma: no cover

    @staticmethod
    def _get_config_path(project_root: Path) -> Path:
        return project_root / "config.yaml"

    @staticmethod
    def _validate_config(settings: dict[str, Any]) -> dict[str, Any]:
        """Validate the loaded configuration against expected structure and values.

        Args:
            settings: Raw configuration dictionary to validate

        Returns:
            Validated configuration
        """
        validated: dict[str, Any] = deepcopy(dict(Config.DEFAULT_CONFIG))

        log_level = settings.get("log_level", validated["log_level"]).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level: str = validated["log_level"]
        validated["log_level"] = log_level

        if "folio_path" in settings:  # pragma: no branch
            validated["folio_path"] = str(settings["folio_path"])

        if "db_path" in settings:  # pragma: no branch
            validated["db_path"] = str(settings["db_path"])

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

        if "header_ignore" in settings and isinstance(settings["header_ignore"], list):
            validated["header_ignore"] = settings["header_ignore"]

        if "duplicate_approval" in settings and isinstance(
            settings["duplicate_approval"],
            dict,
        ):
            duplicate_approval = settings["duplicate_approval"]
            validated_duplicate_approval = validated["duplicate_approval"].copy()

            if "column_name" in duplicate_approval:  # pragma: no branch
                validated_duplicate_approval["column_name"] = str(
                    duplicate_approval["column_name"],
                )

            if "approval_value" in duplicate_approval:  # pragma: no branch
                validated_duplicate_approval["approval_value"] = str(
                    duplicate_approval["approval_value"],
                )

            validated["duplicate_approval"] = validated_duplicate_approval

        return validated

    def __str__(self) -> str:
        """Return a string representation of the Config object."""
        config_str = " Config Details:\n"
        config_str += f"  Config Path: {self.config_path}\n"
        config_str += f"  Folio Path: {self.folio_path}\n"
        config_str += f"  Database Path: {self.db_path}\n"
        config_str += f"  Log Level: {self.log_level}\n"
        config_str += f"  Sheets: {self.sheets}\n"
        config_str += f"  Header Keywords: {self.header_keywords}\n"
        config_str += f"  Header Ignore: {self.header_ignore}\n"
        config_str += f"  Duplicate Approval Column: {self.duplicate_approval_column}\n"
        config_str += f"  Duplicate Approval Value: {self.duplicate_approval_value}\n"
        return config_str
