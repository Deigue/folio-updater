"""Rendering single cells: money, prices, percentages, colour by sign.

Shared by every view that prints figures, so a number reads the same in the
dashboard, the ticker view and anywhere else.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from domain import COMPACT_RANGES, SIGNED, TORONTO_TZ, Fmt, Measure
from domain.numeric import q2
from exporters.catalog import shows
from exporters.sheet_spec import render
from term import supports_unicode
from ui.vocabulary import (
    ACCOUNT_TYPE_COLORS,
    CURRENCY_COLORS,
    DAY_MOVE_STRONG_AT,
    FLAT,
    FLAT_BELOW,
    GAIN,
    GAIN_STRONG,
    INCOME,
    LOSS,
    LOSS_STRONG,
    MONEY_PRECISION,
    PRICE_PRECISION,
    REFERENCE_STYLE,
    RETURN_STRONG_AT,
    SHORT_ACCOUNT_TYPES,
    UNIT_PRECISION,
    WEIGHT_ALERT,
    WEIGHT_ALERT_AT,
    WEIGHT_WARN,
    WEIGHT_WARN_AT,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from domain import AccountType, Currency
    from engine.holdings import Holding
    from exporters.sheet_spec import Col

UNICODE = supports_unicode()
WARN_GLYPH = "⚠" if UNICODE else "!"
EM_DASH = "—" if UNICODE else "-"
DELTA_GLYPH = "Δ" if UNICODE else "+/-"
RISE, FALL = ("▲", "▼") if UNICODE else ("^", "v")
TREE_BRANCH = "└" if UNICODE else "`-"

PERCENT_PRECISION = 2

_HALF_DAY = 12  # hours on a twelve-hour clock


def display_round(number: float, precision: int) -> float:
    """Round a figure to the decimals a cell shows, collapsing `-0.0` to `0.0`."""
    return round(number, precision) + 0.0


def money(
    value: Decimal | None,
    *,
    blank_zero: bool = False,
    plus: bool = False,
) -> str:
    """Render a money figure, leaving a genuine blank blank.

    Rounded to cents *before* the sign is read, so an FX residue of a
    millionth of a cent prints as `0.00` rather than the alarming `-0.00`.
    `plus` marks a gain with a leading `+`, for a change.
    """
    if value is None:
        return EM_DASH
    number = float(q2(value)) + 0.0  # collapses -0.0, which reads as a real debit
    if blank_zero and number == 0:
        return ""
    flag = "+" if plus and number > 0 else ""
    return f"{flag}{number:,.{MONEY_PRECISION}f}"


def price(value: Decimal | None) -> str:
    """Render a per-unit price at its own, finer precision."""
    if value is None:
        return EM_DASH
    number = display_round(float(value), PRICE_PRECISION)
    return f"{number:,.{PRICE_PRECISION}f}".rstrip("0").rstrip(".")


def units(value: Decimal | None) -> str:
    """Render a unit count, dropping the zeros whole units do not need."""
    if value is None:
        return EM_DASH
    text = f"{display_round(float(value), UNIT_PRECISION):,.{UNIT_PRECISION}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def percent(value: Decimal | None, *, plus: bool = False) -> str:
    """Render a ratio as a percentage, with a leading `+` on a gain if asked."""
    if value is None:
        return EM_DASH
    flag = "+" if plus else ""
    number = display_round(float(value) * 100, PERCENT_PRECISION)
    return f"{number:{flag},.{PERCENT_PRECISION}f}%"


def style(text: str, style: str | None) -> str:
    """Wrap a rendered cell in a style, leaving blanks and placeholders bare.

    A blank must stay truly blank, since `fit` drops a column no row fills in.
    """
    if not text or not style or text == EM_DASH:
        return text
    return f"[{style}]{text}[/]"


def signed(text: str, value: Decimal | None) -> str:
    """Colour a rendered cell by the sign of the number behind it.

    Green up, red down, matching every other table in the app.
    """
    if value is None or value == 0:
        return text
    return style(text, GAIN if value > 0 else LOSS)


def graded(
    text: str,
    ratio: Decimal | None,
    strong_at: Decimal,
    sign: Decimal | None = None,
) -> str:
    """Colour a percentage by its sign and how large it is.

    A move too small to matter is dimmed, an ordinary one takes the plain sign
    colour, and one at or beyond `strong_at` is drawn bold and bright.

    Args:
        text: The rendered percentage.
        ratio: The ratio behind it, which decides the intensity.
        strong_at: The magnitude at which the colour turns strong.
        sign: What decides up or down, when that is not the ratio itself.

    Returns:
        The cell, marked up.
    """
    if ratio is not None and abs(ratio) < FLAT_BELOW:
        return style(text, FLAT)
    sign = ratio if sign is None else sign
    if ratio is None or sign is None or sign == 0:
        return text
    strong = abs(ratio) >= strong_at
    if sign > 0:
        return style(text, GAIN_STRONG if strong else GAIN)
    return style(text, LOSS_STRONG if strong else LOSS)


def day_move(value: Decimal | None, sign: Decimal | None = None) -> str:
    """Render a one-day percentage move, graded by its size."""
    return graded(percent(value), value, DAY_MOVE_STRONG_AT, sign)


def lifetime_return(value: Decimal | None, sign: Decimal | None = None) -> str:
    """Render a lifetime percentage return, graded by its size."""
    return graded(percent(value), value, RETURN_STRONG_AT, sign)


def income(value: Decimal) -> str:
    """Render dividend income in its own colour, blanking a zero."""
    return style(money(value, blank_zero=True), INCOME)


def currency_tint(currency: Currency, text: str) -> str:
    """Tint a currency label with that currency's colour."""
    return style(text, CURRENCY_COLORS.get(currency, "dim"))


