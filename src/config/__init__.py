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

from config.brokers import IbkrSettings, WealthsimpleSettings
from config.optional_fields import OptionalFieldsConfig
from config.settings import build_defaults, validate_config
from config.transforms import TransformsConfig
from config.validation import BROKER_IDS, FEE_CONVENTIONS, FEE_SIGNS, ConfigError
from domain import AccountType, BrokerId, FeeConvention, FeeSign
from term import console_warning

if TYPE_CHECKING:
    from decimal import Decimal


class Config:
    """Configuration for the folio."""

    DEFAULT_CONFIG: ClassVar[MappingProxyType[str, Any]] = MappingProxyType(
        build_defaults(),
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
    def audit_log_level(self) -> str:
        """Get the level the audit trail records at."""
        return self._settings["audit_log_level"]

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
        to confirm settlement dates. The directory is created
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
    def ibkr(self) -> IbkrSettings:
        """What folio downloads from IBKR."""
        return IbkrSettings(queries=MappingProxyType(self._settings["brokers"]["ibkr"]))

    @property
    def wealthsimple(self) -> WealthsimpleSettings:
        """How folio signs in to Wealthsimple and what it leaves out."""
        settings = self._settings["brokers"]["wealthsimple"]
        return WealthsimpleSettings(
            user_agent=settings["user_agent"],
            exclude_accounts=tuple(settings["exclude_accounts"]),
        )

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
        `amount_includes_fees` key. A `MARGIN` entry becomes a `NON_REGISTERED`
        type with `margin: True`.
        """
        return self._settings["accounts"]["map"]

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
        value = self._account_setting(
            account,
            "amount_includes_fees",
            self.account_fee_default,
        )
        return FEE_CONVENTIONS.get(str(value).lower(), FeeConvention.AUTO)

    @property
    def account_fee_sign_default(self) -> str:
        """Default fee sign for accounts with no explicit override."""
        return self._settings["accounts"]["defaults"]["fee_sign"]

    def fee_sign_for(self, account: str) -> FeeSign:
        """Resolve which sign an account stores a charged commission with.

        Args:
            account: The account name as stored on the transaction.

        Returns:
            The account's explicit override where one is configured, otherwise
            the configured default. `auto` means detect it from the rows.
        """
        value = self._account_setting(
            account,
            "fee_sign",
            self.account_fee_sign_default,
        )
        return FEE_SIGNS.get(str(value).lower(), FeeSign.AUTO)

    def broker_of(self, account: str) -> BrokerId | None:
        """Return the broker holding an account, or None for one folio never downloads.

        The account's `accounts.map` entry decides when it names a broker; otherwise
        the name's `<BROKER>-` prefix does.
        """
        mapped = self._account_setting(account, "broker", None)
        if mapped is not None:
            return BROKER_IDS[mapped]
        name = account.upper()
        return next((b for b in BrokerId if name.startswith(b.account_prefix)), None)

    def _account_setting(self, account: str, key: str, default: str | None) -> Any:  # noqa: ANN401
        """Read one setting from an account's `accounts.map` entry, else the default."""
        return self.account_map.get(account.upper(), {}).get(key, default)

    @property
    def auto_getfx(self) -> bool:
        """Whether cost-base commands may fetch missing FX rates."""
        return self._settings["cost_basis"]["auto_getfx"]

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
        """Path to fx.parquet in data path."""
        return self._data_path / "fx.parquet"

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
            configuration = _read_settings(config_yaml)

        configuration = validate_config(configuration)
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
            "Audit Log Level": self.audit_log_level,
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
            "Brokers": sorted(self._settings["brokers"]) or "none configured",
            "Optional Columns": f"{len(self.optional_fields)} field(s) configured",
            "Transforms": transforms,
            "Account Naming Convention": self.account_naming_convention,
            "Account Overrides": account_overrides or "none configured",
            "Account Fee Default": self.account_fee_default,
            "Auto GetFX": self.auto_getfx,
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


def _read_settings(config_yaml: Path) -> dict[str, Any]:
    """Read a config file's top-level settings.

    Raises:
        ConfigError: When the file is not valid YAML or does not hold a mapping.
    """
    try:
        with Path.open(config_yaml, "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
    except yaml.MarkedYAMLError as error:
        where = f" line {error.problem_mark.line + 1}" if error.problem_mark else ""
        msg = f"{config_yaml.name}{where}: {error.problem or 'invalid YAML'}"
        raise ConfigError(msg) from error
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        msg = f"{config_yaml.name} cannot be read: {error}"
        raise ConfigError(msg) from error
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        msg = (
            f"{config_yaml.name} must hold settings as key: value pairs, "
            f"not a {type(settings).__name__}."
        )
        raise ConfigError(msg)
    return settings


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
