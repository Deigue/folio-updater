"""Where commands get the brokers they download from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app import get_config
from services.ibkr_broker import IbkrBroker
from services.wealthsimple_broker import WealthsimpleBroker

if TYPE_CHECKING:
    from collections.abc import Callable

    from config import Config
    from services.broker import Broker, StatementBroker


@dataclass(frozen=True)
class Brokers:
    """Every broker folio downloads from."""

    ibkr: Broker
    wealthsimple: StatementBroker


def _configured(config: Config) -> Brokers:
    """Build each broker from its settings."""
    return Brokers(
        ibkr=IbkrBroker(config.ibkr),
        wealthsimple=WealthsimpleBroker(config.wealthsimple),
    )


class BrokerRegistry:
    """Hands out the brokers; a test swaps `factory` to download from fakes."""

    factory: ClassVar[Callable[[Config], Brokers]] = _configured

    @classmethod
    def load(cls) -> Brokers:
        """Return the brokers, built from the current settings."""
        return cls.factory(get_config())
