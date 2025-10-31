"""Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

import yaml

from utils.constants import Column
from utils.optional_fields import OptionalFieldsConfig
from utils.transforms import TransformsConfig


class Config:
    """Configuration for the folio."""

    DEFAULT_CONFIG: ClassVar[MappingProxyType[str, Any]] = MappingProxyType(
        {
            "folio_path": "data/folio.xlsx",
            "data_path": "data",
            "log_level": "ERROR",
            "sheets": {
                "tickers": "Tickers",
                "txns": "Txns",
                "fx": "FX",
            },
            "header_keywords": {
                str(Column.Txn.TXN_DATE): [
                    "txndate",
                    "transaction date",
                    "date",
                    "tradedate",
                    "reportdate",
                ],
                str(Column.Txn.ACTION): ["action", "type", "activity"],
                str(Column.Txn.AMOUNT): ["amount", "value", "total"],
                str(Column.Txn.CURRENCY): ["$", "currency", "curr"],
                str(Column.Txn.PRICE): ["price", "unit price", "share price"],
                str(Column.Txn.UNITS): ["units", "shares", "qty", "quantity"],
                str(Column.Txn.TICKER): ["ticker", "symbol", "stock"],
                str(Column.Txn.ACCOUNT): ["account", "alias", "account id"],
                str(Column.Txn.SETTLE_DATE): ["settledate", "settlement date"],
            },
            "header_ignore": [],
            "duplicate_approval": {
                "column_name": "Duplicate",
                "approval_value": "OK",
            },
            "backup": {
                "enabled": True,
                "path": "backups",
                "max_backups": 50,
            },
            "brokers": {
                "ibkr": {
                    "FlexReport": "111111",
                    "CashActivity": "999999",
                },
                "wealthsimple": {
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) "
                        "Gecko/20100101 Firefox/143.0"
                    ),
                },
            },
            "optional_columns": {},
            "transforms": {"rules": []},
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
        data_path = Path(settings["data_path"])
        if not data_path.is_absolute():
            data_path: Path = (project_root / settings["data_path"]).resolve()
            if not data_path.exists():
                data_path.mkdir(parents=True, exist_ok=True)
        self._data_path: Path = data_path
        backup_path = Path(settings["backup"]["path"])
        if not backup_path.is_absolute():
            backup_path: Path = (project_root / settings["backup"]["path"]).resolve()
        self._backup_path: Path = backup_path
        self._imports_path: Path = data_path / "imports"
        self._processed_path: Path = data_path / "processed"
        optional_columns = settings.get("optional_columns", {})
        self._optional_fields = OptionalFieldsConfig(optional_columns)
        transforms_config = settings.get("transforms", {})
        self._transforms = TransformsConfig(transforms_config)

    @property
    def config_path(self) -> Path:
        """Get the path to the config.yaml file."""
        return self._config_path

    @property
    def folio_path(self) -> Path:
        """Get the folio path."""
        return self._folio_path

    @property
    def data_path(self) -> Path:
        """Get the data directory path."""
        return self._data_path

    @property
    def db_path(self) -> Path:
        """Get the database path."""
        return self._data_path / "folio.db"

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

    @property
    def optional_fields(self) -> OptionalFieldsConfig:
        """Get the optional fields configuration."""
        return self._optional_fields

    @property
    def transforms(self) -> TransformsConfig:
        """Get the transforms configuration."""
        return self._transforms

    @property
    def backup_enabled(self) -> bool:
        """Whether backups are enabled."""
        backup_config = self._settings.get("backup", {})
        return backup_config.get("enabled", True)

    @property
    def backup_path(self) -> Path:
        """The directory where backups are stored."""
        return self._backup_path

    @property
    def imports_path(self) -> Path:
        """The directory where files to be imported are staged.

        This directory holds downloaded files that are ready for processing
        and import into the folio database. The directory is created
        lazily when first accessed.
        """
        if not self._imports_path.exists():
            self._imports_path.mkdir(parents=True, exist_ok=True)
        return self._imports_path

    @property
    def processed_path(self) -> Path:
        """The directory where processed files are moved.

        This directory holds files that have already been imported
        into the folio database. The directory is created
        lazily when first accessed.
        """
        if not self._processed_path.exists():
            self._processed_path.mkdir(parents=True, exist_ok=True)
        return self._processed_path

    @property
    def max_backups(self) -> int:
        """Maximum number of backups to keep."""
        backup_config = self._settings.get("backup", {})
        return backup_config.get("max_backups", 50)

    @property
    def brokers(self) -> dict[str, dict[str, str]]:
        """Get broker configuration."""
        return self._settings.get("brokers", {})

    @property
    def tkr_sheet(self) -> str:
        """Get the tickers sheet name.

        Renamed from tickers_sheet().
        """
        return self._settings["sheets"]["tickers"]

    @property
    def txn_sheet(self) -> str:
        """Get the transactions sheet name.

        Renamed from transactions_sheet().
        """
        return self._settings["sheets"]["txns"]

    @property
    def fx_sheet(self) -> str:
        """Get the forex sheet name.

        Renamed from forex_sheet().
        """
        return self._settings["sheets"]["fx"]

    @property
    def txn_parquet(self) -> Path:
        """Path to transactions.parquet in data path."""
        return self._data_path / "transactions.parquet"

    @property
    def fx_parquet(self) -> Path:
        """Path to forex.parquet in data path."""
        return self._data_path / "forex.parquet"

    @property
    def tkr_parquet(self) -> Path:
        """Path to tickers.parquet in data path."""
        return self._data_path / "tickers.parquet"

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
        if getattr(sys, "frozen", False):  # pragma: no cover
            # Running as PyInstaller executable - files in executable directory
            return Path(sys.executable).parent
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

        Config._validate_log_level(settings, validated)
        Config._validate_folio_path(settings, validated)
        Config._validate_data_path(settings, validated)
        Config._validate_sheets(settings, validated)
        Config._validate_header_keywords(settings, validated)
        Config._validate_header_ignore(settings, validated)
        Config._validate_duplicate_approval(settings, validated)
        Config._validate_backup(settings, validated)
        Config._validate_brokers(settings, validated)
        Config._validate_optional_columns(settings, validated)
        Config._validate_transforms(settings, validated)

        return validated

    @staticmethod
    def _validate_log_level(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        log_level = settings.get("log_level", validated["log_level"]).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = validated["log_level"]
        validated["log_level"] = log_level

    @staticmethod
    def _validate_folio_path(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "folio_path" in settings:
            validated["folio_path"] = str(settings["folio_path"])

    @staticmethod
    def _validate_data_path(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "data_path" in settings:
            validated["data_path"] = str(settings["data_path"])

    @staticmethod
    def _validate_sheets(settings: dict[str, Any], validated: dict[str, Any]) -> None:
        if "sheets" in settings and isinstance(settings["sheets"], dict):
            validated["sheets"].update(settings["sheets"])

    @staticmethod
    def _validate_header_keywords(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "header_keywords" in settings and isinstance(
            settings["header_keywords"],
            dict,
        ):
            validated["header_keywords"].update(
                {
                    k: v
                    for k, v in settings["header_keywords"].items()
                    # Add internal fields that are not in the default mapping here.
                    if k in validated["header_keywords"] or k in {str(Column.Txn.FEE)}
                },
            )

    @staticmethod
    def _validate_header_ignore(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "header_ignore" in settings and isinstance(settings["header_ignore"], list):
            validated["header_ignore"] = settings["header_ignore"]

    @staticmethod
    def _validate_duplicate_approval(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "duplicate_approval" in settings and isinstance(
            settings["duplicate_approval"],
            dict,
        ):
            duplicate_approval = settings["duplicate_approval"]
            validated_duplicate_approval = validated["duplicate_approval"].copy()

            if "column_name" in duplicate_approval:
                validated_duplicate_approval["column_name"] = str(
                    duplicate_approval["column_name"],
                )

            if "approval_value" in duplicate_approval:
                validated_duplicate_approval["approval_value"] = str(
                    duplicate_approval["approval_value"],
                )

            validated["duplicate_approval"] = validated_duplicate_approval

    @staticmethod
    def _validate_backup(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "backup" in settings and isinstance(settings["backup"], dict):
            backup_config = settings["backup"]
            validated_backup = validated["backup"].copy()

            if "enabled" in backup_config:
                enabled = backup_config["enabled"]
                if isinstance(enabled, bool):
                    validated_backup["enabled"] = enabled

            if "path" in backup_config:
                validated_backup["path"] = str(backup_config["path"])

            if "max_backups" in backup_config:
                max_backups = backup_config["max_backups"]
                if isinstance(max_backups, int) and max_backups > 0:
                    validated_backup["max_backups"] = max_backups

            validated["backup"] = validated_backup

    @staticmethod
    def _validate_brokers(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "brokers" in settings and isinstance(settings["brokers"], dict):
            validated["brokers"] = settings["brokers"]

    @staticmethod
    def _validate_optional_columns(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "optional_columns" in settings and isinstance(
            settings["optional_columns"],
            dict,
        ):
            validated["optional_columns"] = settings["optional_columns"]

    @staticmethod
    def _validate_transforms(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        if "transforms" in settings and isinstance(settings["transforms"], dict):
            validated["transforms"] = settings["transforms"]

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
        config_str += f"  Backup Enabled: {self.backup_enabled}\n"
        config_str += f"  Backup Path: {self.backup_path}\n"
        config_str += f"  Max Backups: {self.max_backups}\n"
        config_str += f"  Transforms: {self.transforms}\n"
        return config_str

    def __repr__(self) -> str:
        """Return a concise representation of the Config object."""
        return f"<Config config_path={self.config_path}>"
