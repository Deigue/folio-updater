"""Tests for excel_importer module."""

import logging
from contextlib import _GeneratorContextManager
from typing import Callable

from app.app_context import AppContext
from importers.excel_importer import import_transactions
from mock.folio_setup import ensure_folio_exists

logger = logging.getLogger(__name__)


def test_import_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        ensure_folio_exists()
        config = ctx.config
        transactions: int = import_transactions(config.folio_path)
        logger.info("%d transactions imported.", transactions)
        assert transactions > 0
