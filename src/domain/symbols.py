"""What a symbol's spelling says about the security it names."""

from __future__ import annotations

import re

# A US dollar line of a Toronto-style listing: `DLR.U.TO` beside `DLR.TO`.
_USD_LINE = re.compile(r"^(?P<base>.+)\.U\.(?P<exchange>[A-Z]+)$")


def cad_twin(symbol: str) -> str | None:
    """Name the Canadian dollar line a US dollar line mirrors, or None for any other.

    `DLR.U.TO` and `DLR.TO` are one interlisted security in two currencies, so a
    close of one converts to the other at that day's rate.
    """
    found = _USD_LINE.match(symbol)
    return None if found is None else f"{found['base']}.{found['exchange']}"
