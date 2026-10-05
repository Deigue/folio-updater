"""The demo folio: a realistic two-year portfolio built from real market history.

Everything the demo needs lives in this one module, including a snapshot of
real month-end closes, dividends and splits for every symbol it trades. The
snapshot keeps generation offline and deterministic. Refresh it from Yahoo
Finance with:

    uv run python -m datagen.demo --refresh

The snapshot stores what Yahoo serves: closes and dividends adjusted for every
split since, as if today's share count had always applied. A price or a
dividend on a given day is un-adjusted when it is read, so a trade before a
split books the price the market actually showed that day.
"""

from __future__ import annotations

import argparse
import calendar
import re
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path

from domain.numeric import dec
from services.symbols import SymbolResolver

START = date(2024, 10, 1)
END = date(2026, 9, 30)

# Folio symbols the demo trades, spelled the way the folio stores them.
SYMBOLS: tuple[str, ...] = (
    # US blue chips
    "AAPL",
    "MSFT",
    "NVDA",
    "META",
    "COST",
    "KO",
    "JNJ",
    "NFLX",
    "XYZ",
    "INTC",
    "O",
    # US ETFs
    "SPY",
    "SCHD",
    "QQQ",
    # Canadian
    "RY.TO",
    "TD.TO",
    "ENB.TO",
    "CNR.TO",
    "XEQT.TO",
    "VFV.TO",
    "REI.UN.TO",
    "DLR.TO",
    "DLR.U.TO",
)
# The USD/CAD rate, under a name no security uses.
USDCAD = "USDCAD"
# Yahoo does not list the USD line of DLR. It holds the same US dollars as the
# CAD line, so its price is the CAD line's converted at the day's rate.
_PRICED_IN_USD_FROM_CAD: dict[str, str] = {"DLR.U.TO": "DLR.TO"}
# Real renames: the old symbol, the new one, and the first day under the new
# name. Yahoo files the whole history under the new symbol, so the snapshot
# holds it there.
RENAMES: tuple[tuple[str, str, date], ...] = (("SQ", "XYZ", date(2025, 1, 21)),)

_CENT = Decimal("0.01")
_RATE_PLACES = Decimal("0.0001")
_SNAPSHOT_START = "# -- SNAPSHOT START "
_SNAPSHOT_END = "# -- SNAPSHOT END "
_LINE_LIMIT = 88


class DemoScenarioError(ValueError):
    """The demo's own data cannot produce a valid folio."""


# -- SNAPSHOT READING ---------------------------------------------------------


def _parse_events(text: str) -> list[tuple[date, Decimal]]:
    """Read a `YYYY-MM-DD:value` list from the snapshot."""
    events: list[tuple[date, Decimal]] = []
    for token in text.split():
        day, _, value = token.partition(":")
        events.append((date.fromisoformat(day), dec(value)))
    return events


@cache
def _closes() -> dict[str, list[Decimal]]:
    """Month-end closes per symbol, oldest first, from `_FIRST_MONTH` on."""
    return {symbol: [dec(v) for v in text.split()] for symbol, text in _CLOSES.items()}


@cache
def _splits() -> dict[str, list[tuple[date, Decimal]]]:
    """Split dates and ratios (new shares per old share) per symbol."""
    return {symbol: _parse_events(text) for symbol, text in _SPLITS.items()}


def _snapshot_symbol(symbol: str) -> str:
    """Name the symbol the snapshot files a security's history under."""
    for old, new, _ in RENAMES:
        if symbol == old:
            return new
    return symbol


def trading_symbol(symbol: str, on: date) -> str:
    """Name the symbol a security traded under on a given day.

    Args:
        symbol: Any name the security has had.
        on: The day in question.

    Returns:
        The old name before its rename took effect, the new one from then on.
    """
    for old, new, effective in RENAMES:
        if symbol in (old, new):
            return old if on < effective else new
    return symbol


def split_factor(symbol: str, on: date) -> Decimal:
    """How many of today's shares one share held on `on` has become.

    Args:
        symbol: Folio symbol.
        on: The day a price or a holding is measured.

    Returns:
        The product of every split ratio dated after `on`, 1 when there is none.
    """
    factor = Decimal(1)
    for day, ratio in _splits().get(_snapshot_symbol(symbol), []):
        if day > on:
            factor *= ratio
    return factor


