"""Wealthsimple, downloaded through its API: activities and monthly statements."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, time
from typing import TYPE_CHECKING

import ws_api
from ws_api import CurlException, ManualLoginRequired

from app import get_config
from domain import TORONTO_TZ, BrokerId
from services.broker import (
    BrokerError,
    BrokerFailure,
    DownloadResult,
    PreviewTable,
    resolve_window,
)
from services.broker_files import ws_activities_file_name, ws_statement_file_name
from services.wealthsimple_client import (
    WealthsimpleAuthenticationError,
    WealthsimpleClient,
)
from term import console_info, console_success, console_warning

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from datetime import date
    from pathlib import Path

    from config.brokers import WealthsimpleSettings
    from models.wealthsimple import ActivityFeedItem, WealthsimpleAccount
    from services.broker import Progress

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 10
_PREVIEW_COLUMNS = (
    "Date",
    "Action",
    "Ticker",
    "Amount",
    "Currency",
    "Account",
    "Description",
)


class WealthsimpleBroker:
    """Wealthsimple: one activities file per download, one statement per account."""

    id = BrokerId.WS

    def __init__(self, settings: WealthsimpleSettings) -> None:
        """Sign in and download as `settings` says."""
        self._client = WealthsimpleClient(settings)

    def sign_in(self) -> None:
        """Resume the stored session, or log in, prompting for what is missing."""
        with self._translated_errors():
            self._client.ensure_authenticated()

    def reset_credentials(self) -> None:
        """Forget the stored login, so the next download asks for it again."""
        self._client.reset_credentials()
        console_success("Wealthsimple credentials reset successfully")

    def download(
        self,
        start: date | None,
        end: date | None,
        progress: Progress,
    ) -> DownloadResult:
        """Download every account's activities in the window into one source file."""
        window = resolve_window(self.id, start, end)
        with self._translated_errors():
            progress("Retrieving accounts...")
            accounts = self._client.get_accounts()
            progress("Downloading transactions...")
            activities = self._client.get_activities(
                [account.id for account in accounts],
                datetime.combine(window.start, time(), tzinfo=TORONTO_TZ),
                datetime.combine(window.end, time(), tzinfo=TORONTO_TZ),
                load_all=True,
            )
            console_info(f"Retrieved {len(activities)} source rows")
            if not activities:
                return DownloadResult()
            name = ws_activities_file_name(
                window.start.strftime("%Y%m%d"),
                window.end.strftime("%Y%m%d"),
            )
            self._client.export_activities_to_csv(activities, name, accounts)
        return DownloadResult(
            files=(get_config().imports_path / name,),
            previews=(_preview(activities, accounts),),
        )

    def monthly_statements(self, month: str, progress: Progress) -> list[Path]:
        """Download every account's statement for a YYYY-MM month."""
        files: list[Path] = []
        total_rows = 0
        with self._translated_errors():
            progress("Retrieving accounts...")
            accounts = self._client.get_accounts()
            progress("Downloading monthly statements...")
            for account in accounts:
                rows = self._client.get_monthly_statement(account.id, f"{month}-01")
                if not rows:
                    console_warning(
                        f"No statement rows found for account {account.account_name}",
                    )
                    continue
                name = ws_statement_file_name(account.account_name, month)
                self._client.export_statement_to_csv(rows, name)
                files.append(get_config().statements_path / name)
                total_rows += len(rows)
        if files:
            console_success(f"Retrieved {total_rows} statement rows")
        return files

    @contextmanager
    def _translated_errors(self) -> Generator[None]:
        """Raise what the API client raises as a BrokerError saying what went wrong.

        A payload of an unexpected shape is left to stop the download as it is, so
        the change is noticed and fixed.
        """
        try:
            yield
        except (WealthsimpleAuthenticationError, ManualLoginRequired) as e:
            raise BrokerError(self.id, BrokerFailure.LOGIN_FAILED, str(e)) from e
        except CurlException as e:
            raise BrokerError(self.id, BrokerFailure.BUSY, str(e)) from e
        except ws_api.WSApiException as e:
            detail = str(e) or type(e).__name__
            raise BrokerError(self.id, BrokerFailure.UNEXPECTED_RESPONSE, detail) from e
        except OSError as e:
            raise BrokerError(self.id, BrokerFailure.NOT_SAVED, str(e)) from e


def _preview(
    activities: Sequence[ActivityFeedItem],
    accounts: Sequence[WealthsimpleAccount],
) -> PreviewTable:
    """Preview the first activities retrieved, each account by its nickname."""
    names = {
        account.id: account.nickname or account.account_type or account.id
        for account in accounts
    }
    rows = tuple(
        (
            activity.occurred_on.isoformat(),
            activity.type or "",
            activity.asset_symbol or "",
            activity.amount or "",
            activity.currency or "",
            names.get(activity.account_id or "")
            or (activity.account_id[:8] + "..." if activity.account_id else ""),
            activity.description or "",
        )
        for activity in activities[:PREVIEW_ROWS]
    )
    return PreviewTable(
        title="Downloaded Source Rows Preview",
        columns=_PREVIEW_COLUMNS,
        rows=rows,
        total=len(activities),
    )
