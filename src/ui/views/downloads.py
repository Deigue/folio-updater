"""Rendering what a broker download retrieved."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ui.vocabulary import DOWNLOAD_DROP_ORDER
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from collections.abc import Sequence

    from models.wealthsimple import Account, ActivityFeedItem

PREVIEW_ROWS = 10
DESCRIPTION_WIDTH = 30


def show_activities_preview(
    activities: Sequence[ActivityFeedItem],
    accounts: Sequence[Account],
) -> None:
    """Show the first few Wealthsimple activities a download retrieved.

    Args:
        activities: Everything retrieved, newest first.
        accounts: The accounts they came from, to name each activity's account.
    """
    names = {
        account.id: account.nickname or account.account_type or account.id
        for account in accounts
    }
    show_data_table(
        [
            {
                "Date": activity.occurred_at.strftime("%Y-%m-%d"),
                "Action": activity.type,
                "Ticker": activity.asset_symbol or "",
                "Amount": float(activity.amount) if activity.amount else 0,
                "Currency": activity.currency,
                "Account": names.get(activity.account_id or "")
                or (activity.account_id[:8] + "..." if activity.account_id else ""),
                "Description": _shorten(activity.description),
            }
            for activity in activities[:PREVIEW_ROWS]
        ],
        title=f"Downloaded Transactions Preview ({len(activities)} total)",
        max_rows=PREVIEW_ROWS,
        drop_order=DOWNLOAD_DROP_ORDER,
    )


def _shorten(description: str | None) -> str:
    """Cut a description to the width the preview has room for."""
    if description is None:
        return ""
    if len(description) > DESCRIPTION_WIDTH:
        return description[:DESCRIPTION_WIDTH] + "..."
    return description