def splits(symbol: str) -> list[tuple[date, Decimal]]:
    """Every split of a symbol in the snapshot, as (date, new per old)."""
    return list(_splits().get(_snapshot_symbol(symbol), []))


def price_on(symbol: str, on: date) -> Decimal:
    """Price a symbol on a given day, as the market showed it.

    Interpolated between the surrounding month-end closes by how far into the
    month `on` falls, then un-adjusted for any split after it.

    Args:
        symbol: Folio symbol, or `USDCAD` for the exchange rate.
        on: Any day the snapshot covers.

    Returns:
        The price to the cent, or the rate to four places.

    Raises:
        DemoScenarioError: If the snapshot does not cover the symbol or day.
    """
    closes = _closes().get(_snapshot_symbol(symbol))
    first = date.fromisoformat(f"{_FIRST_MONTH}-01")
    index = (on.year - first.year) * 12 + on.month - first.month
    if closes is None or not 0 < index < len(closes):
        msg = f"The demo snapshot has no price for {symbol} on {on}"
        raise DemoScenarioError(msg)
    days_in_month = calendar.monthrange(on.year, on.month)[1]
    before, after = closes[index - 1], closes[index]
    adjusted = before + (after - before) * Decimal(on.day) / Decimal(days_in_month)
    if symbol == USDCAD:
        return adjusted.quantize(_RATE_PLACES)
    return (adjusted * split_factor(symbol, on)).quantize(_CENT)


@cache
def dividends(symbol: str) -> list[tuple[date, Decimal]]:
    """Every dividend a symbol paid inside the window, per share held then.

    Args:
        symbol: Folio symbol.

    Returns:
        (ex-date, amount per share on that date), oldest first.
    """
    events = _parse_events(_DIVIDENDS.get(_snapshot_symbol(symbol), ""))
    return [(day, amount * split_factor(symbol, day)) for day, amount in events]


# -- SNAPSHOT REFRESH ---------------------------------------------------------


