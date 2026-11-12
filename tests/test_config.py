"""Tests for the config module."""

from __future__ import annotations

import inspect
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import pandas as pd
import pytest
import yaml

from app import bootstrap
from app.logging_setup import SharedLogHandler, get_audit_logger
from config import Config, ConfigError, write_missing_setting
from config.settings import SETTINGS, validate_config, written_value
from domain import TXN_ESSENTIALS, AccountType, FeeConvention, FeeSign
from ingest.mappers import TransactionMapper

from .helpers.console import capture_output

if TYPE_CHECKING:
    from collections.abc import Generator

    from .helpers.context import TempContext


def test_a_missing_or_empty_config_loads_the_defaults(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    with Path.open(config.config_path) as f:
        generated = yaml.safe_load(f)
    assert generated == Config.DEFAULT_CONFIG
    assert generated["transforms"] == {
        "rules": [],
        "merge_groups": [],
        "cancellations": [],
    }

    # The generated file reads back as exactly the defaults, with nothing to warn about.
    with capture_output() as captured:
        assert validate_config(generated) == Config.DEFAULT_CONFIG
    assert captured.get_text() == ""

    # A file the user blanked is read as no settings, not refused.
    config.config_path.write_text("", encoding="utf-8")
    assert Config.load(tmp_path).log_level == Config.DEFAULT_CONFIG["log_level"]


class _ReadRecorder(dict):
    """A settings mapping that records the key path of every read from it."""

    def __init__(
        self,
        data: dict[str, Any],
        path: tuple[str, ...],
        seen: set[tuple[str, ...]],
    ) -> None:
        super().__init__(data)
        self._path = path
        self._seen = seen

    def _read(self, key: str, value: object) -> object:
        path = (*self._path, key)
        self._seen.add(path)
        if isinstance(value, dict):
            return _ReadRecorder(value, path, self._seen)
        return value

    def __getitem__(self, key: str) -> object:
        return self._read(key, super().__getitem__(key))

    def get(self, key: str, default: object = None) -> object:  # ty: ignore[invalid-method-override]
        return self[key] if key in self else self._read(key, default)


def test_every_setting_config_reads_is_declared(tmp_path: Path) -> None:
    """A read no declaration covers would be missing from the generated file."""
    seen: set[tuple[str, ...]] = set()
    config = Config(tmp_path, _ReadRecorder(validate_config({}), (), seen))
    for name, _ in inspect.getmembers(Config, lambda a: isinstance(a, property)):
        getattr(config, name)
    config.fee_convention_for("ANY-TFSA")
    config.fee_sign_for("ANY-TFSA")

    declared = [tuple(setting.path.split(".")) for setting in SETTINGS]

    def on_the_declared_tree(path: tuple[str, ...]) -> bool:
        # A container of declarations, a declaration, or data inside a declared block.
        return any(
            path[: len(known)] == known or known[: len(path)] == path
            for known in declared
        )

    assert ("transforms", "merge_groups") in seen
    assert [path for path in sorted(seen) if not on_the_declared_tree(path)] == []


def test_a_missing_absolute_data_folder_is_created(
    tmp_path: Path,
    temp_ctx: TempContext,
) -> None:
    data = tmp_path / "elsewhere" / "data"
    with temp_ctx({"data_path": str(data)}) as ctx:
        assert ctx.config.data_path == data
        assert data.is_dir()


def test_a_rule_with_an_unquoted_yaml_boolean_is_ignored_with_a_warning(
    temp_ctx: TempContext,
) -> None:
    """`Ticker: [ON]` is read as `[True]`, so the rule would never match."""
    transforms = yaml.safe_load(
        """
rules:
  - conditions: {Ticker: [ON]}
    actions: {Ticker: ONSEMI}
  - conditions: {Ticker: [MSFT]}
    actions: {Ticker: ON}
  - conditions: {Ticker: ["ON"]}
    actions: {Ticker: ONSEMI}
""",
    )
    with capture_output() as captured, temp_ctx(transforms=transforms) as ctx:
        rules = ctx.config.transforms.rules

    # Only the rule with both values quoted survives, rather than the boolean
    # one being read as a ticker named `True`.
    assert [(rule.conditions, rule.actions) for rule in rules] == [
        ({"Ticker": ["ON"]}, {"Ticker": "ONSEMI"}),
    ]
    assert captured.get_text().count("holds a YAML boolean") == 2


PAY_GROUP = """
  - name: pay
    match_fields: [Ticker]
    source_actions: [Dividends, Tax]
    target_action: DIVIDEND
"""
UNDO_RULE = """
  - name: undo
    conditions: {Action: [CANCELLATION]}
    match_fields: [Ticker]
"""


def test_a_bare_rules_key_keeps_the_merge_and_cancellation_rules(
    temp_ctx: TempContext,
) -> None:
    """`rules:` with nothing under it is YAML null, and must not stop the parse."""
    transforms = yaml.safe_load(
        f"rules:\nmerge_groups:{PAY_GROUP}cancellations:{UNDO_RULE}",
    )
    with capture_output() as captured, temp_ctx(transforms=transforms) as ctx:
        parsed = ctx.config.transforms

    assert parsed.rules == []
    assert [group.name for group in parsed.merge_groups] == ["pay"]
    assert [rule.name for rule in parsed.cancellations] == ["undo"]
    assert "ignoring" not in captured.get_text()


def test_transform_entries_that_cannot_be_used_warn_and_the_rest_survive(
    temp_ctx: TempContext,
) -> None:
    """A stray `-`, a bare word or a half-written entry is lost, so it is reported."""
    transforms = yaml.safe_load(
        f"""
rules:
  - conditions: {{Ticker: [MSFT]}}
    actions: {{Ticker: SPY}}
  -
  - just text
  - conditions: {{Ticker: [NFLX]}}
merge_groups:{PAY_GROUP}  - name: short
    match_fields: [Ticker]
    source_actions: [Dividends]
    target_action: DIVIDEND
  -
cancellations:{UNDO_RULE}  - name: no-conditions
    match_fields: [Ticker]
  -
""",
    )
    with capture_output() as captured, temp_ctx(transforms=transforms) as ctx:
        parsed = ctx.config.transforms

    assert [rule.actions for rule in parsed.rules] == [{"Ticker": "SPY"}]
    assert [group.name for group in parsed.merge_groups] == ["pay"]
    assert [rule.name for rule in parsed.cancellations] == ["undo"]
    text = captured.get_text()
    assert text.count("ignoring a rule") == 3
    assert "ignoring merge group 'short'" in text
    assert text.count("ignoring a merge group") == 1
    assert "ignoring cancellation rule 'no-conditions'" in text
    assert text.count("ignoring a cancellation rule") == 1

    # A block that is not a list is lost whole, so that is reported too.
    with capture_output() as captured, temp_ctx(transforms={"rules": "oops"}) as ctx:
        assert ctx.config.transforms.rules == []
    assert "transforms.rules: oops is not a list; ignoring it." in captured.get_text()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("log_level: [unclosed\n", r"config\.yaml line \d+", id="syntax"),
        pytest.param("- a\n- b\n", r"config\.yaml .*key: value", id="not-a-mapping"),
        pytest.param(
            "accounts:\n  map:\n    zz-tfsa: TFSA\n    ZZ-TFSA: RRSP\n",
            r"zz-tfsa and ZZ-TFSA",
            id="conflicting-account-keys",
        ),
        pytest.param(
            "contribution_room:\n  TFSA: {2025: 7000}\n  tfsa: {2025: 6500}\n",
            r"TFSA and tfsa",
            id="conflicting-room-types",
        ),
    ],
)
def test_a_config_file_that_cannot_be_used_stops_the_load(
    tmp_path: Path,
    text: str,
    reason: str,
) -> None:
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=reason):
        Config.load(tmp_path)


