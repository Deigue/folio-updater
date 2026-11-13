"""Each broker's settings, read once from the `brokers` block."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class IbkrSettings:
    """What folio downloads from IBKR.

    Attributes:
        queries: Flex Query ID by query name; a name with Activity is cash activity.
    """

    queries: Mapping[str, str]


@dataclass(frozen=True)
class WealthsimpleSettings:
    """How folio signs in to Wealthsimple and what it leaves out.

    Attributes:
        user_agent: Browser identity sent to Wealthsimple.
        exclude_accounts: Account descriptions never downloaded.
    """

    user_agent: str
    exclude_accounts: tuple[str, ...]
