"""Build transactions in memory and replay them, with no folio or config behind them."""

from __future__ import annotations

from decimal import Decimal

from domain import AccountType, Action, Currency, FeeConvention
from engine.accounts import Account
from engine.fx_rates import FxRates
from engine.replay import ReplayConfig, detect_fee_signs, replay
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

    Fee signs are detected exactly as `ReplayConfig.build` detects them, since
    which sign means "charged" is read from the rows rather than configured.
    """
    names = {row.account for row in rows}
    types = types or dict.fromkeys(names, AccountType.NON_REGISTERED)
    margin = margin or set()
    cfg = ReplayConfig(
        accounts={
            name: Account(name, account_type, margin=name in margin)
            for name, account_type in types.items()
        },
        fee_conventions=conventions or dict.fromkeys(names, FeeConvention.AUTO),
        symbols=SymbolResolver(aliases or []),
        fee_signs=detect_fee_signs(rows),
    )
    return replay(rows, fx, cfg)