def test_a_log_rollover_another_process_blocks_is_skipped_quietly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows will not rename a log that another folio command holds open."""
    log_file = tmp_path / "folio.log"
    handler = SharedLogHandler(log_file, when="midnight", encoding="utf-8")

    def held_open(*_args: object) -> NoReturn:
        raise PermissionError

    monkeypatch.setattr(os, "rename", held_open)
    handler.rolloverAt = int(time.time()) - 1  # midnight has passed

    handler.emit(logging.makeLogRecord({"msg": "still written"}))
    handler.close()

    assert handler.rolloverAt > time.time()
    assert "still written" in log_file.read_text(encoding="utf-8")


def test_backup_settings_are_read_from_config(temp_ctx: TempContext) -> None:
    with temp_ctx({"backup": {"enabled": True, "max_backups": 3}}) as ctx:
        assert ctx.config.backup_enabled
        assert ctx.config.max_backups == 3


def test_relative_path_resolves(temp_ctx: TempContext) -> None:
    with temp_ctx({"folio_path": "data/testfolio.xlsx"}) as ctx:
        config = ctx.config
        assert config.folio_path.is_absolute()
        assert "testfolio.xlsx" in str(config.folio_path)


def test_absolute_path_kept(tmp_path: Path, temp_ctx: TempContext) -> None:
    absolute_path: Path = tmp_path / "absolute.xlsx"
    with temp_ctx({"folio_path": str(absolute_path)}) as ctx:
        assert ctx.config.folio_path == absolute_path


def test_settings_that_cannot_be_read_fall_back(temp_ctx: TempContext) -> None:
    with (
        capture_output() as captured,
        temp_ctx(
            {
                "optional_columns": {
                    "Memo": {"keywords": "note"},
                    "Odd": {"type": "bogus"},
                    "Blank": None,
                    "Count": {"keywords": {"note": 1}},
                    "Listed": ["note"],
                },
                "log_level": 3,
                "audit_log_level": "LOUD",
                "sheets": {"tickers": "Tickers", "txns": None, "unknown_sheet": "Nope"},
                "header_ignore": {"ID": True},
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
        ) as ctx,
    ):
        config = ctx.config
        assert config.log_level == "ERROR"  # Defaults to ERROR on bad value
        assert config.audit_log_level == "INFO"
        assert "InvalidKeyword" not in config.header_keywords
        assert config.header_ignore == []
        assert config.brokers == Config.DEFAULT_CONFIG["brokers"]
        assert config.txn_sheet == "Txns"
        assert "unknown_sheet" not in config.sheets
        assert config.max_backups == 50  # A bool is not a backup count.
        assert config.duplicate_approval_column == "Duplicate"
        assert config.duplicate_approval_value == "1"
        # An unusable optional column is skipped, and a lone keywords string
        # is one keyword.
        fields = config.optional_fields.get_all_fields()
        assert set(fields) == {"Memo"}
        assert fields["Memo"].keywords == ["note"]
    text = " ".join(captured.get_text().split())
    assert "optional_columns.Odd.type: bogus is not one of" in text
    assert "optional_columns.Count.keywords: {'note': 1} is not a name" in text
    assert "ignoring Odd." in text
    assert "ignoring Count." in text
    assert "optional_columns.Listed: ['note'] is not a mapping of settings" in text
    # A column left empty is not configured, so there is nothing to warn about.
    assert "Blank" not in text
    assert "sheets.unknown_sheet: unknown setting" in text
    assert "brokers: notadict is not a mapping of settings; using the defaults." in text


@pytest.fixture
def project_logs(tmp_path: Path) -> Generator[Path]:
    """Take back every handler a reload rooted at tmp_path adds.

    The logs follow the project root, so a reload writes into tmp_path, but the
    handlers stay on the process's loggers for the rest of the worker's run.

    Yields:
        The logs folder a reload rooted at tmp_path writes into.
    """
    loggers = [logging.getLogger(), get_audit_logger()]
    saved = [(lg, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield tmp_path / "logs"
    for lg, level, propagate, handlers in saved:
        for handler in [h for h in lg.handlers if h not in handlers]:
            lg.removeHandler(handler)
            handler.close()
        lg.setLevel(level)
        lg.propagate = propagate


def test_reload_config_reads_the_file_again(
    tmp_path: Path,
    temp_ctx: TempContext,
    project_logs: Path,
) -> None:
    good_folio = tmp_path / "good.xlsx"
    with temp_ctx() as ctx:
        with Path.open(ctx.config.config_path, mode="w") as f:
            yaml.safe_dump(
                {
                    "folio_path": str(good_folio),
                    "log_level": "INFO",
                    "sheets": {"tickers": "TKR", "txns": "TXNS"},
                    "header_keywords": {"TxnDate": ["settledate"]},
                },
                f,
            )

        new_config: Config = bootstrap.reload_config(tmp_path)

    assert new_config.folio_path == good_folio
    assert new_config.tickers_sheet == "TKR"
    assert new_config.txn_sheet == "TXNS"
    assert new_config.header_keywords["TxnDate"] == ["settledate"]
    # The new log level reaches the logs, which the reload sets up.
    assert logging.getLogger().level == logging.INFO
    assert (project_logs / "folio.log").exists()
    assert (project_logs / "audit.log").exists()


def test_the_audit_log_records_info_until_its_own_level_is_raised(
    tmp_path: Path,
    project_logs: Path,
) -> None:
    """`log_level` quiets folio.log only, and a raised audit level takes on reload."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("log_level: ERROR\n", encoding="utf-8")
    bootstrap.reload_config(tmp_path)
    get_audit_logger().info("ADD TXN first")

    config_yaml.write_text(
        "log_level: ERROR\naudit_log_level: ERROR\n",
        encoding="utf-8",
    )
    bootstrap.reload_config(tmp_path)
    get_audit_logger().info("ADD TXN second")

    audit = (project_logs / "audit.log").read_text(encoding="utf-8")
    assert "ADD TXN first" in audit
    assert "ADD TXN second" not in audit
    assert "ADD TXN" not in (project_logs / "folio.log").read_text(encoding="utf-8")


