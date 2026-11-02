"""Value-driven shading shared by dashboard, ticker and cost-base cells."""

from decimal import Decimal

import pytest
from rich.color import Color
from rich.text import Text

from ui.cells import EM_DASH, day_move, graded, lifetime_return
from ui.vocabulary import DAY_MOVE_STRONG_AT, FLAT_BELOW, RETURN_STRONG_AT


@pytest.mark.parametrize("threshold", [DAY_MOVE_STRONG_AT, RETURN_STRONG_AT])
@pytest.mark.parametrize(
    ("sign", "colors"),
    [
        (1, ["#4da767", "#70b980", "#91cba0", "#b2ddbf", "#d3efdf", "#effff5"]),
        (-1, ["#e27777", "#e88f8f", "#eea7a7", "#f4bfbf", "#fad7d7", "#ffefef"]),
    ],
)
def test_shading_progresses_through_six_steps(
    threshold: Decimal,
    sign: int,
    colors: list[str],
) -> None:
    for fraction, color in zip(
        ("0.1", "0.2", "0.4", "0.6", "0.8", "1"),
        colors,
        strict=True,
    ):
        ratio = Decimal(fraction) * threshold * sign
        assert graded("value", ratio, threshold) == f"[{color}]value[/]"
    assert graded("value", threshold * sign * 2, threshold) == f"[{colors[-1]}]value[/]"


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("background", ["#1e1e1e", "#2d2d2d"])
def test_every_shade_is_readable_and_gets_brighter(sign: int, background: str) -> None:
    previous = 0.0
    for fraction in ("0.1", "0.2", "0.4", "0.6", "0.8", "1"):
        markup = graded("value", Decimal(fraction) * sign, Decimal(1))
        color = str(Text.from_markup(markup).spans[0].style)
        luminance = _luminance(color)
        assert luminance > previous
        assert (luminance + 0.05) / (_luminance(background) + 0.05) >= 4.5
        previous = luminance


def _luminance(color: str) -> float:
    """Use WCAG's sRGB relative luminance for the contrast regression."""
    rgb = Color.parse(color).get_truecolor()
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in (channel / 255 for channel in rgb)
    ]
    return sum(
        c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def test_flat_missing_and_overridden_sign_behavior_is_preserved() -> None:
    assert graded("0%", FLAT_BELOW / 2, DAY_MOVE_STRONG_AT) == "[dim]0%[/]"
    assert graded("missing", None, DAY_MOVE_STRONG_AT) == "missing"
    assert graded("", DAY_MOVE_STRONG_AT, DAY_MOVE_STRONG_AT) == ""
    assert graded(EM_DASH, DAY_MOVE_STRONG_AT, DAY_MOVE_STRONG_AT) == EM_DASH
    assert graded("gain", -DAY_MOVE_STRONG_AT, DAY_MOVE_STRONG_AT, Decimal(1)) == (
        "[#effff5]gain[/]"
    )
    assert graded("zero", DAY_MOVE_STRONG_AT, DAY_MOVE_STRONG_AT, Decimal(0)) == "zero"
    assert day_move(DAY_MOVE_STRONG_AT) == "[#effff5]3.00%[/]"
    assert lifetime_return(RETURN_STRONG_AT) == "[#effff5]25.00%[/]"
