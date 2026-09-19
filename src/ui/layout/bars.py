"""Text bars: a proportion drawn in characters, for a table cell or a panel row."""

from __future__ import annotations

from decimal import Decimal

# Left-aligned eighth blocks, from an empty cell up to (but not including) a full one.
_EIGHTHS = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")
_FULL_BLOCK = "█"
_ASCII_BLOCK = "#"


def _clamp(fraction: Decimal) -> Decimal:
    """Keep a fraction within the bar."""
    return min(max(fraction, Decimal(0)), Decimal(1))


def hbar(fraction: Decimal, width: int, *, unicode: bool = True) -> str:
    """Draw a solid bar filling `fraction` of `width` cells.

    Args:
        fraction: How full the bar is, clamped to 0..1.
        width: Cells a full bar spans.
        unicode: Whether the terminal can draw block elements.

    Returns:
        The bar, at most `width` characters long.
    """
    cells = _clamp(fraction) * width
    if not unicode:
        return _ASCII_BLOCK * int(cells.to_integral_value())
    eighths = int((cells * len(_EIGHTHS)).to_integral_value())
    whole, part = divmod(eighths, len(_EIGHTHS))
    return _FULL_BLOCK * whole + _EIGHTHS[part]


def meter(fraction: Decimal, width: int, glyphs: tuple[str, str]) -> str:
    """Draw a gauge exactly `width` cells wide, filled to `fraction`.

    Args:
        fraction: How full the gauge is, clamped to 0..1.
        width: Cells the gauge spans, filled or not.
        glyphs: The filled and the empty cell.

    Returns:
        The gauge, always `width` characters long. A partly filled cell rounds
        down, so the gauge reads full only once the fraction truly is.
    """
    filled_glyph, empty_glyph = glyphs
    filled = int(_clamp(fraction) * width)
    return filled_glyph * filled + empty_glyph * (width - filled)