def test_cost_base_defaults(temp_ctx: TempContext) -> None:
    """The naming convention handles the common case, so nothing is required."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert config.account_map == {}
        assert config.account_naming_convention is True
        assert config.account_fee_default == "auto"
        assert config.auto_getfx is True
        assert config.acb_parquet.name == "acb.parquet"


def test_quotes_defaults(temp_ctx: TempContext) -> None:
    """Nothing is required: the defaults are what a normal user wants."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert config.quotes_ttl_minutes == 15
        assert config.quotes_metadata_ttl_days == 30
        assert config.quotes_timeout_seconds == 20
        assert config.quotes_symbol_overrides == {}


def test_quotes_settings_are_read_from_config(temp_ctx: TempContext) -> None:
    overrides = {
        "ttl_minutes": 5,
        "metadata_ttl_days": 90,
        "timeout_seconds": 45,
        "symbol_overrides": {"VNTKR.TO": "VNTKR.V"},
    }
    with temp_ctx(quotes=overrides) as ctx:
        config = ctx.config
        assert config.quotes_ttl_minutes == 5
        assert config.quotes_metadata_ttl_days == 90
        assert config.quotes_timeout_seconds == 45
        # Keyed upper-case so a lookup never depends on YAML casing.
        assert config.quotes_symbol_overrides == {"VNTKR.TO": "VNTKR.V"}


