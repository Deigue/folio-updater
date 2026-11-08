"""A symbol's cached quote as the `Quotes` table stores it, fundamentals inside it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Self

from domain import Column, Currency, QuoteStatus
from models.cells import (
    read_moment,
    read_number,
    read_text,
    required_text,
    stored_moment,
    stored_number,
)
from models.columns import Kind, column_of, flattened, stored

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta


@dataclass(frozen=True, kw_only=True)
class Fundamentals:
    """Details from provider for a given security beyond its price.

    Attributes:
        quote_type: The provider's kind of security: EQUITY, ETF, MUTUALFUND...
        trailing_pe: Price over the last twelve months' earnings.
        forward_pe: Price over the next twelve months' expected earnings.
        eps: Earnings per share over the last twelve months.
        beta: Volatility against the market. A fund's three-year beta.
        dividend_rate: Annual dividend per unit.
        dividend_yield: Annual dividend over price, as a ratio.
        last_dividend: The most recent dividend per unit.
        ex_dividend_date: The next or most recent ex-dividend date.
        earnings_date: The next earnings date, when the provider knows it.
        high_52: The 52-week high.
        low_52: The 52-week low.
        avg_50: The 50-day average price.
        avg_200: The 200-day average price.
        volume: The last session's volume.
        avg_volume: Average daily volume.
        expense_ratio: A fund's expense ratio, as a ratio.
        total_assets: A fund's assets under management.
        category: A fund's category.
        fund_family: A fund's manager.
    """

    quote_type: str | None = field(
        default=None,
        metadata=stored(Column.Quote.QUOTE_TYPE),
    )
    trailing_pe: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.TRAILING_PE, Kind.NUMBER),
    )
    forward_pe: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.FORWARD_PE, Kind.NUMBER),
    )
    eps: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.EPS, Kind.NUMBER),
    )
    beta: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.BETA, Kind.NUMBER),
    )
    dividend_rate: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.DIVIDEND_RATE, Kind.NUMBER),
    )
    dividend_yield: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.DIVIDEND_YIELD, Kind.NUMBER),
    )
    last_dividend: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.LAST_DIVIDEND, Kind.NUMBER),
    )
    ex_dividend_date: str | None = field(
        default=None,
        metadata=stored(Column.Quote.EX_DIVIDEND_DATE),
    )
    earnings_date: str | None = field(
        default=None,
        metadata=stored(Column.Quote.EARNINGS_DATE),
    )
    high_52: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.HIGH_52, Kind.NUMBER),
    )
    low_52: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.LOW_52, Kind.NUMBER),
    )
    avg_50: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.AVG_50, Kind.NUMBER),
    )
    avg_200: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.AVG_200, Kind.NUMBER),
    )
    volume: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.VOLUME, Kind.NUMBER),
    )
    avg_volume: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.AVG_VOLUME, Kind.NUMBER),
    )
    expense_ratio: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.EXPENSE_RATIO, Kind.NUMBER),
    )
    total_assets: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.TOTAL_ASSETS, Kind.NUMBER),
    )
    category: str | None = field(
        default=None,
        metadata=stored(Column.Quote.CATEGORY),
    )
    fund_family: str | None = field(
        default=None,
        metadata=stored(Column.Quote.FUND_FAMILY),
    )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read the fundamentals out of a quote's row.

        Args:
            row: The quote's row, keyed by column name.

        Returns:
            The fundamentals.
        """
        read: dict[str, object] = {}
        for item in fields(cls):
            column = column_of(item)
            value = row.get(column.name)
            read[item.name] = (
                _figure(value) if column.kind is Kind.NUMBER else read_text(value)
            )
        return cls(**read)  # ty: ignore[invalid-argument-type]

    def to_row(self) -> dict[str, object]:
        """Return the columns these fundamentals take in a quote's row."""
        row: dict[str, object] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            column = column_of(item)
            row[column.name] = (
                stored_number(value) if column.kind is Kind.NUMBER else value
            )
        return row


