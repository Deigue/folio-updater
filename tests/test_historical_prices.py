"""Historical prices: fetching only the days missing, and reading a day's price."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from db import aliases, historical_prices
from engine.historical_prices import price_spans
from engine.ledger import open_ledger
from models import DateWindow, HistoricalPrice, HistoricalPriceCoverage, TickerAlias
from services.historical_prices import HistoricalPriceService
from services.price_history import PricePoint

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.context import TempContext

# A Monday, so the last finished session is the Friday before it.
MONDAY = date(2024, 6, 10)


def _closes(*days_and_prices: tuple[str, str]) -> list[PricePoint]:
    """Build true closes, oldest first."""
    return [PricePoint(day, Decimal(price)) for day, price in days_and_prices]


def _window(start: str, end: str) -> DateWindow:
    """Build an inclusive run of days."""
    return DateWindow(date.fromisoformat(start), date.fromisoformat(end))


def _stored(symbol: str) -> list[tuple[str, Decimal]]:
    """Read a symbol's stored prices as (day, price)."""
    return [(price.date, price.price) for price in historical_prices.read(symbol)]


def _covered(symbol: str) -> tuple[str, str] | None:
    """Read a symbol's coverage as (from, through)."""
    span = historical_prices.coverage([symbol]).get(symbol)
    return None if span is None else (span.fetched_from, span.fetched_through)


def _hold(symbol: str, start: str, end: str, *prices: tuple[str, str]) -> None:
    """Store prices and coverage as an earlier fetch would have."""
    historical_prices.write(
        [HistoricalPrice(symbol=symbol, date=d, price=Decimal(p)) for d, p in prices],
        [
            HistoricalPriceCoverage(
                symbol=symbol,
                fetched_from=start,
                fetched_through=end,
            ),
        ],
    )


# -- FETCHING -------------------------------------------------------------------