@pytest.mark.parametrize("bad", [0, -5, "fifteen", None, True])
def test_a_nonsense_quote_ttl_keeps_the_default(
    temp_ctx: TempContext,
    bad: object,
) -> None:
    """A zero TTL would mean 'always stale' and a bool is not a count."""
    with temp_ctx(quotes={"ttl_minutes": bad}) as ctx:
        assert ctx.config.quotes_ttl_minutes == 15


def test_check_suppressions_are_trimmed_and_matched_in_upper_case(
    temp_ctx: TempContext,
) -> None:
    checks = {
        "disabled": [" stale-quotes ", None],
        "ignore_tickers": ["msft", ""],
        "ignore_accounts": ["zz-tfsa"],
    }
    with temp_ctx(checks=checks) as ctx:
        assert ctx.config.checks_disabled == ["stale-quotes"]
        assert ctx.config.checks_ignore_tickers == ["MSFT"]
        assert ctx.config.checks_ignore_accounts == ["ZZ-TFSA"]


def test_contribution_room_is_read_by_type_and_year(temp_ctx: TempContext) -> None:
    room = {"TFSA": {2025: 7000, 2026: 7000}, "RRSP": {2026: 9500}}
    with temp_ctx(contribution_room=room) as ctx:
        configured = ctx.config.contribution_room
        assert configured[AccountType.TFSA][2026] == Decimal(7000)
        assert configured[AccountType.RRSP][2026] == Decimal(9500)