@dataclass(frozen=True)
class QuoteSplit:
    """One split in the provider's history of a security.

    Attributes:
        date: The day the provider dates it, `YYYY-MM-DD`.
        ratio: Units after per unit before, so a consolidation is below one.
    """

    date: str
    ratio: Decimal


@dataclass(frozen=True, kw_only=True)
class Quote:
    """A symbol's quote: its latest cached price and what the provider says of it.

    Attributes:
        symbol: The folio's own canonical symbol.
        provider_symbol: How the provider spells it.
        price: Price, in `currency`. None when unpriced.
        prev_close: Previous close, which is what `day_change` is measured from.
        currency: The currency the price is quoted in.
        name: Short name from the provider.
        sector: Sector from the provider.
        exchange: Listing exchange from the provider.
        market_cap: Market capitalisation, in `currency`.
        fundamentals: Valuation, dividend, trading-range and fund figures.
        splits: The provider's split history, oldest first, fetched with the
            metadata. None until it is known, which no splits is not.
        quote_time: The provider's own market timestamp.
        fetched_at: When the price was last fetched. Drives the TTL.
        meta_fetched_at: When the metadata, fundamentals included, was last
            fetched. Its own TTL.
        source: The provider the quote came from.
        status: How the last fetch attempt came out.
    """

    symbol: str = field(
        metadata=stored(Column.Quote.SYMBOL, key=True, upper=True, non_empty=True),
    )
    provider_symbol: str = field(
        metadata=stored(Column.Quote.PROVIDER_SYMBOL, required=True),
    )
    price: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.PRICE, Kind.NUMBER),
    )
    prev_close: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.PREV_CLOSE, Kind.NUMBER),
    )
    currency: Currency | None = field(
        default=None,
        metadata=stored(Column.Quote.CURRENCY),
    )
    name: str | None = field(default=None, metadata=stored(Column.Quote.NAME))
    sector: str | None = field(default=None, metadata=stored(Column.Quote.SECTOR))
    exchange: str | None = field(default=None, metadata=stored(Column.Quote.EXCHANGE))
    market_cap: Decimal | None = field(
        default=None,
        metadata=stored(Column.Quote.MARKET_CAP, Kind.NUMBER),
    )
    fundamentals: Fundamentals = field(
        default_factory=Fundamentals,
        metadata=flattened(Fundamentals),
    )
    splits: tuple[QuoteSplit, ...] | None = field(
        default=None,
        metadata=stored(Column.Quote.SPLITS),
    )
    quote_time: str | None = field(
        default=None,
        metadata=stored(Column.Quote.QUOTE_TIME),
    )
    fetched_at: datetime | None = field(
        default=None,
        metadata=stored(Column.Quote.FETCHED_AT),
    )
    meta_fetched_at: datetime | None = field(
        default=None,
        metadata=stored(Column.Quote.META_FETCHED_AT),
    )
    source: str | None = field(default=None, metadata=stored(Column.Quote.SOURCE))
    status: QuoteStatus = field(
        default=QuoteStatus.ERROR,
        metadata=stored(Column.Quote.STATUS),
    )

    @property
    def age(self) -> timedelta | None:
        """How long ago the price was fetched, or None if it never was."""
        if self.fetched_at is None:
            return None
        return datetime.now(UTC) - self.fetched_at

    def meta_older_than(self, max_age: timedelta) -> bool:
        """Whether the metadata is older than `max_age`, or was never fetched.

        Args:
            max_age: How old the metadata may be and still be used.

        Returns:
            True when a refetch is due.
        """
        if self.meta_fetched_at is None:
            return True
        return datetime.now(UTC) - self.meta_fetched_at > max_age

    @property
    def priced(self) -> bool:
        """Whether this quote can be used to value a holding."""
        return (
            self.status is QuoteStatus.OK and self.price is not None and self.price > 0
        )

    @property
    def day_change(self) -> Decimal | None:
        """The day's per-share move, or None when either side is missing."""
        if self.price is None or self.prev_close is None:
            return None
        return self.price - self.prev_close

    @property
    def from_high_52(self) -> Decimal | None:
        """How far the price sits from its 52-week high: -0.07 is 7% below it."""
        high = self.fundamentals.high_52
        if self.price is None or not high:
            return None
        return self.price / high - 1

    @property
    def day_change_pct(self) -> Decimal | None:
        """The day's move as a share of the previous close, or None without one."""
        change = self.day_change
        if change is None or not self.prev_close:
            return None
        return change / self.prev_close

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a cached quote; an unknown currency is None, an unknown status ERROR."""
        return cls(
            symbol=required_text(row.get(Column.Quote.SYMBOL), Column.Quote.SYMBOL),
            provider_symbol=required_text(
                row.get(Column.Quote.PROVIDER_SYMBOL),
                Column.Quote.PROVIDER_SYMBOL,
            ),
            price=_figure(row.get(Column.Quote.PRICE)),
            prev_close=_figure(row.get(Column.Quote.PREV_CLOSE)),
            currency=_known(Currency, row.get(Column.Quote.CURRENCY)),
            name=read_text(row.get(Column.Quote.NAME)),
            sector=read_text(row.get(Column.Quote.SECTOR)),
            exchange=read_text(row.get(Column.Quote.EXCHANGE)),
            market_cap=_figure(row.get(Column.Quote.MARKET_CAP)),
            fundamentals=Fundamentals.from_row(row),
            splits=_read_splits(row.get(Column.Quote.SPLITS)),
            quote_time=read_text(row.get(Column.Quote.QUOTE_TIME)),
            fetched_at=read_moment(row.get(Column.Quote.FETCHED_AT)),
            meta_fetched_at=read_moment(row.get(Column.Quote.META_FETCHED_AT)),
            source=read_text(row.get(Column.Quote.SOURCE)),
            status=_known(QuoteStatus, row.get(Column.Quote.STATUS))
            or QuoteStatus.ERROR,
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this quote is stored as, in table column order."""
        return {
            str(Column.Quote.SYMBOL): self.symbol,
            str(Column.Quote.PROVIDER_SYMBOL): self.provider_symbol,
            str(Column.Quote.PRICE): stored_number(self.price),
            str(Column.Quote.PREV_CLOSE): stored_number(self.prev_close),
            str(Column.Quote.CURRENCY): str(self.currency) if self.currency else None,
            str(Column.Quote.NAME): self.name,
            str(Column.Quote.SECTOR): self.sector,
            str(Column.Quote.EXCHANGE): self.exchange,
            str(Column.Quote.MARKET_CAP): stored_number(self.market_cap),
            **self.fundamentals.to_row(),
            str(Column.Quote.SPLITS): _stored_splits(self.splits),
            str(Column.Quote.QUOTE_TIME): self.quote_time,
            str(Column.Quote.FETCHED_AT): stored_moment(self.fetched_at),
            str(Column.Quote.META_FETCHED_AT): stored_moment(
                self.meta_fetched_at,
            ),
            str(Column.Quote.SOURCE): self.source,
            str(Column.Quote.STATUS): str(self.status),
        }


def _figure(value: object) -> Decimal | None:
    """Read a figure cell, a zero or unreadable one as unknown: no price is zero."""
    return read_number(value) or None


def _known[E: (Currency, QuoteStatus)](kind: type[E], value: object) -> E | None:
    """Read an enum cell, or None when it is empty or a value this build lacks."""
    read = read_text(value)
    if read is None:
        return None
    try:
        return kind(read.upper() if kind is Currency else read)
    except ValueError:
        return None


def _stored_splits(splits: tuple[QuoteSplit, ...] | None) -> str | None:
    """Render a split history for storage, keeping unknown a genuine null."""
    if splits is None:
        return None
    return json.dumps([[split.date, str(split.ratio)] for split in splits])


def _read_splits(value: object) -> tuple[QuoteSplit, ...] | None:
    """Read a split history back, keeping unknown as None."""
    read = read_text(value)
    if read is None:
        return None
    return tuple(QuoteSplit(when, Decimal(ratio)) for when, ratio in json.loads(read))
