"""Yahoo Finance as the quote provider, through yfinance.

`import yfinance` is deliberately lazy, inside `load_yfinance`. It is a heavy
import, most commands never price anything, and `--offline` must be able to run
without the package resolving at all.
"""

from __future__ import annotations

import logging
import socket
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from app import get_config
from domain import TORONTO_TZ
from domain.numeric import dec
from models import Fundamentals, QuoteSplit
from models.cells import read_number, read_text
from models.columns import Kind, column_of
from services.price_history import PricePoint, months_back
from services.quote_provider import (
    HistoryBars,
    IntradayBars,
    PriceFetch,
    ProviderDescription,
    ProviderPrice,
)
from term import announce

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import ModuleType

logger = logging.getLogger(__name__)

# Fetches are network-bound, so cap the workers: a large folio must not open a
# hundred connections.
_MAX_FETCH_WORKERS = 8

# The host every price, history and crumb request goes through.
_PROVIDER_HOST = ("query1.finance.yahoo.com", 443)
# A connection opens in well under a second or not at all: an unreachable
# provider drops the handshake rather than refusing it.
_PROBE_TIMEOUT_SECONDS = 5

# Exchange suffixes Yahoo already spells with a dot. Anything else after a dot
# is a share class, which Yahoo spells with a dash, so this has to be an
# explicit list rather than a pattern: `BRK.B` and `PMN.V` look identical.
_EXCHANGE_SUFFIXES = frozenset({"TO", "V", "NE", "CN", "AQ", "L", "AX", "HK"})


def load_yfinance() -> ModuleType:
    """Import yfinance, keeping its deprecation warnings off the terminal.

    Returns:
        The yfinance module.
    """
    import yfinance as yf  # noqa: PLC0415 (heavy, and unused when offline)

    warnings.filterwarnings("ignore", category=DeprecationWarning, module="^yfinance")
    # Hidden, a failed request looks exactly like a symbol Yahoo does not know.
    yf.config.debug.hide_exceptions = False
    return yf


def yahoo_spelling(symbol: str) -> str:
    """Render a symbol the way Yahoo Finance spells it.

    Yahoo separates an *exchange* with a dot and a *share class* with a dash.
    Both can appear at once, so the exchange suffix is split off first and only
    what remains is dashed: `REI.UN.TO` is the `UN` class of `REI` on Toronto,
    and Yahoo spells that `REI-UN.TO`.

    Args:
        symbol: The folio symbol, used as it is: a renamed ticker is not followed.

    Returns:
        The Yahoo form.
    """
    symbol = symbol.strip().upper()
    base, _, suffix = symbol.rpartition(".")
    if base and suffix in _EXCHANGE_SUFFIXES:
        return f"{base.replace('.', '-')}.{suffix}"
    return symbol.replace(".", "-")


def _connect_within(address: tuple[str, int], seconds: float) -> None:
    """Open and close one connection to a host, within a single time budget.

    `socket.create_connection` gives every address a host resolves to the full
    timeout, and Yahoo's resolves to half a dozen, so an unreachable provider
    would cost their sum. Here each address gets only what time is left.

    Args:
        address: The host and port.
        seconds: How long every attempt together may take.

    Raises:
        TimeoutError: If no address accepted a connection in time.
    """
    deadline = time.monotonic() + seconds
    for family, kind, proto, _, target in socket.getaddrinfo(
        *address,
        type=socket.SOCK_STREAM,
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with socket.socket(family, kind, proto) as sock:
            sock.settimeout(remaining)
            try:
                sock.connect(target)
            except OSError:
                continue
            return
    msg = f"no connection to {address[0]} within {seconds:g}s"
    raise TimeoutError(msg)


def _run_concurrently[T](
    fetch: Callable[[str], T | None],
    provider_symbols: Sequence[str],
) -> list[T]:
    """Run a per-symbol fetch across a thread pool, dropping empty results.

    Args:
        fetch: Looks up one provider symbol; returns None on a miss.
        provider_symbols: Provider spellings to fetch, never empty: the service
            returns before fetching nothing.

    Returns:
        Whatever `fetch` returned for each symbol, in no particular order,
        with the None misses already filtered out.
    """
    workers = min(len(provider_symbols), _MAX_FETCH_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, provider_symbols))
    return [result for result in results if result is not None]


@dataclass(frozen=True)
class _PriceAttempt:
    """How pricing one provider symbol came out.

    Attributes:
        provider_symbol: The provider spelling asked for.
        price: The price, when it priced.
        outcome: "throttled" or "failed" when the request failed, and None when
            it priced or the provider does not know the symbol.
    """

    provider_symbol: str
    price: ProviderPrice | None = None
    outcome: Literal["throttled", "failed"] | None = None


