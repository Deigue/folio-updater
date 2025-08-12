"""
Schema utilities to use with the database and Excel files.
"""

import logging
import re
from src.constants import TXN_ESSENTIALS
from src import config

logger = logging.getLogger(__name__)

def _normalize(name: str) -> str:
    name = name.strip().lower()
    if not name:
        return name
    return re.sub(r"[^a-z0-9$]", "", name)

