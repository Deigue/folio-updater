"""Tests for shared CLI command helpers."""

from __future__ import annotations

from unittest.mock import patch

from cli.commands import common as common_cmd


def test_fx_coverage_is_skipped_when_config_turns_it_off() -> None:
    with (
        patch.object(common_cmd, "get_config") as config,
        patch.object(common_cmd.ForexService, "ensure_coverage") as ensure,
    ):
        config.return_value.auto_getfx = False
        common_cmd.ensure_fx_coverage()
    ensure.assert_not_called()


def test_fx_coverage_is_skipped_for_an_empty_folio() -> None:
    with (
        patch.object(common_cmd, "get_config") as config,
        patch.object(
            common_cmd.ForexService,
            "get_earliest_transaction_date",
            return_value=None,
        ),
        patch.object(common_cmd.ForexService, "ensure_coverage") as ensure,
    ):
        config.return_value.auto_getfx = True
        common_cmd.ensure_fx_coverage()
    ensure.assert_not_called()


def test_fx_coverage_through_today_drops_the_settle_date_bound() -> None:
    """A dashboard prices at today's market, so it needs today's rate.

    Passing no end at all lets `ensure_coverage` reach for everything the Bank
    of Canada has published, instead of stopping at the last transaction.
    """
    with (
        patch.object(common_cmd, "get_config") as config,
        patch.object(
            common_cmd.ForexService,
            "get_earliest_transaction_date",
            return_value="2025-01-02",
        ),
        patch.object(common_cmd, "get_max_value") as latest,
        patch.object(common_cmd.ForexService, "ensure_coverage") as ensure,
    ):
        config.return_value.auto_getfx = True
        common_cmd.ensure_fx_coverage(through_today=True)

    ensure.assert_called_once_with("2025-01-02", None)
    # The settle date is not even read, so the folio is not opened for it.
    latest.assert_not_called()