def test_a_first_fetch_stores_every_finished_session_from_the_start(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.historical = {
        "SPY": _closes(
            ("2024-06-03", "527.8"),
            ("2024-06-07", "534.01"),
            ("2024-06-10", "535.66"),  # today, still trading
        ),
    }

    with temp_ctx():
        update = HistoricalPriceService.ensure_coverage(
            {"SPY": _window("2024-06-03", "2024-06-09")},
            today=MONDAY,
        )

        stored, covered = _stored("SPY"), _covered("SPY")

    assert quote_provider.asked("historical_prices") == [["SPY"]]
    assert stored == [
        ("2024-06-03", Decimal("527.8")),
        ("2024-06-07", Decimal("534.01")),
    ]
    assert covered == ("2024-06-03", "2024-06-09")
    assert update.stored == 2


def test_a_later_fetch_asks_only_for_the_days_since(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.historical = {
        "SPY": _closes(("2024-06-07", "1"), ("2024-06-10", "535.66")),
    }

    with temp_ctx():
        _hold("SPY", "2024-06-03", "2024-06-07", ("2024-06-07", "534.01"))

        HistoricalPriceService.ensure_coverage(
            {"SPY": _window("2024-06-03", "2024-06-11")},
            today=date(2024, 6, 12),
        )

        stored, covered = _stored("SPY"), _covered("SPY")

    assert stored == [
        ("2024-06-07", Decimal("534.01")),
        ("2024-06-10", Decimal("535.66")),
    ]
    assert covered == ("2024-06-03", "2024-06-11")


def test_nothing_is_asked_while_coverage_reaches_the_last_session(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    """Through Friday is up to date over the weekend and on Monday morning."""
    with temp_ctx():
        _hold("SPY", "2024-06-03", "2024-06-07")
        # A closed holding needs nothing past its last day, however long ago.
        _hold("MSFT", "2023-01-03", "2023-03-31")

        update = HistoricalPriceService.ensure_coverage(
            {
                "SPY": _window("2024-06-03", "2024-06-09"),
                "MSFT": _window("2023-01-03", "2023-03-31"),
                # Bought today: no session of it has finished yet.
                "VFV.TO": _window("2024-06-10", "2024-06-09"),
            },
            today=MONDAY,
        )
        covered = historical_prices.coverage(["VFV.TO"])

    assert quote_provider.asked("historical_prices") == []
    assert covered == {}
    assert update.fetched == 0


def test_coverage_grows_backward_for_an_older_trade(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.historical = {
        "SPY": _closes(
            ("2024-06-03", "527.8"),
            ("2024-06-05", "9.99"),  # already held: the stored price stays
            ("2024-06-07", "534.01"),
        ),
    }

    with temp_ctx():
        _hold("SPY", "2024-06-05", "2024-06-09", ("2024-06-05", "534.67"))

        HistoricalPriceService.ensure_coverage(
            {"SPY": _window("2024-06-03", "2024-06-09")},
            today=MONDAY,
        )

        stored, covered = _stored("SPY"), _covered("SPY")

    assert stored == [
        ("2024-06-03", Decimal("527.8")),
        ("2024-06-05", Decimal("534.67")),
        ("2024-06-07", Decimal("534.01")),
    ]
    assert covered == ("2024-06-03", "2024-06-09")


def test_a_symbol_with_no_prices_is_covered_and_not_asked_again(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    """Still held a week later, it is not asked about each new session."""
    with temp_ctx():
        first = HistoricalPriceService.ensure_coverage(
            {"GONE": _window("2024-06-03", "2024-06-09")},
            today=MONDAY,
        )
        HistoricalPriceService.ensure_coverage(
            {"GONE": _window("2024-06-03", "2024-06-16")},
            today=date(2024, 6, 17),
        )

        stored, covered = _stored("GONE"), _covered("GONE")

    assert quote_provider.asked("historical_prices") == [["GONE"]]
    assert first.no_data == {"GONE"}
    assert stored == []
    assert covered == ("2024-06-03", "2024-06-09")


def test_an_older_trade_asks_again_for_a_symbol_with_no_prices(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.historical = {"GONE": _closes(("2024-05-31", "12.5"))}

    with temp_ctx():
        _hold("GONE", "2024-06-03", "2024-06-09")

        HistoricalPriceService.ensure_coverage(
            {"GONE": _window("2024-05-31", "2024-06-09")},
            today=MONDAY,
        )

        stored = _stored("GONE")

    assert stored == [("2024-05-31", Decimal("12.5"))]


def test_a_throttled_or_failed_symbol_is_asked_again_next_time(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.throttled = {"SLOW"}
    quote_provider.failed = {"DOWN"}
    spans = {
        "SLOW": _window("2024-06-03", "2024-06-09"),
        "DOWN": _window("2024-06-03", "2024-06-09"),
    }

    with temp_ctx():
        update = HistoricalPriceService.ensure_coverage(spans, today=MONDAY)
        HistoricalPriceService.ensure_coverage(spans, today=MONDAY)

        covered = historical_prices.coverage()

    assert covered == {}
    assert update.throttled == {"SLOW"}
    assert update.failed == {"DOWN"}
    assert [sorted(asked) for asked in quote_provider.asked("historical_prices")] == [
        ["DOWN", "SLOW"],
        ["DOWN", "SLOW"],
    ]


def test_an_unreachable_provider_is_never_asked(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.up = False

    with temp_ctx():
        HistoricalPriceService.ensure_coverage(
            {"SPY": _window("2024-06-03", "2024-06-09")},
            today=MONDAY,
        )

        covered = historical_prices.coverage()

    assert quote_provider.asked("historical_prices") == []
    assert covered == {}


# -- READING --------------------------------------------------------------------


def test_a_days_price_is_the_latest_close_on_or_before_it(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _hold(
            "SPY",
            "2024-06-03",
            "2024-06-09",
            ("2024-06-06", "535.66"),
            ("2024-06-07", "534.01"),
        )

        on_friday = HistoricalPriceService.price_on("SPY", date(2024, 6, 7))
        on_sunday = HistoricalPriceService.price_on("SPY", date(2024, 6, 9))
        before_any = HistoricalPriceService.price_on("SPY", date(2024, 6, 4))
        past_coverage = HistoricalPriceService.price_on("SPY", date(2024, 6, 10))
        never_fetched = HistoricalPriceService.price_on("MSFT", date(2024, 6, 7))

    assert on_friday == Decimal("534.01")
    assert on_sunday == Decimal("534.01")  # a weekend takes Friday's close
    assert before_any is None
    assert past_coverage is None
    assert never_fetched is None


# -- SPANS ----------------------------------------------------------------------


def _buy(ticker: str, day: str) -> None:
    """Seed a buy of 10 units in Canadian dollars, which needs no FX rate."""
    seed_transaction(
        ticker=ticker,
        date=day,
        currency="CAD",
        amount="-1000",
        price="100",
        units="10",
    )


def test_each_symbol_needs_prices_from_its_first_trade_while_held(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _buy("RY.TO", "2024-03-04")
        _buy("RY.TO", "2024-05-01")
        _buy("VFV.TO", "2024-01-15")
        seed_transaction(
            action="SELL",
            ticker="VFV.TO",
            date="2024-02-20",
            currency="CAD",
            amount="1100",
            price="110",
            units="-10",
        )

        spans = price_spans(open_ledger(offline=True), today=MONDAY)

    assert spans == {
        # Still held, so through the last finished day.
        "RY.TO": _window("2024-03-04", "2024-06-09"),
        # Sold out, so nothing is needed after its last trade.
        "VFV.TO": _window("2024-01-15", "2024-02-20"),
    }


def test_a_renamed_security_needs_prices_under_its_symbol_today(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _buy("FB", "2022-01-10")
        aliases.write(
            [
                TickerAlias(
                    old_ticker="FB",
                    new_ticker="META",
                    effective_date="2022-06-09",
                ),
            ],
        )

        spans = price_spans(open_ledger(offline=True), today=MONDAY)

    assert spans == {"META": _window("2022-01-10", "2024-06-09")}
