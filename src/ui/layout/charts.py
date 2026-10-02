"""Small area charts drawn in block characters, for a terminal panel."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

# A cell filled from the floor in eighths, empty to full.
_EIGHTHS = " ▁▂▃▄▅▆▇█"
_ASCII_FULL, _ASCII_PART = "#", "."
_GUIDE, _ASCII_GUIDE = "┈", "-"
_STEPS = 8


@dataclass(frozen=True)
class ChartStyle:
    """The colours a chart is drawn in.

    Attributes:
        up: A bar above the baseline.
        down: A bar below it.
        level: A bar exactly on it.
        guide: The dotted baseline, where no bar covers it.
    """

    up: str
    down: str
    level: str
    guide: str


def area_chart(
    values: Sequence[Decimal],
    baseline: Decimal,
    *,
    width: int,
    height: int,
    style: ChartStyle,
    fill: Decimal = Decimal(1),
    unicode: bool = True,
) -> list[Text]:
    """Draw a series as an area chart, a dotted line marking the baseline.

    Args:
        values: The series, oldest first.
        baseline: The level bars are coloured against, drawn as a dotted line.
        width: Columns the chart spans.
        height: Rows the chart spans.
        style: The colours.
        fill: How much of the width the series covers, from the left. The rest
            stays empty but for the baseline, like a session still trading.
        unicode: Whether the terminal can draw block elements.

    Returns:
        The chart's rows, top first, each exactly `width` cells wide.
    """
    drawn = max(1, min(width, round(width * fill)))
    columns = _resample(values, drawn) if values else []
    low = min([*columns, baseline])
    high = max([*columns, baseline])
    span = (high - low) or Decimal(1)
    levels = [
        max(1, round((value - low) / span * height * _STEPS)) for value in columns
    ]
    guide_row = height - 1 - min(height - 1, int((baseline - low) / span * height))
    guide = _GUIDE if unicode else _ASCII_GUIDE

    rows: list[Text] = []
    for row in range(height):
        floor = (height - 1 - row) * _STEPS
        empty = guide if row == guide_row else " "
        line = Text()
        for value, level in zip(columns, levels, strict=True):
            part = level - floor
            if part <= 0:
                line.append(empty, style=style.guide)
                continue
            tint = (
                style.up
                if value > baseline
                else style.down
                if value < baseline
                else style.level
            )
            line.append(_cell(part, unicode=unicode), style=tint)
        line.append(empty * (width - len(columns)), style=style.guide)
        rows.append(line)
    return rows


def _cell(part: int, *, unicode: bool) -> str:
    """Draw one cell filled `part` eighths from the floor."""
    if part >= _STEPS:
        return _EIGHTHS[-1] if unicode else _ASCII_FULL
    return _EIGHTHS[part] if unicode else _ASCII_PART


def _resample(values: Sequence[Decimal], count: int) -> list[Decimal]:
    """Fit a series to `count` columns, keeping its first and last value.

    A longer series is sampled at even steps. A shorter one is stretched, each
    value held across the columns it covers, so a sparse range still spans its
    width.
    """
    size = len(values)
    if size == count:
        return list(values)
    if size > count:
        if count == 1:
            return [values[-1]]
        return [values[round(step * (size - 1) / (count - 1))] for step in range(count)]
    return [values[min(size - 1, step * size // count)] for step in range(count)]
