"""The `Revisions` table: a change token for each watched table."""

from __future__ import annotations

from db.queries import get_connection, get_revisions


def tokens() -> dict[str, str]:
    """Return the change token of every watched table that exists."""
    with get_connection() as conn:
        return get_revisions(conn)
