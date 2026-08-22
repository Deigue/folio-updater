"""Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from utils.constants import AccountType, Column, FeeConvention
from utils.numeric import dec
from utils.optional_fields import OptionalFieldsConfig
from utils.transforms import TransformsConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

# How `amount_includes_fees` is spelled in YAML.
_FEE_CONVENTIONS: dict[str, FeeConvention] = {
    "auto": FeeConvention.AUTO,
    "true": FeeConvention.INCLUDED,
    "yes": FeeConvention.INCLUDED,
    "included": FeeConvention.INCLUDED,
    "false": FeeConvention.EXCLUDED,
    "no": FeeConvention.EXCLUDED,
    "excluded": FeeConvention.EXCLUDED,
}


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
                "timeout_seconds": 20,
                "symbol_overrides": {},
            },
            "contribution_room": {},
        },
    )

    # Keys copied straight from YAML once they type-check, mapped to the type
    # they must have and the coercion applied before storing. `object` accepts
    # any non-null value. Blocks needing per-field rules get their own
    # `_validate_*` helper instead.
    _PASSTHROUGH_KEYS: ClassVar[dict[str, tuple[type, Callable[[Any], Any]]]] = {
        "folio_path": (object, str),
        "data_path": (object, str),
        "header_ignore": (list, list),
        "brokers": (dict, dict),
        "optional_columns": (dict, dict),
        "transforms": (dict, dict),
    }

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
        self._statements_path: Path = data_path / "statements"
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
    def statements_path(self) -> Path:
        """The directory where statement files are stored.

        This directory holds monthly statement files that can be imported
        to update settlement dates. The directory is created
        lazily when first accessed.
        """
        if not self._statements_path.exists():
            self._statements_path.mkdir(parents=True, exist_ok=True)
        return self._statements_path

    @property
    def max_backups(self) -> int:
        """Maximum number of backups to keep."""
        return self._settings["backup"]["max_backups"]

    @property
    def brokers(self) -> dict[str, dict[str, str]]:
        """Get broker configuration."""
        return self._settings["brokers"]

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
    def account_map(self) -> dict[str, dict[str, Any]]:
        """Per-account overrides.

        The shorthand `WS-PERSONAL: NON_REGISTERED` is expanded during
        validation, so every entry here carries a `type` key and may carry an
        `amount_includes_fees` key.
        """
        return self._settings["accounts"]["map"]

    @property
    def account_types(self) -> dict[str, str]:
        """Explicit account name to `AccountType` overrides.

        Only accounts the `<BROKER>-<TYPE>` naming convention cannot resolve
        need an entry here.
        """
        return {
            name: str(entry["type"])
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
        return _FEE_CONVENTIONS.get(str(value).lower(), FeeConvention.AUTO)

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
        return {
            AccountType(name): {int(year): dec(limit) for year, limit in years.items()}
            for name, years in self._settings["contribution_room"].items()
        }

    @property
    def acb_parquet(self) -> Path:
        """Path to the cached cost-base master frame."""
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
    def _validate_config(settings: dict[str, Any]) -> dict[str, Any]:
        """Validate the loaded configuration against expected structure and values.

        Args:
            settings: Raw configuration dictionary to validate

        Returns:
            Validated configuration
        """
        validated: dict[str, Any] = deepcopy(dict(Config.DEFAULT_CONFIG))

        Config._validate_passthrough_keys(settings, validated)
        Config._validate_log_level(settings, validated)
        Config._validate_sheets(settings, validated)
        Config._validate_header_keywords(settings, validated)
        Config._validate_duplicate_approval(settings, validated)
        Config._validate_backup(settings, validated)
        Config._validate_accounts(settings, validated)
        Config._validate_cost_basis(settings, validated)
        Config._validate_display(settings, validated)
        Config._validate_checks(settings, validated)
        Config._validate_quotes(settings, validated)
        Config._validate_contribution_room(settings, validated)

        return validated

    @staticmethod
    def _validate_passthrough_keys(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Copy every `_PASSTHROUGH_KEYS` entry that type-checks.

        Values of the wrong type, and explicit nulls, leave the default in
        place.
        """
        for key, (expected, coerce) in Config._PASSTHROUGH_KEYS.items():
            value = settings.get(key)
            if value is not None and isinstance(value, expected):
                validated[key] = coerce(value)

    @staticmethod
    def _validate_accounts(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `accounts` block, normalising both `map` forms."""
        accounts = settings.get("accounts")
        if not isinstance(accounts, dict):
            return

        current = validated["accounts"]
        if isinstance(accounts.get("naming_convention"), bool):
            current["naming_convention"] = accounts["naming_convention"]

        defaults = accounts.get("defaults")
        if isinstance(defaults, dict) and "amount_includes_fees" in defaults:
            # `false` is a meaningful value here, so test for the key.
            fees = str(defaults["amount_includes_fees"]).lower()
            if fees in _FEE_CONVENTIONS:
                current["defaults"] = {"amount_includes_fees": fees}

        raw_map = accounts.get("map")
        if isinstance(raw_map, dict):
            current["map"] = {
                name: entry
                for name, entry in (
                    (str(name), Config._account_entry(value))
                    for name, value in raw_map.items()
                )
                if entry is not None
            }

    @staticmethod
    def _account_entry(value: Any) -> dict[str, Any] | None:  # noqa: ANN401
        """Normalise one `accounts.map` entry, or None when it is unusable."""
        entry = value if isinstance(value, dict) else {"type": value}
        account_type = entry.get("type")
        normalized: dict[str, Any] = {}

        if account_type is not None:
            try:
                normalized["type"] = str(AccountType(str(account_type).upper()))
            except ValueError:
                return None

        if "amount_includes_fees" in entry:
            fees = str(entry["amount_includes_fees"]).lower()
            if fees in _FEE_CONVENTIONS:
                normalized["amount_includes_fees"] = fees

        return normalized or None

    @staticmethod
    def _validate_cost_basis(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `cost_basis` block."""
        cost_basis = settings.get("cost_basis")
        if not isinstance(cost_basis, dict):
            return
        if isinstance(cost_basis.get("auto_getfx"), bool):
            validated["cost_basis"]["auto_getfx"] = cost_basis["auto_getfx"]

    @staticmethod
    def _validate_checks(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `checks` block."""
        checks = settings.get("checks")
        if not isinstance(checks, dict):
            return
        current = validated["checks"]
        for key in ("disabled", "ignore_tickers", "ignore_accounts"):
            value = checks.get(key)
            if isinstance(value, list):
                current[key] = [str(entry).strip() for entry in value if entry]

    @staticmethod
    def _validate_quotes(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `quotes` block, keeping the default on any bad value."""
        quotes = settings.get("quotes")
        if not isinstance(quotes, dict):
            return

        current = validated["quotes"]
        for key in ("ttl_minutes", "metadata_ttl_days", "timeout_seconds"):
            value = quotes.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                current[key] = value

        overrides = quotes.get("symbol_overrides")
        if isinstance(overrides, dict):
            current["symbol_overrides"] = {
                str(key): str(value)
                for key, value in overrides.items()
                if key and value
            }

    @staticmethod
    def _validate_contribution_room(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate `contribution_room`, dropping anything unresolvable.

        Keys are account types, then calendar years. A type the engine does not
        know, or a year that is not a number, is skipped.
        """
        room = settings.get("contribution_room")
        if not isinstance(room, dict):
            return

        resolved: dict[str, dict[str, float]] = {}
        for name, years in room.items():
            if not isinstance(years, dict):
                continue
            try:
                account_type = AccountType(str(name).strip().upper())
            except ValueError:
                continue
            limits = Config._room_years(years)
            if limits:
                resolved[str(account_type)] = limits

        validated["contribution_room"] = resolved

    @staticmethod
    def _room_years(years: dict[Any, Any]) -> dict[str, float]:
        """Read one account type's year-to-limit mapping."""
        limits: dict[str, float] = {}
        for year, limit in years.items():
            try:
                limits[str(int(str(year).strip()))] = float(limit)
            except (TypeError, ValueError):
                continue
        return limits

    @staticmethod
    def _validate_display(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `display` block."""
        display = settings.get("display")
        if not isinstance(display, dict):
            return
        currency = str(display.get("currency", "")).upper()
        if currency in {"CAD", "USD"}:
            validated["display"] = {"currency": currency}

    @staticmethod
    def _validate_log_level(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate `log_level`, falling back to the default on any bad value."""
        log_level = str(settings.get("log_level", validated["log_level"])).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = validated["log_level"]
        validated["log_level"] = log_level

    @staticmethod
    def _validate_sheets(settings: dict[str, Any], validated: dict[str, Any]) -> None:
        """Validate `sheets`, keeping only the known sheet names."""
        sheets = settings.get("sheets")
        if not isinstance(sheets, dict):
            return
        validated["sheets"].update(
            {
                name: str(value)
                for name, value in sheets.items()
                if name in validated["sheets"] and value is not None
            },
        )

    @staticmethod
    def _validate_header_keywords(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate `header_keywords`, dropping mappings for unknown columns."""
        header_keywords = settings.get("header_keywords")
        if not isinstance(header_keywords, dict):
            return
        validated["header_keywords"].update(
            {
                column: list(keywords)
                for column, keywords in header_keywords.items()
                # Add internal fields that are not in the default mapping here.
                if isinstance(keywords, list)
                and (
                    column in validated["header_keywords"]
                    or column == str(Column.Txn.FEE)
                )
            },
        )

    @staticmethod
    def _validate_duplicate_approval(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `duplicate_approval` block."""
        duplicate_approval = settings.get("duplicate_approval")
        if not isinstance(duplicate_approval, dict):
            return
        current = validated["duplicate_approval"]
        for key in ("column_name", "approval_value"):
            if duplicate_approval.get(key) is not None:
                current[key] = str(duplicate_approval[key])

    @staticmethod
    def _validate_backup(
        settings: dict[str, Any],
        validated: dict[str, Any],
    ) -> None:
        """Validate the `backup` block."""
        backup = settings.get("backup")
        if not isinstance(backup, dict):
            return
        current = validated["backup"]

        if isinstance(backup.get("enabled"), bool):
            current["enabled"] = backup["enabled"]

        if backup.get("path") is not None:
            current["path"] = str(backup["path"])

        max_backups = backup.get("max_backups")
        # bool is a subclass of int, so `max_backups: true` must not read as 1.
        if (
            isinstance(max_backups, int)
            and not isinstance(max_backups, bool)
            and max_backups > 0
        ):
            current["max_backups"] = max_backups

    def __str__(self) -> str:
        """Return a human-readable dump of every configured setting."""
        transforms = (
            f"{len(self.transforms.rules)} rule(s) and "
            f"{len(self.transforms.merge_groups)} merge group(s)"
        )
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
            "Account Overrides": self.account_map or "none configured",
            "Account Fee Default": self.account_fee_default,
            "Auto GetFX": self.auto_getfx,
            "Display Currency": self.display_currency,
            "Checks Disabled": self.checks_disabled or "none",
            "Checks Ignoring": (
                (self.checks_ignore_tickers + self.checks_ignore_accounts) or "nothing"
            ),
            "Quotes TTL": (
                f"{self.quotes_ttl_minutes}m prices, "
                f"{self.quotes_metadata_ttl_days}d metadata"
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