def _number(value: float, places: int) -> str:  # pragma: no cover
    """Render a float compactly, without trailing zeros."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _wrapped(name: str, tokens: list[str]) -> list[str]:  # pragma: no cover
    """Render one snapshot entry as ruff-stable, line-limited source."""
    one_line = f'    "{name}": "{" ".join(tokens)}",'
    if len(one_line) <= _LINE_LIMIT:
        return [one_line]
    lines = [f'    "{name}": (']
    room = _LINE_LIMIT - len('        ""')
    current = ""
    for token in tokens:
        candidate = f"{current} {token}" if current else token
        if len(candidate) + 1 > room:
            lines.append(f'        "{current} "')
            current = token
        else:
            current = candidate
    lines.append(f'        "{current}"')
    lines.append("    ),")
    return lines


def _render_mapping(  # pragma: no cover
    name: str,
    entries: dict[str, list[str]],
) -> list[str]:
    """Render a `name: dict[str, str] = {...}` snapshot table."""
    lines = [f"{name}: dict[str, str] = {{"]
    for symbol, tokens in entries.items():
        lines.extend(_wrapped(symbol, tokens))
    lines.append("}")
    return lines


def _window_events(series: object, places: int) -> list[str]:  # pragma: no cover
    """Render dated events inside the demo window as `YYYY-MM-DD:value`."""
    tokens: list[str] = []
    for stamp, value in series.items():  # ty: ignore[unresolved-attribute]
        day = stamp.date()
        if START <= day <= END and value:
            tokens.append(f"{day.isoformat()}:{_number(float(value), places)}")
    return tokens


def refresh_snapshot(target: Path | None = None) -> None:  # pragma: no cover
    """Fetch the snapshot from Yahoo Finance and rewrite it in this file.

    Args:
        target: The file to rewrite, this module unless a test says otherwise.

    Raises:
        DemoScenarioError: If Yahoo does not return a full month of closes for
            every symbol.
    """
    import yfinance as yf  # noqa: PLC0415 - a developer tool, heavy, network only

    target = target or Path(__file__)
    first = date.fromisoformat(f"{_FIRST_MONTH}-01")
    months = (END.year - first.year) * 12 + END.month - first.month + 1
    resolver = SymbolResolver([], {})
    closes: dict[str, list[str]] = {}
    dividend_events: dict[str, list[str]] = {}
    split_events: dict[str, list[str]] = {}

    fetched = [s for s in (*SYMBOLS, USDCAD) if s not in _PRICED_IN_USD_FROM_CAD]
    for symbol in fetched:
        ysymbol = "CAD=X" if symbol == USDCAD else resolver.yahoo_symbol(symbol)
        ticker = yf.Ticker(ysymbol)
        history = ticker.history(
            start=first.isoformat(),
            end=(END + timedelta(days=1)).isoformat(),
            interval="1mo",
            auto_adjust=False,
        )
        if len(history) != months:
            msg = f"{ysymbol}: expected {months} monthly closes, got {len(history)}"
            raise DemoScenarioError(msg)
        places = 6 if symbol == USDCAD else 4
        closes[symbol] = [_number(float(v), places) for v in history["Close"]]
        if symbol == USDCAD:
            continue
        if found := _window_events(ticker.dividends, 6):
            dividend_events[symbol] = found
        if found := _window_events(ticker.splits, 4):
            split_events[symbol] = found

    for symbol, cad_line in _PRICED_IN_USD_FROM_CAD.items():
        closes[symbol] = [
            _number(float(price) / float(rate), 4)
            for price, rate in zip(closes[cad_line], closes[USDCAD], strict=True)
        ]

    lines = [
        f'_SNAPSHOT_AS_OF = "{date.today().isoformat()}"',  # noqa: DTZ011
        f'_FIRST_MONTH = "{_FIRST_MONTH}"',
        *_render_mapping("_CLOSES", closes),
        *_render_mapping("_DIVIDENDS", dividend_events),
        *_render_mapping("_SPLITS", split_events),
    ]
    source = target.read_text(encoding="utf-8")
    block = re.compile(
        rf"(^{re.escape(_SNAPSHOT_START)}[^\n]*\n)(.*?)(^{re.escape(_SNAPSHOT_END)})",
        re.MULTILINE | re.DOTALL,
    )
    body = "\n".join(lines) + "\n"
    target.write_text(
        block.sub(lambda m: f"{m[1]}{body}{m[3]}", source, count=1),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Snapshot written: {len(closes)} series, splits {split_events}")  # noqa: T201


def main() -> None:
    """Run the demo module's developer tools."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch the price snapshot from Yahoo Finance",
    )
    if parser.parse_args().refresh:
        refresh_snapshot()


