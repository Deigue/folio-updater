"""A stand-in yfinance, so the Yahoo adapter runs its real code against it."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
from yfinance import exceptions as yf_exceptions

if TYPE_CHECKING:
    from collections.abc import Generator


class FastInfo:
    """Stands in for yfinance's lazily-computed `fast_info`."""

    def __init__(self, price: float | None, prev: float, currency: str) -> None:
        """Hold the price, previous close and currency a ticker reports."""
        self.last_price = price
        self.previous_close = prev
        self.currency = currency


class FailingFastInfo:
    """A `fast_info` whose price read raises, as yfinance's does on a failed request."""

    def __init__(self, error: Exception) -> None:
        """Hold the error the price read raises."""
        self._error = error

    @property
    def last_price(self) -> float:
        """Raise the held error, as the provider's request did."""
        raise self._error


class FakeTicker:
    """Stands in for one `yf.Ticker`."""

    def __init__(
        self,
        fast: object,
        info: dict | None = None,
        splits: pd.Series | None = None,
    ) -> None:
        """Hold what the ticker's price, info and split reads return."""
        self.fast_info = fast
        self.info = info if info is not None else {}
        self.splits = pd.Series(dtype=float) if splits is None else splits


class _FakeTickers:
    """Stands in for `yf.Tickers`, which exposes a `.tickers` mapping."""

    def __init__(self, tickers: dict[str, FakeTicker]) -> None:
        self.tickers = tickers


def _fake_yfinance(
    tickers: dict[str, Any],
    frame: pd.DataFrame | None = None,
) -> ModuleType:
    """Build a stand-in `yfinance` module the fetch seams can import.

    The seams import `yfinance` lazily by name, so putting a module here in
    `sys.modules` exercises the real adapter code: how attributes are read off
    `fast_info`, how a miss is recognised, how the raw dict is shaped. Only the
    provider itself is replaced, never our own logic.
    """
    module = ModuleType("yfinance")
    module.Tickers = lambda names: _FakeTickers(  # ty: ignore[unresolved-attribute]
        {name: tickers[name] for name in names.split() if name in tickers},
    )
    module.Ticker = lambda name: tickers[name]  # ty: ignore[unresolved-attribute]
    module.downloaded = []  # ty: ignore[unresolved-attribute]

    def download(symbols: list[str], **_kwargs: object) -> pd.DataFrame | None:
        module.downloaded.append(list(symbols))
        return frame

    module.download = download  # ty: ignore[unresolved-attribute]
    module.exceptions = yf_exceptions  # ty: ignore[unresolved-attribute]
    module.config = SimpleNamespace(  # ty: ignore[unresolved-attribute]
        debug=SimpleNamespace(hide_exceptions=True),
    )
    return module


@contextmanager
def stand_in_provider(
    tickers: dict[str, Any],
    frame: pd.DataFrame | None = None,
) -> Generator[ModuleType]:
    """Install the stand-in provider for the duration of a block.

    Yields:
        The stand-in module, whose `downloaded` lists each batch downloaded.
    """
    module = _fake_yfinance(tickers, frame)
    with patch.dict(sys.modules, {"yfinance": module}):
        yield module
