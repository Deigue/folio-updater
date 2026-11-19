"""How a figure reads: its format, its kind, its precision and how it shortens."""

from enum import StrEnum


class Fmt(StrEnum):
    """How one column's values should read."""

    TEXT = "text"
    ID = "id"
    DATE = "date"
    MONEY = "money"
    MONEY_SIGNED = "money_signed"
    PRICE = "price"
    PRICE_SIGNED = "price_signed"
    UNITS = "units"
    PERCENT = "percent"
    PERCENT_SIGNED = "percent_signed"
    RATE = "rate"
    RATIO = "ratio"  # a plain ratio at two places, such as a P/E or a beta
    LARGE = "large"  # an amount too large to read whole: 3.81T, 35.16B, 512.40M
    COUNT = "count"  # a count read by magnitude: 27.01M, 168.06K
    # Text a terminal table shortens in its own way when it runs out of room.
    ACCOUNT = "account"
    ACTION = "action"
    CURRENCY = "currency"


class Measure(StrEnum):
    """What kind of figure a column holds: performance, reference, income or weight."""

    REFERENCE = "reference"  # a cost or size fact rather than performance
    DAY_MOVE = "day_move"  # graded by how far a day moves
    RETURN = "return"  # graded by how far a lifetime return runs
    WEIGHT = "weight"  # flagged once it concentrates its pool
    INCOME = "income"


# Decimals a reader should see, per format. A workbook displays a value at this
# precision while the cell keeps every digit; a CSV, having nowhere to hide the
# rest, rounds to it.
DECIMALS: dict[Fmt, int] = {
    Fmt.ID: 0,
    Fmt.MONEY: 2,
    Fmt.MONEY_SIGNED: 2,
    Fmt.PRICE: 4,
    Fmt.PRICE_SIGNED: 4,
    Fmt.UNITS: 6,
    Fmt.PERCENT: 2,
    Fmt.PERCENT_SIGNED: 2,
    Fmt.RATE: 4,
    Fmt.RATIO: 2,
    Fmt.LARGE: 2,
    Fmt.COUNT: 2,
}

# How a large figure is abbreviated wherever it is: each `(size, suffix)`,
# largest first. A figure takes the largest size it reaches.
MAGNITUDES: tuple[tuple[int, str], ...] = (
    (10**12, "T"),
    (10**9, "B"),
    (10**6, "M"),
    (10**3, "K"),
)

# The smallest and largest size each compact format abbreviates by: a market
# cap never drops to thousands, and a volume never climbs past millions. Excel's
# own number formats in `excel_style` say the same thing for the workbook, and
# they hold only three sizes each.
COMPACT_RANGES: dict[Fmt, tuple[int, int]] = {
    Fmt.LARGE: (10**6, 10**12),
    Fmt.COUNT: (1, 10**6),
}

# The formats whose values are numbers rather than text.
NUMERIC: frozenset[Fmt] = frozenset(DECIMALS)

# The formats whose digits are never grouped: an identifier or a year, which a
# reader types back in (`folio query`, `folio edit`) and which the workbook
# writes as plain digits too.
UNGROUPED: frozenset[Fmt] = frozenset({Fmt.ID})

# The formats rendered as a percentage of a ratio.
PERCENTS: frozenset[Fmt] = frozenset({Fmt.PERCENT, Fmt.PERCENT_SIGNED})

# The formats whose sign carries meaning, and is worth colouring.
SIGNED: frozenset[Fmt] = frozenset(
    {Fmt.MONEY_SIGNED, Fmt.PRICE_SIGNED, Fmt.PERCENT_SIGNED},
)