# -- SNAPSHOT START (generated by `--refresh`; do not edit by hand) -----------
_SNAPSHOT_AS_OF = "2026-10-04"
_FIRST_MONTH = "2024-09"
_CLOSES: dict[str, str] = {
    "AAPL": (
        "233 225.91 237.33 250.42 236 241.84 222.13 212.5 200.85 205.17 207.57 232.14 "
        "254.63 270.37 278.85 271.86 259.48 264.18 253.79 271.35 312.06 289.36 308.91 "
        "316.85 333.02"
    ),
    "MSFT": (
        "430.3 406.35 423.46 421.5 415.06 396.99 375.39 395.26 460.36 497.41 533.5 "
        "506.69 517.95 517.81 492.01 483.62 430.29 392.74 370.17 407.78 450.24 373.02 "
        "464.72 507.29 512.9"
    ),
    "NVDA": (
        "121.44 132.76 138.25 134.29 120.07 124.92 108.38 108.92 135.13 157.99 177.87 "
        "174.18 186.58 202.49 177 186.5 191.13 177.19 174.4 199.57 211.14 200.09 "
        "200.75 220.78 228.38"
    ),
    "META": (
        "572.44 567.58 574.32 585.51 689.18 668.2 576.36 549 647.49 738.09 773.44 "
        "738.7 734.38 648.35 647.95 660.09 716.5 648.18 572.13 611.91 632.51 563.29 "
        "556.71 572.34 725.18"
    ),
    "COST": (
        "886.52 874.18 971.88 916.27 979.88 1048.61 945.78 994.5 1040.1801 989.94 "
        "939.64 943.32 925.63 911.45 913.59 862.34 940.25 1010.79 996.43 1014.53 "
        "956.32 935.47 951.89 943.89 910.34"
    ),
    "KO": (
        "71.86 65.31 64.08 62.26 63.48 71.21 71.62 72.55 72.1 70.75 67.89 68.99 66.32 "
        "68.9 73.12 69.91 74.81 81.56 76.05 78.76 79.01 81.27 87.59 88.67 86.08"
    ),
    "JNJ": (
        "162.06 159.86 155.01 144.62 152.15 165.02 165.84 156.31 155.21 152.75 164.74 "
        "177.17 185.42 188.87 206.92 206.95 227.25 248.43 244.44 229.85 225.33 253.97 "
        "256.35 265.85 264.74"
    ),
    "NFLX": (
        "70.927 75.603 88.681 89.132 97.676 98.056 93.253 113.172 120.723 133.913 "
        "115.94 120.825 119.892 111.886 107.58 93.76 83.49 96.24 96.15 93.61 86.02 "
        "71.4 71.71 81.05 69.58"
    ),
    "XYZ": (
        "67.13 72.32 88.55 84.99 90.82 65.3 54.33 58.47 61.75 67.93 77.26 79.64 72.27 "
        "75.94 66.8 65.09 60.43 63.7 60.18 70.51 75.72 76 81.24 82.02 73.53"
    ),
    "INTC": (
        "23.46 21.52 24.05 20.05 19.43 23.73 22.71 20.1 19.55 22.4 19.8 24.35 33.55 "
        "39.99 40.56 36.9 46.47 45.61 44.13 94.48 114.68 139.63 90.2 89.51 120.23"
    ),
    "O": (
        "63.42 59.37 57.89 53.41 54.64 57.03 58.01 57.86 56.62 57.61 56.13 58.76 60.79 "
        "57.98 57.61 56.37 61.16 67 61.18 64.24 61.28 61.96 63.87 61.28 54.3"
    ),
    "SPY": (
        "573.76 568.64 602.55 586.08 601.82 594.18 559.39 554.54 589.39 617.85 632.08 "
        "645.05 666.18 682.06 683.39 681.92 691.97 685.99 650.34 718.66 756.48 746.77 "
        "747.03 767.05 762.63"
    ),
    "SCHD": (
        "28.1767 28.23 29.53 27.32 27.83 28.54 27.96 25.82 26.17 26.5 26.5 27.92 27.3 "
        "26.75 27.59 27.43 29.82 31.77 30.68 32.07 32.5 31.71 33.47 34.89 32.53"
    ),
    "QQQ": (
        "488.07 483.85 509.74 511.23 522.29 508.17 468.92 475.47 519.11 551.64 565.01 "
        "570.4 600.37 629.07 619.25 614.31 621.87 607.29 577.18 667.74 738.31 736.4 "
        "687.99 716.76 739.77"
    ),
    "RY.TO": (
        "168.8 168.39 176.16 173.32 177.18 170.98 162.1 165.47 173.94 179.47 177.79 "
        "199.58 205.12 205.47 216.14 233.99 226.72 228.07 224.88 244.31 264.44 293.68 "
        "293.41 283.4 279.94"
    ),
    "TD.TO": (
        "85.52 76.97 79.23 76.53 82.91 86.64 86.23 88.09 94.77 100.16 100.92 103.12 "
        "111.28 115.16 117.65 129.36 127.26 132.88 129.92 146.33 157.75 172.44 168.04 "
        "167.67 167.33"
    ),
    "ENB.TO": (
        "54.94 56.24 60.57 61.01 62.85 61.81 63.69 64.47 63.87 61.75 62.75 66.45 70.21 "
        "65.4 67.93 65.68 66.47 72.47 75.41 75.34 75.64 76.91 76.28 70.22 66.15"
    ),
    "CNR.TO": (
        "158.37 150.35 156.34 145.97 151.82 146.68 140.04 133.51 144.26 141.89 129.38 "
        "132.95 131.24 134.49 133.83 135.75 130.99 153.07 143.18 152.57 163.25 169.25 "
        "178.25 173.05 169.4"
    ),
    "XEQT.TO": (
        "32.5 32.72 34.47 33.68 35.12 34.92 33.65 32.81 34.6 35.47 36.28 37.3 38.95 "
        "39.83 40.34 39.89 40.65 42.04 40.06 42.38 44.495 45.1 44.8 45.63 45.57"
    ),
    "VFV.TO": (
        "138.05 140.91 150.36 149.99 155.78 153 143.03 136.13 143.95 149.87 155.91 "
        "157.46 164.99 170.28 170.08 166.62 167.64 166.62 160.99 173.81 185.61 188.37 "
        "186.29 189.16 193.12"
    ),
    "REI.UN.TO": (
        "20.38 19 19.01 18.28 18.48 19.39 17.15 17.22 17.34 17.71 17.65 18.48 18.95 "
        "18.77 18.94 18.7 19.51 19.73 18.99 21.26 22.19 22.69 22.18 20.84 20.73"
    ),
    "DLR.TO": (
        "13.74 14.2 14.31 14.58 14.78 14.75 14.65 14.07 14.05 13.88 14.15 14.09 14.15 "
        "14.3 14.29 13.92 13.83 13.88 14.09 13.79 14.03 14.32 14.18 14.05 14.36"
    ),
    "USDCAD": (
        "1.35088 1.3908 1.4014 1.43498 1.4493 1.44435 1.43091 1.38268 1.3739 1.36814 "
        "1.38252 1.3742 1.39162 1.39811 1.3982 1.36947 1.3622 1.36758 1.39283 1.36713 "
        "1.3798 1.4209 1.40112 1.38991 1.41913"
    ),
    "DLR.U.TO": (
        "10.1711 10.21 10.2112 10.1604 10.198 10.2122 10.2382 10.1759 10.2264 10.1452 "
        "10.2349 10.2532 10.168 10.2281 10.2203 10.1645 10.1527 10.1493 10.1161 "
        "10.0868 10.1681 10.0781 10.1205 10.1086 10.1189"
    ),
}
_DIVIDENDS: dict[str, str] = {
    "AAPL": (
        "2024-11-08:0.25 2025-02-10:0.25 2025-05-12:0.26 2025-08-11:0.26 "
        "2025-11-10:0.26 2026-02-09:0.26 2026-05-11:0.27 2026-08-10:0.27"
    ),
    "MSFT": (
        "2024-11-21:0.83 2025-02-20:0.83 2025-05-15:0.83 2025-08-21:0.83 "
        "2025-11-20:0.91 2026-02-19:0.91 2026-05-21:0.91 2026-08-20:0.91"
    ),
    "NVDA": (
        "2024-12-05:0.01 2025-03-12:0.01 2025-06-11:0.01 2025-09-11:0.01 "
        "2025-12-04:0.01 2026-03-11:0.01 2026-06-04:0.25 2026-09-10:0.25"
    ),
    "META": (
        "2024-12-16:0.5 2025-03-14:0.525 2025-06-16:0.525 2025-09-22:0.525 "
        "2025-12-15:0.525 2026-03-16:0.525 2026-06-15:0.525 2026-09-21:0.525"
    ),
    "COST": (
        "2024-11-01:1.16 2025-02-07:1.16 2025-05-02:1.3 2025-08-01:1.3 2025-10-31:1.3 "
        "2026-01-30:1.3 2026-05-01:1.47 2026-07-24:1.47"
    ),
    "KO": (
        "2024-11-29:0.485 2025-03-14:0.51 2025-06-13:0.51 2025-09-15:0.51 "
        "2025-12-01:0.51 2026-03-13:0.53 2026-06-15:0.53 2026-09-15:0.53"
    ),
    "JNJ": (
        "2024-11-26:1.24 2025-02-18:1.24 2025-05-27:1.3 2025-08-26:1.3 2025-11-25:1.3 "
        "2026-02-24:1.3 2026-05-26:1.34 2026-08-25:1.34"
    ),
    "O": (
        "2024-10-01:0.264 2024-11-01:0.264 2024-12-02:0.264 2025-01-02:0.264 "
        "2025-02-03:0.264 2025-03-03:0.268 2025-04-01:0.269 2025-05-01:0.269 "
        "2025-06-02:0.269 2025-07-01:0.269 2025-08-01:0.269 2025-09-02:0.269 "
        "2025-10-01:0.27 2025-10-31:0.27 2025-11-28:0.27 2025-12-31:0.27 "
        "2026-01-30:0.27 2026-02-27:0.27 2026-03-31:0.271 2026-04-30:0.271 "
        "2026-05-29:0.271 2026-06-30:0.271 2026-07-31:0.271 2026-08-31:0.271 "
        "2026-09-30:0.272"
    ),
    "SPY": (
        "2024-12-20:1.966 2025-03-21:1.696 2025-06-20:1.761 2025-09-19:1.831 "
        "2025-12-19:1.993 2026-03-20:1.797 2026-06-18:1.904 2026-09-18:1.889"
    ),
    "SCHD": (
        "2024-12-11:0.265 2025-03-26:0.249 2025-06-25:0.26 2025-09-24:0.26 "
        "2025-12-10:0.278 2026-03-25:0.257 2026-06-24:0.253 2026-09-23:0.267"
    ),
    "QQQ": (
        "2024-12-23:0.835 2025-03-24:0.716 2025-06-23:0.591 2025-09-22:0.694 "
        "2025-12-22:0.794 2026-03-23:0.733 2026-06-22:0.813 2026-09-21:0.751"
    ),
    "RY.TO": (
        "2024-10-24:1.42 2025-01-27:1.48 2025-04-24:1.48 2025-07-24:1.54 "
        "2025-10-27:1.54 2026-01-26:1.64 2026-04-23:1.64 2026-07-27:1.76"
    ),
    "TD.TO": (
        "2024-10-10:1.02 2025-01-10:1.05 2025-04-10:1.05 2025-07-10:1.05 "
        "2025-10-10:1.05 2026-01-09:1.08 2026-04-09:1.08 2026-07-10:1.12"
    ),
    "ENB.TO": (
        "2024-11-15:0.915 2025-02-14:0.943 2025-05-15:0.943 2025-08-15:0.943 "
        "2025-11-14:0.943 2026-02-17:0.97 2026-05-15:0.97 2026-08-14:0.97"
    ),
    "CNR.TO": (
        "2024-12-09:0.845 2025-03-10:0.888 2025-06-09:0.888 2025-09-08:0.888 "
        "2025-12-09:0.888 2026-03-10:0.915 2026-06-09:0.915 2026-09-08:0.915"
    ),
    "XEQT.TO": (
        "2024-12-30:0.275 2025-03-26:0.09 2025-06-25:0.267 2025-09-24:0.1 "
        "2025-12-30:0.205 2026-03-26:0.091 2026-06-25:0.322 2026-09-24:0.102"
    ),
    "VFV.TO": (
        "2024-12-30:0.4 2025-03-27:0.398 2025-06-30:0.369 2025-09-29:0.367 "
        "2025-12-30:0.393 2026-03-27:0.424 2026-06-26:0.395 2026-09-28:0.408"
    ),
    "REI.UN.TO": (
        "2024-10-31:0.0925 2024-11-29:0.0925 2024-12-31:0.0925 2025-01-31:0.0925 "
        "2025-02-28:0.0965 2025-03-31:0.0965 2025-04-30:0.0965 2025-05-30:0.0965 "
        "2025-06-30:0.0965 2025-07-31:0.0965 2025-08-29:0.0965 2025-09-29:0.0965 "
        "2025-10-31:0.0965 2025-11-28:0.0965 2025-12-31:0.0965 2026-01-30:0.0965 "
        "2026-02-27:0.0965 2026-03-31:0.0965 2026-04-30:0.0965 2026-05-29:0.0965 "
        "2026-06-30:0.0965 2026-07-31:0.0965 2026-08-31:0.0965 2026-09-29:0.0965"
    ),
    "DLR.TO": (
        "2024-12-31:0.161 2025-03-31:0.047 2025-06-30:0.109 2025-09-29:0.153 "
        "2025-12-31:0.154 2026-03-31:0.073 2026-06-30:0.146 2026-09-29:0.109"
    ),
}
_SPLITS: dict[str, str] = {
    "NFLX": "2025-11-17:10",
    "SCHD": "2024-10-11:3",
}
# -- SNAPSHOT END -------------------------------------------------------------


# The snapshot above has to exist before the tools run.
if __name__ == "__main__":
    main()