class YahooProvider:
    """Quotes, closes and history from Yahoo Finance."""

    name = "yfinance"

    def __init__(
        self,
        host: tuple[str, int] = _PROVIDER_HOST,
        probe_seconds: float = _PROBE_TIMEOUT_SECONDS,
    ) -> None:
        """Set where reachability is probed, and for how long.

        Args:
            host: The host and port every request goes through.
            probe_seconds: How long the probe may take in all.
        """
        self._host = host
        self._probe_seconds = probe_seconds
        self._reachable: bool | None = None

    def reachable(self) -> bool:
        """Whether Yahoo Finance accepts a connection, asked once.

        yfinance waits out a 30 second timeout on every request it cannot open,
        one symbol at a time behind its cookie lock, and reports each as a symbol
        with no price. Unreachable, a refresh would spend many minutes failing and
        then negative-cache every symbol, so one quick connection settles it first.

        Returns:
            False when the connection could not be opened, which is said once.
        """
        if self._reachable is None:
            try:
                _connect_within(self._host, self._probe_seconds)
            except OSError as error:
                announce.warning(
                    f"Yahoo Finance is unreachable ({error}); "
                    "using cached quotes and history.",
                )
                self._reachable = False
            else:
                self._reachable = True
        return self._reachable

    def spell(self, symbol: str) -> str:
        """Spell a symbol as Yahoo does, or as configured for it."""
        # Read per call, not kept, so an override added with a config reload applies.
        overrides = {
            str(key).strip().upper(): str(value)
            for key, value in get_config().quotes_symbol_overrides.items()
        }
        return overrides.get(symbol.strip().upper()) or yahoo_spelling(symbol)

    def prices(self, provider_symbols: Sequence[str]) -> PriceFetch:
        """Fetch the price and previous close for a batch of symbols.

        Args:
            provider_symbols: Provider spellings to price.

        Returns:
            The prices, and which symbols were throttled or failed.
        """
        yf = load_yfinance()
        provider_errors = yf.exceptions
        tickers = yf.Tickers(" ".join(provider_symbols))
        now = datetime.now(UTC).isoformat()

        def _price(provider_symbol: str) -> _PriceAttempt:
            ticker = tickers.tickers.get(provider_symbol)
            if ticker is None:
                return _PriceAttempt(provider_symbol)
            fast = ticker.fast_info
            try:
                price = fast.last_price
            except provider_errors.YFTickerMissingError:
                # Also raised for an HTTP error body, which yfinance cannot tell
                # apart from a symbol with no prices.
                return _PriceAttempt(provider_symbol)
            except Exception as error:  # noqa: BLE001 (any request can fail)
                logger.debug("No price for %s: %r", provider_symbol, error)
                throttled = isinstance(error, provider_errors.YFRateLimitError)
                return _PriceAttempt(
                    provider_symbol,
                    outcome="throttled" if throttled else "failed",
                )
            if price is None:
                return _PriceAttempt(provider_symbol)
            return _PriceAttempt(
                provider_symbol,
                ProviderPrice(
                    price=read_number(price, provider=True),
                    prev_close=read_number(_previous_close(fast), provider=True),
                    currency=read_text(_attr(fast, "currency"), provider=True),
                    quote_time=now,
                ),
            )

        prices: dict[str, ProviderPrice] = {}
        throttled: set[str] = set()
        failed: set[str] = set()
        for attempt in _run_concurrently(_price, provider_symbols):
            if attempt.price is not None:
                prices[attempt.provider_symbol] = attempt.price
            elif attempt.outcome == "throttled":
                throttled.add(attempt.provider_symbol)
            elif attempt.outcome == "failed":
                failed.add(attempt.provider_symbol)
        return PriceFetch(prices, frozenset(throttled), frozenset(failed))

    def daily_closes(
        self,
        provider_symbols: Sequence[str],
    ) -> dict[str, list[PricePoint]]:
        """Fetch the last few daily closes for a batch, in one request.

        Args:
            provider_symbols: Provider spellings to fetch.

        Returns:
            Each provider symbol the download covered, mapped to its closes.
        """
        frame = _download(provider_symbols, period="5d", interval="1d")
        found = {p: _dated_closes(frame, p) for p in provider_symbols}
        return {
            provider_symbol: closes
            for provider_symbol, closes in found.items()
            if closes
        }

    def describe(
        self,
        provider_symbols: Sequence[str],
    ) -> dict[str, ProviderDescription]:
        """Fetch name, sector, exchange, market cap and fundamentals for a batch.

        Args:
            provider_symbols: Provider spellings to describe.

        Returns:
            Each provider symbol the provider described, mapped to what it said.
        """
        yf = load_yfinance()

        def _describe(provider_symbol: str) -> tuple[str, ProviderDescription] | None:
            try:
                ticker = yf.Ticker(provider_symbol)
                info = ticker.info
            except Exception as error:  # noqa: BLE001 (per-symbol scrape)
                logger.debug("No metadata for %s: %s", provider_symbol, error)
                return None
            if not info:
                return None
            return provider_symbol, ProviderDescription(
                name=read_text(
                    info.get("shortName") or info.get("longName"),
                    provider=True,
                ),
                sector=read_text(info.get("sector"), provider=True),
                exchange=read_text(
                    info.get("exchange") or info.get("fullExchangeName"),
                    provider=True,
                ),
                market_cap=read_number(info.get("marketCap"), provider=True),
                currency=read_text(info.get("currency"), provider=True),
                fundamentals=_fundamentals(info),
                splits=_split_history(ticker),
            )

        return dict(_run_concurrently(_describe, provider_symbols))

    def history(
        self,
        provider_symbols: Sequence[str],
        *,
        today: date,
    ) -> dict[str, HistoryBars]:
        """Fetch a batch's closes for every stored range, in four downloads.

        Daily closes for the last year, weekly for five, monthly for the whole
        listing, and hourly bars for the last month's shape: enough for every
        stored range, and small whatever a symbol's age.

        Args:
            provider_symbols: Provider spellings to fetch.
            today: The day the ranges are measured back from.

        Returns:
            Each provider symbol the downloads covered, mapped to its bars.
        """
        # A margin either side of the earliest anchor, for holidays and gaps.
        margin = timedelta(days=14)
        daily_from = min(months_back(today, 12), date(today.year - 1, 12, 31))
        daily = _download(
            provider_symbols,
            interval="1d",
            start=(daily_from - margin).isoformat(),
        )
        weekly = _download(
            provider_symbols,
            interval="1wk",
            start=(months_back(today, 60) - margin).isoformat(),
        )
        monthly = _download(provider_symbols, interval="1mo", period="max")
        hourly = _download(
            provider_symbols,
            interval="60m",
            start=(months_back(today, 1) - margin).isoformat(),
        )
        bars = {
            provider_symbol: HistoryBars(
                daily=_dated_closes(daily, provider_symbol),
                weekly=_dated_closes(weekly, provider_symbol),
                monthly=_dated_closes(monthly, provider_symbol),
                hourly=_dated_closes(hourly, provider_symbol, intraday=True),
            )
            for provider_symbol in provider_symbols
        }
        return {
            provider_symbol: found
            for provider_symbol, found in bars.items()
            if found.daily or found.weekly or found.monthly or found.hourly
        }

    def intraday(self, provider_symbols: Sequence[str]) -> dict[str, IntradayBars]:
        """Fetch a batch's bars over the last week of sessions.

        One-minute bars for `2h`, five-minute bars for the rest. Regular
        sessions only: no pre- or after-market trading.

        Args:
            provider_symbols: Provider spellings to fetch.

        Returns:
            Each provider symbol the downloads covered, mapped to its bars.
        """
        minutes = _download(provider_symbols, interval="1m", period="5d")
        fives = _download(provider_symbols, interval="5m", period="5d")
        bars = {
            provider_symbol: IntradayBars(
                minutes=_dated_closes(minutes, provider_symbol, intraday=True),
                fives=_dated_closes(fives, provider_symbol, intraday=True),
            )
            for provider_symbol in provider_symbols
        }
        return {
            provider_symbol: found
            for provider_symbol, found in bars.items()
            if found.minutes or found.fives
        }


