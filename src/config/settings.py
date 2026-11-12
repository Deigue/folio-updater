"""Every setting `config.yaml` holds, declared once.

The defaults, the generated `config.yaml` and validation are all built from
`SETTINGS`, so a setting cannot reach one of them and miss another.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from config.validation import (
    FEE_CONVENTIONS,
    FEE_SIGNS,
    NOT_A_MAPPING,
    Unusable,
    account_name,
    account_type,
    as_written,
    boolean,
    currency,
    expand_margin,
    field_type,
    fields,
    log_level,
    map_of,
    money,
    names,
    non_empty,
    one_of,
    positive_int,
    report_unknown,
    report_unusable,
    room_type,
    text,
    year,
)
from domain import Column

if TYPE_CHECKING:
    from config.validation import Validator


@dataclass(frozen=True)
class Setting:
    """One setting: where it sits, its default, and what it may hold.

    Attributes:
        path: Its dotted key path in `config.yaml`, such as `quotes.ttl_minutes`.
        default: The value a new `config.yaml` is written with, and the one used
            when the setting is absent or unusable.
        validate: Turns the written value into a usable one.
        doc: One line on what it does.
    """

    path: str
    default: Any
    validate: Validator
    doc: str


def _keywords(column: str, *spellings: str) -> Setting:
    return Setting(
        f"header_keywords.{column!s}",
        list(spellings),
        names,
        f"Source headers read as the {column} column.",
    )


SETTINGS: tuple[Setting, ...] = (
    Setting("folio_path", "data/folio.xlsx", text, "The folio workbook."),
    Setting("data_path", "data", text, "Folder for the database and its files."),
    Setting("log_level", "ERROR", log_level, "Verbosity of folio.log."),
    Setting("audit_log_level", "INFO", log_level, "Verbosity of audit.log."),
    Setting("sheets.tickers", "Tickers", text, "Name of the Tickers sheet."),
    Setting("sheets.txns", "Txns", text, "Name of the Txns sheet."),
    Setting("sheets.fx", "FX", text, "Name of the FX sheet."),
    _keywords(
        Column.Txn.TXN_DATE,
        "txndate",
        "transaction date",
        "date",
        "tradedate",
        "reportdate",
    ),
    _keywords(Column.Txn.ACTION, "action", "type", "activity"),
    _keywords(Column.Txn.AMOUNT, "amount", "value", "total"),
    _keywords(Column.Txn.CURRENCY, "$", "currency", "curr"),
    _keywords(Column.Txn.PRICE, "price", "unit price", "share price"),
    _keywords(Column.Txn.UNITS, "units", "shares", "qty", "quantity"),
    _keywords(Column.Txn.TICKER, "ticker", "symbol", "stock"),
    _keywords(Column.Txn.ACCOUNT, "account", "alias", "account id"),
    _keywords(Column.Txn.SETTLE_DATE, "settledate", "settlement date"),
    _keywords(Column.Txn.FEE),
    Setting("header_ignore", [], names, "Source columns never imported."),
    Setting(
        "duplicate_approval.column_name",
        "Duplicate",
        text,
        "Column that marks a duplicate as approved.",
    ),
    Setting(
        "duplicate_approval.approval_value",
        "OK",
        text,
        "Value in that column that approves it.",
    ),
    Setting(
        "backup.enabled",
        default=True,
        validate=boolean,
        doc="Back up the folio before changing it.",
    ),
    Setting("backup.path", "backups", text, "Folder the backups go to."),
    Setting("backup.max_backups", 50, positive_int, "Backups to keep."),
    Setting(
        "brokers.ibkr",
        {"FlexReport": "111111", "CashActivity": "999999"},
        map_of(text, as_written),
        "Flex Query ID by query name; a name with Activity is cash activity.",
    ),
    Setting(
        "brokers.wealthsimple.user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) "
        "Gecko/20100101 Firefox/143.0",
        text,
        "Browser identity sent to Wealthsimple.",
    ),
    Setting(
        "brokers.wealthsimple.exclude_accounts",
        ["Cash"],
        names,
        "Wealthsimple accounts never downloaded.",
    ),
    Setting(
        "optional_columns",
        {},
        map_of(text, fields(keywords=names, type=field_type, strict=True)),
        "Extra columns to import, by name, with their keywords and type.",
    ),
    Setting("transforms.rules", [], as_written, "Rules applied to source rows."),
    Setting(
        "transforms.merge_groups",
        [],
        as_written,
        "Source rows merged into one transaction.",
    ),
    Setting(
        "transforms.cancellations",
        [],
        as_written,
        "Source rows that cancel each other out.",
    ),
    Setting(
        "accounts.naming_convention",
        default=True,
        validate=boolean,
        doc="Read an account's type from its name.",
    ),
    Setting(
        "accounts.map",
        {},
        map_of(
            account_name,
            non_empty(
                fields(
                    shorthand="type",
                    finish=expand_margin,
                    type=account_type,
                    amount_includes_fees=one_of(FEE_CONVENTIONS),
                    fee_sign=one_of(FEE_SIGNS),
                ),
            ),
            clash_stops=True,
        ),
        "Per-account type and fee overrides.",
    ),
    Setting(
        "accounts.defaults.amount_includes_fees",
        "auto",
        one_of(FEE_CONVENTIONS),
        "Whether an account's Amount already contains the fee.",
    ),
    Setting(
        "accounts.defaults.fee_sign",
        "auto",
        one_of(FEE_SIGNS),
        "Which sign an account stores a charged commission with.",
    ),
    Setting(
        "cost_basis.auto_getfx",
        default=True,
        validate=boolean,
        doc="Let cost-base commands fetch missing FX rates.",
    ),
    Setting("display.currency", "CAD", currency, "Currency cost-base output uses."),
    Setting("checks.disabled", [], names, "folio check checks that do not run."),
    Setting("checks.ignore_tickers", [], names, "Securities folio check ignores."),
    Setting("checks.ignore_accounts", [], names, "Accounts folio check ignores."),
    Setting("quotes.ttl_minutes", 15, positive_int, "Minutes a price stays fresh."),
    Setting(
        "quotes.metadata_ttl_days",
        30,
        positive_int,
        "Days name, sector and market cap stay fresh.",
    ),
    Setting(
        "quotes.fundamentals_ttl_hours",
        24,
        positive_int,
        "Hours fundamentals stay fresh.",
    ),
    Setting(
        "quotes.timeout_seconds",
        20,
        positive_int,
        "Seconds to wait on the quote provider.",
    ),
    Setting(
        "quotes.symbol_overrides",
        {},
        map_of(text, text),
        "Provider symbol by folio symbol, where the rule cannot derive it.",
    ),
    Setting(
        "contribution_room",
        {},
        map_of(room_type, non_empty(map_of(year, money))),
        "CRA contribution limits by account type and year.",
    ),
)


def build_defaults() -> dict[str, Any]:
    """Every setting at its default, nested as `config.yaml` holds them."""
    defaults: dict[str, Any] = {}
    for setting in SETTINGS:
        _place(defaults, setting.path, deepcopy(setting.default))
    return defaults


def validate_config(settings: dict[str, Any]) -> dict[str, Any]:
    """Read every setting from a loaded `config.yaml`.

    A setting that is absent or left empty keeps its default quietly. One whose
    value cannot be used keeps it with a warning, and so does a key that names
    no setting.

    Args:
        settings: The file's settings, as YAML read them.

    Returns:
        Every setting, nested as `config.yaml` holds them.
    """
    validated = build_defaults()
    for setting in SETTINGS:
        written = _written(settings, setting.path)
        if written is None:
            continue
        value = setting.validate(written, setting.path)
        if isinstance(value, Unusable):
            empty = setting.default in ([], {})
            fallback = "ignoring it" if empty else f"using {setting.default}"
            report_unusable(setting.path, written, value, fallback)
            continue
        _place(validated, setting.path, value)
    _report_unexpected(settings, "")
    return validated


def _written(settings: dict[str, Any], path: str) -> Any:  # noqa: ANN401
    """Find a setting's written value, or None when the file does not hold one."""
    value: Any = settings
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


_SETTING_PATHS = frozenset(setting.path for setting in SETTINGS)
_BLOCK_PATHS = frozenset(
    path.rsplit(".", depth)[0]
    for path in _SETTING_PATHS
    for depth in range(1, path.count(".") + 1)
)


def _report_unexpected(block: dict[str, Any], prefix: str) -> None:
    """Report each key in a block that names no setting, and each lost block.

    A setting's own value is left to its validator, since the keys inside a
    block like `accounts.map` are data.
    """
    for key, value in block.items():
        path = f"{prefix}{key}"
        if path in _SETTING_PATHS:
            continue
        if path not in _BLOCK_PATHS:
            report_unknown(path)
        elif isinstance(value, dict):
            _report_unexpected(value, f"{path}.")
        elif value is not None:
            report_unusable(path, value, NOT_A_MAPPING, "using the defaults")


def _place(target: dict[str, Any], path: str, value: Any) -> None:  # noqa: ANN401
    """Set a value at a dotted path, creating the blocks on the way."""
    *blocks, leaf = path.split(".")
    for block in blocks:
        target = target.setdefault(block, {})
    target[leaf] = value