def test_contribution_room_drops_what_it_cannot_resolve(
    temp_ctx: TempContext,
) -> None:
    room = {
        "TFSA": {2026: 7000},
        "NOTATYPE": {2026: 1000},  # not an account type
        "RRSP": "not a mapping",  # not a year mapping
        "FHSA": {"nineteen": 500},  # not a year
    }
    with temp_ctx(contribution_room=room) as ctx:
        configured = ctx.config.contribution_room

        # A typo costs the room row, never the whole command.
        assert set(configured) == {AccountType.TFSA}
        assert configured[AccountType.TFSA] == {2026: Decimal(7000)}


def test_contribution_room_ignores_a_resp_with_a_warning(
    temp_ctx: TempContext,
) -> None:
    room = {"TFSA": {2026: 7000}, "RESP": {2026: 2500}}
    with capture_output() as captured, temp_ctx(contribution_room=room) as ctx:
        configured = ctx.config.contribution_room

    # An RESP limit is lifetime and per beneficiary, so no room year fits it.
    assert set(configured) == {AccountType.TFSA}
    assert "ignoring RESP" in captured.get_text()


def test_contribution_room_defaults_to_nothing_configured(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        assert ctx.config.contribution_room == {}


def test_accounts_map_accepts_both_forms(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "WS-PERSONAL": "NON_REGISTERED",
                "QT-TFSA": {"type": "TFSA", "amount_includes_fees": True},
                "zz-joint": {"type": "PERSONAL", "amount_includes_fees": True},
            },
        },
    }
    with temp_ctx(overrides) as ctx:
        assert ctx.config.account_map["WS-PERSONAL"]["type"] == "NON_REGISTERED"
        assert ctx.config.account_map["QT-TFSA"]["type"] == "TFSA"
        # Keys are upper-cased and a type alias resolves, keeping the fee setting.
        assert ctx.config.account_map["ZZ-JOINT"]["type"] == "NON_REGISTERED"
        assert ctx.config.fee_convention_for("ZZ-JOINT") is FeeConvention.INCLUDED
        assert ctx.config.fee_convention_for("QT-TFSA") is FeeConvention.INCLUDED
        assert ctx.config.fee_convention_for("WS-PERSONAL") is FeeConvention.AUTO


def test_unparseable_account_entries_are_dropped(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "GOOD-TFSA": "TFSA",
                "BAD": "NOT_A_TYPE",
                True: "TFSA",  # an unquoted ON, read by YAML as a boolean
                "ODD-RRSP": {"type": "RRSP", "amount_includes_fees": "maybe"},
            },
        },
    }
    with capture_output() as captured, temp_ctx(overrides) as ctx:
        assert set(ctx.config.account_map) == {"GOOD-TFSA", "ODD-RRSP"}
        assert ctx.config.account_map["GOOD-TFSA"]["type"] == "TFSA"
        assert (
            "accounts.map.BAD: NOT_A_TYPE is not an account type" in captured.get_text()
        )
        assert "ignoring True, which YAML read as a boolean" in captured.get_text()
        # The bad fee value is dropped, but the usable type survives.
        assert ctx.config.account_map["ODD-RRSP"]["type"] == "RRSP"
        assert ctx.config.fee_convention_for("ODD-RRSP") is FeeConvention.AUTO