def _download(provider_symbols: Sequence[str], **window: str) -> Any:  # noqa: ANN401 (pandas)
    """Download a batch's bars over one window, grouped by symbol.

    Args:
        provider_symbols: Provider spellings to fetch.
        **window: The `interval`, and a `start` or a `period`.

    Returns:
        The provider's frame, or None when it returned nothing.
    """
    return load_yfinance().download(
        list(provider_symbols),
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        timeout=get_config().quotes_timeout_seconds,
        **window,
    )


def _dated_closes(
    frame: Any,  # noqa: ANN401 (a pandas frame from the provider)
    provider_symbol: str,
    *,
    intraday: bool = False,
) -> list[PricePoint]:
    """Pull one symbol's closes, with their dates, out of a batched download.

    Args:
        frame: The provider's download, or None when it returned nothing.
        provider_symbol: The provider symbol to read.
        intraday: Stamp each close with its UTC time rather than its date.

    Returns:
        The closes, oldest first.
    """
    if frame is None or frame.empty:
        return []
    try:
        closes = _close_column(frame, provider_symbol).dropna()
    except (KeyError, TypeError, IndexError):
        return []
    points: list[PricePoint] = []
    for stamp, close in zip(closes.index, closes.to_numpy(), strict=True):
        moment = stamp.to_pydatetime()
        if intraday:
            aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
            when = aware.astimezone(UTC).isoformat()
        else:
            when = moment.date().isoformat()
        points.append(PricePoint(when, dec(close)))
    return points