def account_type_cell(account_type: AccountType) -> str:
    """Name an account type in four letters at most, in the type's colour."""
    short = SHORT_ACCOUNT_TYPES.get(account_type, str(account_type))
    return style(short, ACCOUNT_TYPE_COLORS.get(account_type))


def weight(value: Decimal | None) -> str:
    """Render a holding's weight, flagged once it concentrates its pool."""
    text = percent(value)
    if value is None:
        return text
    if value >= WEIGHT_ALERT_AT:
        return style(text, WEIGHT_ALERT)
    if value >= WEIGHT_WARN_AT:
        return style(text, WEIGHT_WARN)
    return text


def long_date(iso: str | None) -> str | None:
    """Render a `YYYY-MM-DD` date the way a reader says it: `Oct 28, 2026`."""
    if not iso:
        return None
    day = date.fromisoformat(iso[:10])
    return f"{day:%b} {day.day}, {day.year}"


def short_date(day: date) -> str:
    """Render a recent date without its year: `Oct 28`."""
    return f"{day:%b} {day.day}"


def clock_time(iso: str) -> str:
    """Render a UTC timestamp as Toronto wall-clock time: `1:59 PM`."""
    moment = datetime.fromisoformat(iso).astimezone(TORONTO_TZ)
    afternoon, hour = divmod(moment.hour, _HALF_DAY)
    return f"{hour or _HALF_DAY}:{moment.minute:02d} {'PM' if afternoon else 'AM'}"


# The formats a terminal reads exactly as their text, with no sign or grading.
_AS_TEXT = frozenset({Fmt.TEXT, Fmt.DATE, Fmt.RATIO, Fmt.TALLY, *COMPACT_RANGES})


def figure_cell(column: Col, value: object, *, closed: bool = False) -> str:
    """Render one catalog column's value the way its format and measure read.

    `closed` marks a closed holding's row, where a zero is what it never earned
    and is left blank.
    """
    if column.fmt in _AS_TEXT:
        return _as_text(column.fmt, value, quiet=column.quiet)
    number: Decimal | None = value  # ty: ignore[invalid-assignment]
    graded = _GRADED.get(column.measure) if column.measure else None
    if graded:
        return graded(number)
    if column.measure is Measure.INCOME and number is not None:
        return income(number)
    text = _figure_text(column.fmt, number, blank_zero=column.quiet or closed)
    if column.fmt in SIGNED:
        text = signed(text, number)
    return style(text, REFERENCE_STYLE) if column.measure is Measure.REFERENCE else text


def _as_text(fmt: Fmt, value: object, *, quiet: bool = False) -> str:
    """Render text, a date, a ratio, a tally or a compact figure, blank when missing."""
    if value is None:
        return ""
    if fmt is Fmt.TEXT:
        return str(value)
    if fmt is Fmt.DATE:
        return long_date(str(value)) or ""
    return render(fmt, value, quiet=quiet)


def _figure_text(fmt: Fmt, number: Decimal | None, *, blank_zero: bool) -> str:
    """Render a number at its format's own precision, unstyled."""
    if fmt is Fmt.UNITS:
        return units(number)
    if fmt in {Fmt.PRICE, Fmt.PRICE_SIGNED}:
        return price(number)
    if fmt in {Fmt.PERCENT, Fmt.PERCENT_SIGNED}:
        return percent(number)
    return money(number, blank_zero=blank_zero)


# The measures graded by their own size, each by its own scale.
_GRADED: dict[Measure, Callable[[Decimal | None], str]] = {
    Measure.DAY_MOVE: day_move,
    Measure.RETURN: lifetime_return,
    Measure.WEIGHT: weight,
}


def holding_cell(column: Col, holding: Holding) -> str:
    """Render one catalog column for a holding, blank where a closed one has nothing."""
    if not shows(column, holding):
        return ""
    return figure_cell(column, column.read(holding), closed=holding.closed)
