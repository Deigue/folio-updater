"""Account model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from models.base import (
    from_datetime,
    from_list,
    from_none,
    from_str,
    to_class,
    to_enum,
)
from utils.constants import Currency


@dataclass
class Account:
    """Wealthsimple account data.

    Attributes:
        id (str): The unique identifier for the account.

    """

    id: str
