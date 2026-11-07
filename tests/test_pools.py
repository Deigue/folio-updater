"""Tests for a pool's identity and the figures it is valued with."""

from __future__ import annotations

import pytest

from engine.pools import FOLIO, Pool, account_pool, type_pool


@pytest.mark.parametrize(
    ("pool", "label"),
    [
        (account_pool("WS-TFSA"), "WS-TFSA"),
        (type_pool("NON_REGISTERED"), "NON-REGISTERED"),
        (FOLIO, "Portfolio"),
    ],
    ids=["account", "type", "folio"],
)
def test_a_pool_names_itself_from_its_grain(pool: Pool, label: str) -> None:
    assert pool.label == label
