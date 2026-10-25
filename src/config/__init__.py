"""Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""

from __future__ import annotations

import re
import sys
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from config.optional_fields import OptionalFieldsConfig
from config.transforms import TransformsConfig
from config.validation import FEE_CONVENTIONS, validate_config
from domain import AccountType, Column, FeeConvention
from term import console_warning

if TYPE_CHECKING:
    from decimal import Decimal


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
                    "exclude_accounts": ["Cash"],
                },
            },
            "optional_columns": {},
            "transforms": {"rules": []},
            "accounts": {
                "naming_convention": True,
                "map": {},
                "defaults": {"amount_includes_fees": "auto"},
            },
            "cost_basis": {
                "auto_getfx": True,
            },
            "display": {"currency": "CAD"},
            "checks": {
                "disabled": [],
                "ignore_tickers": [],
                "ignore_accounts": [],
            },
            "quotes": {
                "ttl_minutes": 15,
                "metadata_ttl_days": 30,
                "fundamentals_ttl_hours": 24,
                "timeout_seconds": 20,
                "symbol_overrides": {},
            },
            "contribution_room": {},
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
        try:
            self._ensure_dir(data_path)
        except OSError as error:
            # Only the commands that open the folio need it, and they fail on it.
            console_warning(f"Could not create the data folder {data_path}: {error}")
        self._data_path: Path = data_path
        backup_path = Path(settings["backup"]["path"])
        if not backup_path.is_absolute():
            backup_path: Path = (project_root / settings["backup"]["path"]).resolve()
        self._backup_path: Path = backup_path
        self._imports_path: Path = data_path / "imports"
        self._processed_path: Path = data_path / "processed"
        self._statements_path: Path = data_path / "statements"
        self._statements_processed_path: Path = self._statements_path / "processed"
        self._optional_fields = OptionalFieldsConfig(settings["optional_columns"])
        self._transforms = TransformsConfig(settings["transforms"])

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
        return self._settings["header_ignore"]

    @property
    def duplicate_approval_column(self) -> str:
        """Get the name of the column used to approve duplicate transactions."""
        return self._settings["duplicate_approval"]["column_name"]

    @property
    def duplicate_approval_value(self) -> str:
        """Get the value that indicates duplicate transaction approval."""
        return self._settings["duplicate_approval"]["approval_value"]

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
        return self._settings["backup"]["enabled"]

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
        return self._ensure_dir(self._imports_path)

    @property
    def processed_path(self) -> Path:
        """The directory where processed files are moved.

        This directory holds files that have already been imported
        into the folio database. The directory is created
        lazily when first accessed.
        """
        return self._ensure_dir(self._processed_path)

    @property
    def statements_path(self) -> Path:
        """The directory where statement files are stored.

        This directory holds monthly statement files that can be imported
        to update settlement dates. The directory is created
        lazily when first accessed.
        """
        return self._ensure_dir(self._statements_path)

    @property
    def statements_processed_path(self) -> Path:
        """The directory where fully imported statements are archived.

        A subfolder of the statements directory, so statements a run has
        finished with stay apart from the ones still waiting. The directory is
        created lazily when first accessed.
        """
        return self._ensure_dir(self._statements_processed_path)

    @property
    def max_backups(self) -> int:
        """Maximum number of backups to keep."""
        return self._settings["backup"]["max_backups"]

    @property
    def brokers(self) -> dict[str, dict[str, str]]:
        """Get broker configuration."""
        return self._settings["brokers"]

    @property
    def tickers_sheet(self) -> str:
        """Get the tickers sheet name."""
        return self._settings["sheets"]["tickers"]

    @property
    def txn_sheet(self) -> str:
        """Get the transactions sheet name."""
        return self._settings["sheets"]["txns"]

    @property
    def fx_sheet(self) -> str:
        """Get the FX sheet name."""
        return self._settings["sheets"]["fx"]

    @property
    def account_settings(self) -> dict[str, Any]:
        """The whole validated `accounts` block, for detecting any change to it."""
        return self._settings["accounts"]

    @property
    def account_map(self) -> dict[str, dict[str, Any]]:
        """Per-account overrides.

        The shorthand `WS-PERSONAL: NON_REGISTERED` is expanded during
        validation, so every entry here carries a `type` key and may carry an
        `amount_includes_fees` key.
        """
        return self._settings["accounts"]["map"]

    @property
    def account_types(self) -> dict[str, AccountType]:
        """Explicit account name to `AccountType` overrides.

        Only accounts the `<BROKER>-<TYPE>` naming convention cannot resolve
        need an entry here.
        """
        return {
            name: entry["type"]
            for name, entry in self.account_map.items()
            if entry.get("type")
        }

    @property
    def account_naming_convention(self) -> bool:
        """Whether an account's type may be inferred from its name."""
        return self._settings["accounts"]["naming_convention"]

    @property
    def account_fee_default(self) -> str:
        """Default fee convention for accounts with no explicit override."""
        return self._settings["accounts"]["defaults"]["amount_includes_fees"]

    def fee_convention_for(self, account: str) -> FeeConvention:
        """Resolve whether an account's `Amount` already contains the fee.

        Args:
            account: The account name as stored on the transaction.

        Returns:
            The account's explicit override where one is configured, otherwise
            the configured default. `auto` means detect it from the rows.
        """
        mapping = self.account_map
        entry = mapping.get(account) or mapping.get(account.upper()) or {}
        value = entry.get("amount_includes_fees", self.account_fee_default)
        return FEE_CONVENTIONS.get(str(value).lower(), FeeConvention.AUTO)

    @property
    def auto_getfx(self) -> bool:
        """Whether cost-base commands may fetch missing FX rates."""
        return self._settings["cost_basis"]["auto_getfx"]

    @property
    def display_currency(self) -> str:
        """Default currency cost-base outputs to."""
        return self._settings["display"]["currency"]

    @property
    def checks_disabled(self) -> list[str]:
        """Slugs of the `folio check` checks that should not run."""
        return self._settings["checks"]["disabled"]

    @property
    def checks_ignore_tickers(self) -> list[str]:
        """Securities that `folio check` will ignore."""
        return [ticker.upper() for ticker in self._settings["checks"]["ignore_tickers"]]

    @property
    def checks_ignore_accounts(self) -> list[str]:
        """Accounts that `folio check` will ignore."""
        return [name.upper() for name in self._settings["checks"]["ignore_accounts"]]

    @property
    def quotes_ttl_minutes(self) -> int:
        """How long a cached price stays fresh before a refetch is due."""
        return self._settings["quotes"]["ttl_minutes"]

    @property
    def quotes_metadata_ttl_days(self) -> int:
        """How long cached name, sector and market cap stay fresh."""
        return self._settings["quotes"]["metadata_ttl_days"]

    @property
    def quotes_fundamentals_ttl_hours(self) -> int:
        """How long cached fundamentals stay fresh for a command showing them."""
        return self._settings["quotes"]["fundamentals_ttl_hours"]

    @property
    def quotes_timeout_seconds(self) -> int:
        """How long to wait on the quote provider before giving up."""
        return self._settings["quotes"]["timeout_seconds"]

    @property
    def quotes_symbol_overrides(self) -> dict[str, str]:
        """Folio symbol to provider symbol, for the ones the rule cannot derive.

        Keyed upper-case so a lookup never depends on how it was written in YAML.
        """
        overrides = self._settings["quotes"]["symbol_overrides"]
        return {str(key).upper(): str(value) for key, value in overrides.items()}

    @property
    def contribution_room(self) -> dict[AccountType, dict[int, Decimal]]:
        """Contribution room by account type and year.

        A CRA limit is a person-level number covering every account of that type,
        so it is keyed by type rather than by account. An absent type simply
        means the room row is not shown for it.
        """
        return self._settings["contribution_room"]

    @property
    def acb_parquet(self) -> Path:
        """Path to the cached cost-base ledger."""
        return self._data_path / "acb.parquet"

    @property
    def txn_parquet(self) -> Path:
        """Path to transactions.parquet in data path."""
        return self._data_path / "transactions.parquet"

    @property
    def fx_parquet(self) -> Path:
        """Path to forex.parquet in data path."""
        return self._data_path / "forex.parquet"

    @property
    def tickers_parquet(self) -> Path:
        """Path to tickers.parquet in data path."""
        return self._data_path / "tickers.parquet"

    @classmethod
    def load(cls, project_root: Path | None = None) -> Config:
        """Load config.yaml from disk, creating it if it doesn't exist.

        Args:
            project_root: Optional Path to the project root. If None, uses
                `get_default_root_directory`, which searches upward for a `.git`
                folder or a `config.yaml` (or uses the frozen executable's folder).

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

        configuration = validate_config(configuration, cls.DEFAULT_CONFIG)
        return cls(resolved_root, configuration)

    @staticmethod
    def get_default_root_directory() -> Path:
        """Get the project root directory by searching upwards for markers."""
        if getattr(sys, "frozen", False):
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
    def _ensure_dir(path: Path) -> Path:
        """Create a directory (and its parents) if it is missing, and return it."""
        path.mkdir(parents=True, exist_ok=True)
        return path

    def __str__(self) -> str:
        """Return a human-readable dump of every configured setting."""
        transforms = (
            f"{len(self.transforms.rules)} rule(s), "
            f"{len(self.transforms.merge_groups)} merge group(s) and "
            f"{len(self.transforms.cancellations)} cancellation rule(s)"
        )
        account_overrides = {
            name: {key: str(value) for key, value in entry.items()}
            for name, entry in self.account_map.items()
        }
        details: dict[str, Any] = {
            "Config Path": self.config_path,
            "Project Root": self.project_root,
            "Folio Path": self.folio_path,
            "Data Path": self.data_path,
            "Database Path": self.db_path,
            "Log Level": self.log_level,
            "Sheets": self.sheets,
            "Header Keywords": f"{len(self.header_keywords)} column(s) mapped",
            "Header Ignore": self.header_ignore,
            "Duplicate Approval": (
                f"{self.duplicate_approval_column}={self.duplicate_approval_value}"
            ),
            "Backups": (
                f"{'enabled' if self.backup_enabled else 'disabled'}, "
                f"max {self.max_backups}, at {self.backup_path}"
            ),
            "Brokers": sorted(self.brokers) or "none configured",
            "Optional Columns": f"{len(self.optional_fields)} field(s) configured",
            "Transforms": transforms,
            "Account Naming Convention": self.account_naming_convention,
            "Account Overrides": account_overrides or "none configured",
            "Account Fee Default": self.account_fee_default,
            "Auto GetFX": self.auto_getfx,
            "Display Currency": self.display_currency,
            "Checks Disabled": self.checks_disabled or "none",
            "Checks Ignoring": (
                (self.checks_ignore_tickers + self.checks_ignore_accounts) or "nothing"
            ),
            "Quotes TTL": (
                f"{self.quotes_ttl_minutes}m prices, "
                f"{self.quotes_metadata_ttl_days}d metadata, "
                f"{self.quotes_fundamentals_ttl_hours}h fundamentals"
            ),
            "Quote Symbol Overrides": self.quotes_symbol_overrides or "none configured",
            "Contribution Room": (
                sorted(str(name) for name in self.contribution_room)
                or "none configured"
            ),
        }
        lines = "".join(f"  {label}: {value}\n" for label, value in details.items())
        return f" Config Details:\n{lines}"

    def __repr__(self) -> str:
        """Return a concise representation of the Config object."""
        return f"<Config config_path={self.config_path}>"


def write_missing_setting(config_path: Path, key: str, value: dict[str, Any]) -> bool:
    """Give a top-level setting a value, unless it already has one.

    The file is edited as text so the user's comments and layout survive: the
    setting's one empty line (`key: {}`, as `Config.load` writes it, a bare
    `key:`, or any other falsy value such as `[]` or `false`) is replaced, or the
    setting is appended when it is absent.

    Args:
        config_path: The config.yaml to edit.
        key: Top-level setting name.
        value: What to write under it.

    Returns:
        True when the file was changed, False when the setting already had a
        value.
    """
    text = config_path.read_text(encoding="utf-8")
    if (yaml.safe_load(text) or {}).get(key):
        return False

    block = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    # Past the check above, a `key:` line can only hold a falsy value.
    empty = re.compile(rf"^{re.escape(key)}:[^\n]*\n?", re.MULTILINE)
    if empty.search(text):
        edited = empty.sub(lambda _: block, text, count=1)
    else:
        separator = "" if not text or text.endswith("\n") else "\n"
        edited = f"{text}{separator}{block}"

    staging = config_path.with_name(f"{config_path.name}.tmp")
    with Path.open(staging, "w", encoding="utf-8") as f:
        f.write(edited)
    staging.replace(config_path)
    return True