@pytest.mark.parametrize(
    ("overrides", "warning"),
    [
        pytest.param(
            {"quotes": {"ttl_minutes": -5}},
            "quotes.ttl_minutes: -5 is not a positive whole number; using 15.",
            id="scalar",
        ),
        pytest.param(
            {"backup": {"enabled": "yes"}},
            "backup.enabled: yes is not true or false; using True.",
            id="boolean",
        ),
        pytest.param(
            {"accounts": {"map": ["ZZ-TFSA"]}},
            "accounts.map: ['ZZ-TFSA'] is not a mapping of settings; ignoring it.",
            id="block",
        ),
        pytest.param(
            {"quotes": "fast"},
            "quotes: fast is not a mapping of settings; using the defaults.",
            id="container",
        ),
        pytest.param(
            {"accounts": {"map": {"ZZ-RRSP": {"type": "RRSP", "fee_sign": "up"}}}},
            "accounts.map.ZZ-RRSP.fee_sign: up is not one of auto, negative or "
            "positive; ignoring it.",
            id="entry-field",
        ),
        pytest.param(
            {"accounts": {"map": {"ZZ-RRSP": "NOT_A_TYPE"}}},
            "accounts.map.ZZ-RRSP: NOT_A_TYPE is not an account type; ignoring it.",
            id="entry-shorthand",
        ),
        pytest.param(
            {"contribution_room": {"NOTATYPE": {2026: 1000}}},
            "contribution_room: ignoring NOTATYPE, which is not an account type.",
            id="entry-key",
        ),
        pytest.param(
            {"contribution_room": {"TFSA": {"twenty": 7000}}},
            "contribution_room.TFSA: ignoring twenty, which is not a year.",
            id="nested-entry-key",
        ),
        pytest.param(
            {"contribution_room": {"TFSA": {2026: True}}},
            "contribution_room.TFSA.2026: True is not an amount; ignoring it.",
            id="boolean-amount",
        ),
        pytest.param(
            {"contribution_room": {"TFSA": {2026: "lots"}}},
            "contribution_room.TFSA.2026: lots is not an amount; ignoring it.",
            id="word-amount",
        ),
        pytest.param(
            {"contribution_room": {"TFSA": {2026: float("inf")}}},
            "contribution_room.TFSA.2026: inf is not an amount; ignoring it.",
            id="infinite-amount",
        ),
        pytest.param(
            {"brokers": {"ibkr": {"Trades": [1]}}},
            "brokers.ibkr.Trades: [1] is not text; ignoring it.",
            id="broker-query-id",
        ),
        pytest.param(
            {"log_level": "FATAL"},
            "log_level: FATAL is not a log level (DEBUG, INFO, WARNING, ERROR or "
            "CRITICAL); using ERROR.",
            id="log-level-alias",
        ),
    ],
)
def test_a_value_that_cannot_be_used_warns(
    temp_ctx: TempContext,
    overrides: dict[str, object],
    warning: str,
) -> None:
    with capture_output() as captured, temp_ctx(overrides):
        pass
    assert warning in " ".join(captured.get_text().split())


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"accounts": None}, id="block"),
        pytest.param({"accounts": {"map": None}}, id="data-block"),
        pytest.param({"quotes": {"ttl_minutes": None}}, id="scalar"),
        pytest.param({"accounts": {"map": {"ZZ-TFSA": None}}}, id="entry"),
        pytest.param(
            {"accounts": {"map": {"ZZ-TFSA": {"type": "TFSA", "fee_sign": None}}}},
            id="entry-field",
        ),
        pytest.param({"accounts": {"defaults": {}}}, id="empty-mapping"),
        pytest.param({"quotes": {"ttl_minutes": ""}}, id="empty-string"),
        pytest.param({"quotes": []}, id="empty-list-block"),
    ],
)
def test_a_setting_left_empty_takes_its_default_quietly(
    temp_ctx: TempContext,
    overrides: dict[str, object],
) -> None:
    """`key:` with nothing under it is not configuring it."""
    with capture_output() as captured, temp_ctx(overrides) as ctx:
        assert ctx.config.fee_sign_for("ZZ-TFSA") is FeeSign.AUTO
        assert ctx.config.quotes_ttl_minutes == 15
    assert captured.get_text() == ""


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        pytest.param({"log_levle": "INFO"}, "log_levle", id="top-level"),
        pytest.param(
            {"duplicate_approval": {"column": "Dup"}},
            "duplicate_approval.column",
            id="nested",
        ),
        pytest.param(
            {"accounts": {"map": {"ZZ-TFSA": {"type": "TFSA", "colour": "red"}}}},
            "accounts.map.ZZ-TFSA.colour",
            id="inside-an-entry",
        ),
        pytest.param(
            {"transforms": {"merge_groups": [{"name": "pay", "matchfields": []}]}},
            "transforms.merge_groups[1].matchfields",
            id="inside-a-transform",
        ),
        pytest.param({"display": {"currency": "USD"}}, "display", id="retired-display"),
    ],
)
def test_an_unknown_key_warns(
    temp_ctx: TempContext,
    overrides: dict[str, object],
    path: str,
) -> None:
    with capture_output() as captured, temp_ctx(overrides):
        pass
    assert f"{path}: unknown setting, ignoring it." in captured.get_text()


