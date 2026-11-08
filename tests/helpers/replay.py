"""Build transactions in memory and replay them, with no folio or config behind them."""

from __future__ import annotations

from decimal import Decimal

from domain import AccountType, Action, Currency, FeeConvention
from engine.accounts import Account
from engine.fees import FeeRules
from engine.fx_rates import FxRates
from engine.replay import ReplayConfig, replay
from engine.types import ReplayResult, TxnRow
from services.symbols import SymbolResolver

FLAT_FX = FxRates(("2020-01-01",), (Decimal("1.30"),))


def make_row(
    txn_id: int,
    date: str,
    action: Action,
    *,
    amount: str = "0",
    units: str = "0",
    price: str = "0",
    fee: str = "0",
    currency: str = "CAD",
    ticker: str | None = "TEST",
    account: str = "IBKR-PERSONAL",
    settle_date: str | None = None,
) -> TxnRow:
    """Build one transaction with every money column already Decimal."""
    return TxnRow(
        txn_id=txn_id,
        txn_date=date,
        settle_date=settle_date or date,
        action=action,
        amount=Decimal(amount),
        currency=Currency(currency),
        price=Decimal(price),
        units=Decimal(units),
        fee=Decimal(fee),
        ticker=ticker,
        account=account,
    )


def run(
    rows: list[TxnRow],
    fx: FxRates = FLAT_FX,
    types: dict[str, AccountType] | None = None,
    conventions: dict[str, FeeConvention] | None = None,
    aliases: list[tuple[str, str, str]] | None = None,
    margin: set[str] | None = None,
) -> ReplayResult:
    """Replay rows with explicit account facts, so no config is involved.

    Fee rules are resolved exactly as `ReplayConfig.build` resolves them: a
    convention is pinned only where `conventions` names the account, and the fee
    sign is always read from the rows.
    """
    names = {row.account for row in rows}
    types = types or dict.fromkeys(names, AccountType.NON_REGISTERED)
    margin = margin or set()
    conventions = conventions or {}
    accounts = {
        name: Account(
            name,
            account_type,
            margin=name in margin,
            fees=FeeRules(convention=conventions.get(name, FeeConvention.AUTO)),
        )
        for name, account_type in types.items()
    }
    cfg = ReplayConfig.build(rows, SymbolResolver(aliases or []), accounts)
    return replay(rows, fx, cfg)