def _close_column(frame: Any, provider_symbol: str) -> Any:  # noqa: ANN401 (pandas)
    """Find one symbol's closes in a download, whatever shape it came in.

    Grouped by ticker, a download keys its columns by symbol and then field,
    and yfinance keeps that shape even for a single symbol. A flat frame, with
    the fields alone, can only hold one symbol, so it is read as that one.
    """
    if frame.columns.nlevels > 1:
        return frame[provider_symbol]["Close"]
    return frame["Close"]


def _fundamentals(info: Mapping[str, Any]) -> Fundamentals:
    """Read the fundamentals out of a provider's info, in one set of units."""
    raw: dict[str, object] = {
        "quote_type": info.get("quoteType"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "eps": _first(info, "trailingEps", "epsTrailingTwelveMonths"),
        "beta": _first(info, "beta", "beta3Year"),
        "dividend_rate": _first(
            info,
            "dividendRate",
            "trailingAnnualDividendRate",
        ),
        "dividend_yield": _from_percent(info.get("dividendYield")),
        "last_dividend": info.get("lastDividendValue"),
        "ex_dividend_date": _epoch_date(info.get("exDividendDate")),
        # An earnings time is a moment in the trading day, so it is read
        # on the market's clock: after the close is still the same day.
        "earnings_date": _epoch_date(
            _first(info, "earningsTimestampStart", "earningsTimestamp"),
            TORONTO_TZ,
        ),
        "high_52": info.get("fiftyTwoWeekHigh"),
        "low_52": info.get("fiftyTwoWeekLow"),
        "avg_50": info.get("fiftyDayAverage"),
        "avg_200": info.get("twoHundredDayAverage"),
        "volume": _first(info, "regularMarketVolume", "volume"),
        "avg_volume": info.get("averageVolume"),
        "expense_ratio": _from_percent(info.get("netExpenseRatio")),
        "total_assets": _first(info, "totalAssets", "netAssets"),
        "category": info.get("category"),
        "fund_family": info.get("fundFamily"),
    }
    read: dict[str, Any] = {}
    for item in fields(Fundamentals):
        numeric = column_of(item).kind is Kind.NUMBER
        value = raw.get(item.name)
        read[item.name] = (
            read_number(value, provider=True)
            if numeric
            else read_text(value, provider=True)
        )
    return Fundamentals(**read)


def _split_history(ticker: Any) -> tuple[QuoteSplit, ...] | None:  # noqa: ANN401
    """Read a ticker's split history, or None when it could not be fetched."""
    try:
        history = ticker.splits
    except Exception as error:  # noqa: BLE001 (a separate provider request)
        logger.debug("No split history: %s", error)
        return None
    return tuple(
        QuoteSplit(when.strftime("%Y-%m-%d"), Decimal(str(ratio)))
        for when, ratio in history.items()
    )


def _first(info: Mapping[str, Any], *keys: str) -> object | None:
    """Read the first of several provider keys that holds anything."""
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    return None


def _from_percent(value: object) -> Decimal | None:
    """Turn a provider percentage (0.77 meaning 0.77%) into a ratio."""
    if read_text(value, provider=True) is None:
        return None
    number = dec(value)
    return number / 100 if number else None


def _epoch_date(value: object, zone: tzinfo = UTC) -> str | None:
    """Turn a provider epoch timestamp, in seconds, into a `YYYY-MM-DD` date.

    Args:
        value: The timestamp. A bare date, such as an ex-dividend date, arrives
            as midnight UTC, which is why UTC is the default.
        zone: The clock to read the date on.

    Returns:
        The date, or None when the value is not a timestamp.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    try:
        return datetime.fromtimestamp(float(value), zone).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _previous_close(fast: object) -> object | None:
    """Read the previous **regular session** close off a `fast_info`.

    `previous_close` groups a week of *pre/post-market* hourly bars by calendar
    date and takes the last bar of the day before (counting extended-hours).

    `regular_market_previous_close` takes the second-last row of the daily bar
    series, which is the prior session's official close and what every quote
    page measures the day's change from.

    Args:
        fast: A yfinance `fast_info` object.

    Returns:
        The prior regular-session close, falling back to the extended-hours
        figure.
    """
    regular = _attr(fast, "regular_market_previous_close")
    if regular is not None:
        return regular
    return _attr(fast, "previous_close")


def _attr(source: object, name: str) -> object | None:
    """Read one attribute off a provider object without trusting it exists."""
    try:
        return getattr(source, name, None)
    except Exception:  # noqa: BLE001 (fast_info computes lazily and can raise)
        return None