def test_a_lone_name_is_a_list_of_one(temp_ctx: TempContext) -> None:
    overrides = {
        "header_ignore": "ID",
        "checks": {"disabled": "stale-quotes", "ignore_tickers": "msft"},
        "header_keywords": {"Fee": "commission"},
    }
    with capture_output() as captured, temp_ctx(overrides) as ctx:
        assert ctx.config.header_ignore == ["ID"]
        assert ctx.config.checks_disabled == ["stale-quotes"]
        assert ctx.config.checks_ignore_tickers == ["MSFT"]
        assert ctx.config.header_keywords["Fee"] == ["commission"]
    assert captured.get_text() == ""


def test_a_lone_keywords_string_matches_only_that_header(
    temp_ctx: TempContext,
) -> None:
    """`keywords: note` once read as the four headers n, o, t and e."""
    frame = pd.DataFrame(columns=[*TXN_ESSENTIALS, "note", "n"])
    with temp_ctx({"optional_columns": {"Memo": {"keywords": "note"}}}):
        mapped = TransactionMapper.map_headers(frame)
    assert "Memo" in mapped.columns
    assert "n" in mapped.columns
    assert "note" not in mapped.columns


def test_cost_basis_block(temp_ctx: TempContext) -> None:
    with temp_ctx({"cost_basis": {"auto_getfx": False}}) as ctx:
        assert ctx.config.auto_getfx is False


DOCS = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"


def _documented_example() -> dict[str, Any]:
    """Read the YAML block under `## Example structure` in the configuration docs."""
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("## Example structure", 1)[1]
    return yaml.safe_load(section.split("```yaml", 1)[1].split("```", 1)[0])


def test_the_documented_example_shows_every_setting_and_loads_cleanly(
    tmp_path: Path,
) -> None:
    example = _documented_example()

    missing = [s.path for s in SETTINGS if written_value(example, s.path) is None]
    assert missing == []
    with capture_output() as captured:
        Config(tmp_path, validate_config(example))
    assert captured.get_text() == ""


def test_every_top_level_setting_has_a_row_in_the_docs_table() -> None:
    text = DOCS.read_text(encoding="utf-8")
    assert [key for key in Config.DEFAULT_CONFIG if f"| **`{key}`**" not in text] == []


