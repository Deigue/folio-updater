"""The `folio update` command.

Catches the folio up in one shot:
download from every broker, import, confirm which settlement dates can be updated via
monthly statements, run the health checks, write the workbook and
show the dashboards.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

import typer
from ws_api import WSApiException

from app import bootstrap, get_config
from cli.commands.common import (
    ensure_fx_coverage,
    export_to_parquet,
    load_folio,
    replay_result,
)
from cli.commands.dash import show_dash
from cli.commands.download import (
    download_ibkr,
    download_wealthsimple,
    download_ws_statement,
    latest_txn_date,
)
from cli.commands.generate import write_folio
from cli.commands.import_data import (
    confirm_demo_replacement,
    import_directory,
    pending_import_files,
)
from cli.commands.settle_info import archive_statement, import_single_statement
from db import get_connection, get_max_value, get_rows
from domain import TORONTO_TZ, Column, SettlementOutcome, Table
from engine.checks import run_checks
from engine.update_review import (
    review_checks,
    review_import,
    review_late_cancellations,
    review_statement,
    review_stuck,
    review_transfer_pairs,
    review_unpriced,
    split_remaining,
    statement_months,
)
from exporters import ParquetExporter
from models import Concern, HardFailure, UpdateReport, UpdateStage
from services import IBKRServiceError, WealthsimpleServiceError
from term import console_info, console_rule
from term.progress import ProgressDisplay
from ui.views.checks import print_report
from ui.views.update import show_update_report, show_workbook_link

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    import pandas as pd

    from cli.commands.import_data import FileImport
    from config import Config

# Broker keys as they appear in account names and download file names.
IBKR = "ibkr"
WS = "ws"
_DOWNLOAD_NAMES = {IBKR: "ibkr", WS: "wealthsimple"}

_DOWNLOAD_ERRORS = (IBKRServiceError, WealthsimpleServiceError, WSApiException, OSError)
_STATEMENT_SUFFIXES = (".csv", ".xlsx")
# Download files carry their range: ibkr_TradeConfirmation_20260807_20260902.csv
_FILE_RANGE = re.compile(r"_(\d{4})(\d{2})(\d{2})_(\d{4})(\d{2})(\d{2})")
_UPDATE_RULE = "bright_blue"


def run_update(*, resume: bool = False) -> None:
    """Catch the folio up and report what changed.

    Args:
        resume: Skip downloading and carry on from whatever waits in the
            imports folder, after a failed import was fixed.

    Raises:
        typer.Exit: With 1 when a stage stopped the run.
    """
    config = bootstrap.reload_config()
    today = datetime.now(TORONTO_TZ).date()
    report = UpdateReport()
    first_new_id = _max_txn_id()
    since = {IBKR: latest_txn_date(IBKR), WS: latest_txn_date(WS)}

    if resume:
        console_info("Resuming: skipping downloads, importing what is waiting.")
    else:
        _download_stage(config, report)

    if not report.failed:
        _import_stage(config, report, since)

    if not report.failed:
        _statement_stage(config, report, today)

    report.new_txns = _txns_since(first_new_id)

    if not report.failed:
        _review_transfers(report)
        _check_stage(report)
        _generate_stage(report)
        console_rule("Dashboard", style=_UPDATE_RULE)
        show_dash(by_type=True)
        if report.workbook is not None:
            show_workbook_link(report.workbook)

    show_update_report(report)
    if report.failed:
        raise typer.Exit(1)


# -- DOWNLOAD -------------------------------------------------------------------


def _download_stage(config: Config, report: UpdateReport) -> None:
    """Download from every broker, or remove what this run downloaded and stop.

    Args:
        config: The application configuration.
        report: Where a failure is recorded.
    """
    console_rule("Download", style=_UPDATE_RULE)
    before = set(pending_import_files(config.imports_path))
    try:
        failed = _download_all(config)
    except KeyboardInterrupt:  # pragma: no cover
        _remove_new_files(config.imports_path, before)
        raise
    if failed is None:
        return

    broker, error = failed
    removed = _remove_new_files(config.imports_path, before)
    report.failure = HardFailure(
        stage=UpdateStage.DOWNLOAD,
        error=f"{broker} download failed: {error}",
        next_steps=_download_fixes(broker, error, removed),
    )


def _download_all(config: Config) -> tuple[str, str] | None:
    """Download IBKR, then Wealthsimple, stopping at the first failure.

    Args:
        config: The application configuration.

    Returns:
        The broker that failed and why, or None when both succeeded.
    """
    broker = "IBKR"
    try:
        failures = download_ibkr(config, None, None, None)
        if failures:
            return broker, "; ".join(failures)
        broker = "Wealthsimple"
        download_wealthsimple(None, None)
    except _DOWNLOAD_ERRORS as error:
        return broker, str(error) or type(error).__name__
    except typer.Exit:
        return broker, "see the message above"
    return None


def _remove_new_files(imports: Path, before: set[Path]) -> list[str]:
    """Delete the files that appeared in the imports folder during this run.

    Args:
        imports: The imports folder.
        before: The files that were there when the run started.

    Returns:
        The names of the files removed.
    """
    new = [path for path in pending_import_files(imports) if path not in before]
    for path in new:
        path.unlink()
    return [path.name for path in new]


def _download_fixes(broker: str, error: str, removed: list[str]) -> tuple[str, ...]:
    """Spell out how to recover from a failed download.

    Args:
        broker: The broker that failed.
        error: Why it failed.
        removed: Files this run downloaded and then removed.

    Returns:
        The steps, in order.
    """
    steps: list[str] = []
    if removed:
        steps.append(
            f"Removed {len(removed)} file(s) this run downloaded, so nothing "
            f"partial gets imported: {', '.join(removed)}",
        )
    lowered = error.lower()
    if broker == "IBKR" and ("1012" in lowered or "expired" in lowered):
        steps.extend(
            (
                (
                    "The IBKR flex token has expired. Create a new one in the "
                    "IBKR portal, then store it:"
                ),
                "folio download -b ibkr --credentials",
            ),
        )
    elif broker == "Wealthsimple" and "auth" in lowered:
        steps.extend(
            (
                "Wealthsimple could not log in. Reset the login:",
                "folio download -b wealthsimple --credentials",
            ),
        )
    else:
        steps.append(
            "If the broker is busy or still preparing the report, wait a few minutes.",
        )
    steps.extend(("Then run:", "folio update"))
    return tuple(steps)


# -- IMPORT ---------------------------------------------------------------------


def _broker_of(filename: str) -> str | None:
    """Return the broker a downloaded file came from, or None for any other file."""
    if filename.startswith(f"{IBKR}_"):
        return IBKR
    if filename.startswith(f"{WS}_"):
        return WS
    return None  # pragma: no cover


def _import_stage(
    config: Config,
    report: UpdateReport,
    since: dict[str, str | None],
) -> None:
    """Import every waiting file and review each one.

    Args:
        config: The application configuration.
        report: Where concerns, notes and a failure are recorded.
        since: Each broker's latest stored transaction date before the run.
    """
    console_rule("Import", style=_UPDATE_RULE)
    if pending_import_files(config.imports_path) and not confirm_demo_replacement():
        report.failure = HardFailure(
            stage=UpdateStage.IMPORT,
            error="The folio holds the demo portfolio, and replacing it was declined.",
            next_steps=(
                (
                    "To replace the demo with your transactions: "
                    "folio update --resume, and answer yes."
                ),
                f"To keep the demo: move the downloads out of {config.imports_path}.",
            ),
        )
        return
    outcomes = import_directory(config.imports_path, verbose=True, interactive=False)
    if not outcomes:
        console_info("Nothing new to import.")
        return

    for outcome in outcomes:
        if outcome.results is None:
            continue
        broker = _broker_of(outcome.path.name)
        concerns, noise = review_import(
            outcome.path.name,
            outcome.results,
            since.get(broker) if broker else None,
        )
        report.concerns.extend(concerns)
        report.pending.extend(noise)

    _review_late_cancellations(outcomes, report)

    if any(outcome.imported for outcome in outcomes):
        export_to_parquet()

    failed = [outcome for outcome in outcomes if outcome.error is not None]
    if failed:
        report.failure = HardFailure(
            stage=UpdateStage.IMPORT,
            error="\n".join(f"{o.path.name}: {o.error}" for o in failed),
            next_steps=_import_fixes(config, failed),
        )


def _review_late_cancellations(
    outcomes: list[FileImport],
    report: UpdateReport,
) -> None:
    """Raise cancellations that voided nothing in their own file.

    Args:
        outcomes: Every file imported this run.
        report: Where the concerns are recorded.
    """
    late = [
        (outcome.path.name, outcome.results.cancel_events)
        for outcome in outcomes
        if outcome.results is not None
        and any(event.cancelled is None for event in outcome.results.cancel_events)
    ]
    if late:  # pragma: no cover
        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        for filename, events in late:
            report.concerns.extend(review_late_cancellations(filename, events, txns))


def _import_fixes(config: Config, failed: list[FileImport]) -> tuple[str, ...]:
    """Spell out how to recover from files that could not be imported.

    Args:
        config: The application configuration.
        failed: The files that failed.

    Returns:
        The steps, in order.
    """
    steps = [
        (
            f"The file(s) stayed in {config.imports_path}; logs/importer.log "
            "has the detail. Every other file was imported."
        ),
        "If a file needs a mapping or rule change, fix config.yaml.",
        (
            "If a file's contents are wrong (truncated or garbled), delete it "
            "and download its range again:"
        ),
    ]
    for outcome in failed:
        broker = _broker_of(outcome.path.name)
        found = _FILE_RANGE.search(outcome.path.name)
        if broker and found:
            y1, m1, d1, y2, m2, d2 = found.groups()
            steps.append(
                f"folio download -b {_DOWNLOAD_NAMES[broker]} "
                f"-f {y1}-{m1}-{d1} -t {y2}-{m2}-{d2}",
            )
    steps.extend(
        (
            (
                "Rows that arrive again from a report that did import are "
                "skipped as duplicates. Then run:"
            ),
            "folio update --resume",
        ),
    )
    return tuple(steps)


# -- STATEMENTS -----------------------------------------------------------------


def _calculated_ws() -> pd.DataFrame:
    """Return the Wealthsimple transactions whose settlement date is calculated."""
    with get_connection() as conn:
        return get_rows(
            conn,
            Table.TXNS,
            where=(
                f'"{Column.Txn.SETTLE_CALCULATED}" = ? '
                f'AND UPPER("{Column.Txn.ACCOUNT}") LIKE ?'
            ),
            params=[1, f"%{WS.upper()}%"],
            order_by=f'"{Column.Txn.SETTLE_DATE}", "{Column.Txn.TXN_ID}"',
        )


def _statement_files(config: Config, month: str) -> list[Path]:
    """Return the statements waiting for a YYYY-MM month.

    Archived statements are ignored: a month that still has calculated dates
    after its statement was archived is downloaded again.
    """
    pattern = f"ws_statement_*_{month.replace('-', '')}.*"
    return sorted(
        path
        for path in config.statements_path.glob(pattern)
        if path.suffix.lower() in _STATEMENT_SUFFIXES
    )


def _statement_stage(config: Config, report: UpdateReport, today: date) -> None:
    """Confirm calculated settlement dates from every statement that can exist.

    A month's statement still waiting in the statements folder is imported
    rather than fetched. Every statement read is archived and never read again,
    so a month that keeps calculated dates is downloaded afresh next run.

    Args:
        config: The application configuration.
        report: Where confirmations, concerns and notes are recorded.
        today: The date the run happens on.
    """
    console_rule("Statements", style=_UPDATE_RULE)
    calculated = _calculated_ws()
    months = statement_months(calculated[Column.Txn.SETTLE_DATE], today)
    if not months:  # pragma: no cover
        console_info("No calculated settlement date waits on a published statement.")

    changed = False
    imported: set[str] = set()
    for month in months:
        files = _statement_files(config, month) or _fetch_statement(month, report)
        if files:
            imported.add(month)
        for path in files:
            result = import_single_statement(path, verbose=True, interactive=False)
            archive_statement(path)
            report.settled.extend(
                match
                for match in result.settlement_matches
                if match.outcome is SettlementOutcome.MATCHED
            )
            report.concerns.extend(
                review_statement(
                    path.name,
                    result.settlement_matches,
                    result.transfers_rejected,
                ),
            )
            changed |= result.settlement_updates > 0 or result.transfers_created() > 0

    if changed:
        with ProgressDisplay.spinner(color="dark_violet") as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            ParquetExporter().export_all()

    remaining = _calculated_ws()
    on_hand = {
        month
        for month in statement_months(remaining[Column.Txn.SETTLE_DATE], today)
        if month in imported or _statement_files(config, month)
    }
    stuck, pending = split_remaining(remaining, on_hand, today)
    if (concern := review_stuck(stuck)) is not None:
        report.concerns.append(concern)
    if not pending.empty:
        report.pending.append(
            f"{len(pending)} settlement date(s) wait on statements not out yet",
        )


def _fetch_statement(month: str, report: UpdateReport) -> list[Path]:
    """Download one month's statement, noting when it is missing or failed.

    Args:
        month: The YYYY-MM month.
        report: Where a failure or an unpublished month is recorded.

    Returns:
        The statement files written, empty when there are none.
    """
    try:
        files = download_ws_statement(f"{month}-01")
    except _DOWNLOAD_ERRORS as error:
        report.concerns.append(
            Concern(
                stage=UpdateStage.STATEMENTS,
                title=f"{month} statement download failed",
                why=(
                    f"{error or type(error).__name__}. Its settlement dates stay "
                    "calculated until the statement is imported."
                ),
                commands=("folio update --resume",),
            ),
        )
        return []
    if not files:
        report.pending.append(
            f"{month} statement not published yet; once it is: folio update --resume",
        )
    return files


# -- CHECK, GENERATE ------------------------------------------------------------


def _review_transfers(report: UpdateReport) -> None:
    """Raise new withdrawals and contributions that look like transfers."""
    if report.new_txns.empty:
        return
    with get_connection() as conn:
        txns = get_rows(conn, Table.TXNS)
    concern = review_transfer_pairs(txns, report.new_txns[Column.Txn.TXN_ID])
    if concern is not None:  # pragma: no cover
        report.concerns.append(concern)


def _check_stage(report: UpdateReport) -> None:
    """Run the health checks and raise every failure."""
    console_rule("Check", style=_UPDATE_RULE)
    ensure_fx_coverage()
    results = run_checks(replay_result(load_folio()), get_config())
    print_report(results, only=None)
    concerns, warned = review_checks(results)
    report.concerns.extend(concerns)
    if warned:
        report.pending.append(f"{warned} check(s) only warned: folio check")


def _generate_stage(report: UpdateReport) -> None:
    """Write the workbook and raise any holding it could not price."""
    console_rule("Generate", style=_UPDATE_RULE)
    written = write_folio()
    if written is None:  # pragma: no cover
        return
    report.workbook = written.path
    if (concern := review_unpriced(written.unpriced)) is not None:
        report.concerns.append(concern)


# -- FOLIO STATE ----------------------------------------------------------------


def _max_txn_id() -> int:
    """Return the highest TxnId stored, or 0 for an empty folio."""
    with get_connection() as conn:
        latest = get_max_value(conn, Table.TXNS, Column.Txn.TXN_ID)
    return int(latest) if latest else 0


def _txns_since(txn_id: int) -> pd.DataFrame:
    """Return the transactions stored after a TxnId, oldest first."""
    with get_connection() as conn:
        return get_rows(
            conn,
            Table.TXNS,
            where=f'"{Column.Txn.TXN_ID}" > ?',
            params=[txn_id],
            order_by=f'"{Column.Txn.TXN_ID}"',
        )