def test_fee_default_of_false_is_honoured(temp_ctx: TempContext) -> None:
    """`false` is a real convention here, not an absent value."""
    with temp_ctx({"accounts": {"defaults": {"amount_includes_fees": False}}}) as ctx:
        assert ctx.config.account_fee_default == "false"
        assert ctx.config.fee_convention_for("ANY-TFSA") is FeeConvention.EXCLUDED

    # An unrecognised spelling leaves the default in place.
    with temp_ctx({"accounts": {"defaults": {"amount_includes_fees": "maybe"}}}) as ctx:
        assert ctx.config.account_fee_default == "auto"


def test_fee_sign_is_set_per_account_and_defaults_to_auto(
    temp_ctx: TempContext,
) -> None:
    overrides = {
        "accounts": {
            "map": {
                "QT-TFSA": {"type": "TFSA", "fee_sign": "Negative"},
                "WS-TFSA": {"type": "TFSA", "fee_sign": "positive"},
            },
        },
    }
    with temp_ctx(overrides) as ctx:
        assert ctx.config.fee_sign_for("QT-TFSA") is FeeSign.NEGATIVE
        assert ctx.config.fee_sign_for("WS-TFSA") is FeeSign.POSITIVE
        assert ctx.config.fee_sign_for("OTHER-TFSA") is FeeSign.AUTO


def test_fee_sign_default_can_be_pinned_without_losing_the_convention_default(
    temp_ctx: TempContext,
) -> None:
    overrides = {
        "accounts": {
            "defaults": {"fee_sign": "negative"},
            "map": {"QT-TFSA": {"type": "TFSA", "fee_sign": "positive"}},
        },
    }
    with temp_ctx(overrides) as ctx:
        assert ctx.config.fee_sign_for("ANY-TFSA") is FeeSign.NEGATIVE
        assert ctx.config.fee_sign_for("QT-TFSA") is FeeSign.POSITIVE
        # Pinning one default leaves the other where it was.
        assert ctx.config.account_fee_default == "auto"

    with temp_ctx(
        {"accounts": {"defaults": {"amount_includes_fees": "included"}}},
    ) as ctx:
        assert ctx.config.fee_sign_for("ANY-TFSA") is FeeSign.AUTO
        assert ctx.config.fee_convention_for("ANY-TFSA") is FeeConvention.INCLUDED


def test_unrecognised_fee_sign_is_dropped(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "defaults": {"fee_sign": "sideways"},
            "map": {"ODD-RRSP": {"type": "RRSP", "fee_sign": "sideways"}},
        },
    }
    with temp_ctx(overrides) as ctx:
        # The bad value is dropped, but the usable type survives.
        assert ctx.config.account_map["ODD-RRSP"]["type"] == "RRSP"
        assert ctx.config.fee_sign_for("ODD-RRSP") is FeeSign.AUTO


ROOM = {"TFSA": {2025: 7000}}


@pytest.mark.parametrize(
    "unset",
    [
        pytest.param("contribution_room: {}\n", id="as-config-load-writes-it"),
        pytest.param("", id="absent"),
        pytest.param("contribution_room: []\n", id="empty-list"),
        pytest.param("contribution_room: false\n", id="false"),
        pytest.param('contribution_room: ""  # fill in\n', id="empty-string"),
    ],
)
def test_a_missing_setting_is_written_around_the_users_comments(
    tmp_path: Path,
    unset: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"# my folio\nlog_level: ERROR  # keep quiet\n{unset}\n# brokers\nbrokers: []",
        encoding="utf-8",
    )

    assert write_missing_setting(config_path, "contribution_room", ROOM) is True

    text = config_path.read_text(encoding="utf-8")
    assert "# my folio\nlog_level: ERROR  # keep quiet\n" in text
    assert "# brokers\nbrokers: []" in text
    assert text.count("contribution_room:") == 1
    assert Config.load(tmp_path).contribution_room == {
        AccountType.TFSA: {2025: Decimal(7000)},
    }


def test_a_setting_with_a_value_is_left_alone(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    existing = "contribution_room:\n  RRSP:\n    2025: 32490\n"
    config_path.write_text(existing, encoding="utf-8")

    assert write_missing_setting(config_path, "contribution_room", ROOM) is False
    assert config_path.read_text(encoding="utf-8") == existing
