"""The `folio update` command.

Catches the folio up in one shot:
download from every broker, import, confirm settlement dates from
monthly statements, run the health checks, write the workbook and
show the dashboards.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app import bootstrap, get_config
from cli.commands.common import export_to_parquet, require_ledger
from cli.commands.dash import show_types
from cli.commands.download import download_from, download_monthly_statements
from cli.commands.generate import write_folio
from cli.commands.import_data import (
    confirm_demo_replacement,
    import_directory,
)
from cli.commands.settle_info import archive_statement, import_single_statement
from db import txns
from domain import TORONTO_TZ, BrokerId, Grain, SettlementOutcome
from engine.accounts import FOLIO, Pool
from engine.checks import CheckReport, run_checks
from engine.pools import FolioValuation
from engine.since_update import record_update
from engine.update_review import (
    review_checks,
    review_import,
    review_statement,
    review_stuck,
    review_transfer_pairs,
    review_unpriced,
    split_remaining,
    statement_months,
)
from exporters import ParquetExporter
from models import Concern, HardFailure, UpdateReport, UpdateStage
from services import BrokerError, BrokerFailure, BrokerRegistry
from services.broker_files import (
    broker_of_file,
    date_range_of,
    delete_new_files,
    statements_for_month,
    waiting_files,
)
from term import console_info, console_rule
from term.progress import spinner
from ui.views.checks import print_report
from ui.views.update import show_update_report, show_workbook_link
from ui.vocabulary import RULE_UPDATE, SPINNER_STATEMENT

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from config import Config
    from engine.since_update import SinceUpdate
    from models import FileImport


def run_update(*, resume: bool = False, offline: bool = False) -> UpdateReport:
    """Catch the folio up and report what changed.

    Args:
        resume: Skip downloading and carry on from whatever waits in the
            imports folder, after a failed import was fixed.
        offline: Never touch the network: skip downloading, import only the
            files and statements already waiting, and value the folio from
            cached quotes and rates.

    Returns:
        The report shown, failed when a stage stopped the run.
    """
    config = bootstrap.reload_config()
    today = datetime.now(TORONTO_TZ).date()
    report = UpdateReport()
    first_new_id = txns.latest_id()
    overlap_days = {broker: txns.latest_date_for(broker) for broker in BrokerId}

    if offline:
        console_info("Offline: skipping downloads, importing what is waiting.")
    elif resume:
        console_info("Resuming: skipping downloads, importing what is waiting.")
    else:
        _download_stage(config, report)

    if not report.failed:
        _import_stage(config, report, overlap_days)

    if not report.failed:
        _statement_stage(config, report, today, offline=offline)

    report.new_txns = txns.since(first_new_id)

    since: list[SinceUpdate] | None = None
    if not report.failed:
        _review_transfers(report)
        _check_stage(report, offline=offline)
        _generate_stage(report, offline=offline)
        since = _dashboard_stage(offline=offline)
        if report.workbook is not None:
            show_workbook_link(report.workbook)

    show_update_report(report, since)
    return report


# -- DOWNLOAD -------------------------------------------------------------------


def _download_stage(config: Config, report: UpdateReport) -> None:
    """Download from every broker, or remove what this run downloaded and stop.

    Args:
        config: The application configuration.
        report: Where a failure is recorded.
    """
    console_rule("Download", style=RULE_UPDATE)
    before = set(waiting_files(config.imports_path))
    try:
        error = _download_all()
    except KeyboardInterrupt:  # pragma: no cover
        delete_new_files(config.imports_path, before)
        raise
    if error is None:
        return

    removed = delete_new_files(config.imports_path, before)
    report.failure = HardFailure(
        stage=UpdateStage.DOWNLOAD,
        error=f"{error.broker.display_name} download failed: {error}",
        next_steps=_download_fixes(error, removed),
    )


def _download_all() -> BrokerError | None:
    """Download from every broker in turn, stopping at the first that fails.

    Returns:
        Why a broker failed, or None when every one succeeded. A broker whose
        parts failed is described by its most actionable kind and every part's
        error.
    """
    for broker in BrokerRegistry.load().all():
        try:
            result = download_from(broker, None, None)
        except BrokerError as error:
            return error
        if result.failures:
            ranked = list(BrokerFailure)
            kind = min((f.error.kind for f in result.failures), key=ranked.index)
            detail = "; ".join(f"{f.part}: {f.error}" for f in result.failures)
            return BrokerError(broker.id, kind, detail)
    return None


def _download_fixes(error: BrokerError, removed: list[str]) -> tuple[str, ...]:
    """Spell out how to recover from a failed download, by what went wrong.

    Args:
        error: Why the download failed.
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
    name = error.broker.display_name
    credentials = f"folio download -b {error.broker.cli_name} --credentials"
    match error.kind:
        case BrokerFailure.CREDENTIALS_EXPIRED:
            steps.extend(
                (f"The {name} credentials have expired. Store new ones:", credentials),
            )
        case BrokerFailure.LOGIN_FAILED:
            steps.extend((f"{name} could not log in. Reset the login:", credentials))
        case BrokerFailure.NOT_CONFIGURED:
            steps.append(f"Set up {name} under brokers in config.yaml.")
        case BrokerFailure.NOT_SAVED:
            steps.append(
                "Close the file if another program has it open, and check there "
                "is space on the disk.",
            )
        case _:
            steps.append(
                "If the broker is busy or still preparing the statement, wait a few "
                "minutes. If it keeps failing, logs/folio.log has the detail.",
            )
    steps.extend(("Then run:", "folio update"))
    return tuple(steps)


# -- IMPORT ---------------------------------------------------------------------


def _import_stage(
    config: Config,
    report: UpdateReport,
    overlap_days: dict[BrokerId, str | None],
) -> None:
    """Import every waiting file and review each one.

    Args:
        config: The application configuration.
        report: Where concerns, notes and a failure are recorded.
        overlap_days: Each broker's overlap day: its latest stored transaction date
            before the run.
    """
    console_rule("Import", style=RULE_UPDATE)
    if waiting_files(config.imports_path) and not confirm_demo_replacement():
        report.failure = HardFailure(
            stage=UpdateStage.IMPORT,
            error="The folio is the demo folio, and replacing it was declined.",
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
        broker = broker_of_file(outcome.path.name)
        review = review_import(
            outcome.path.name,
            outcome.results,
            overlap_days.get(broker) if broker else None,
        )
        report.concerns.extend(review.concerns)
        report.pending.extend(review.pending)
        report.expected.extend(review.expected)

    if any(outcome.imported for outcome in outcomes):
        export_to_parquet()

    failed = [outcome for outcome in outcomes if outcome.error is not None]
    if failed:
        report.failure = HardFailure(
            stage=UpdateStage.IMPORT,
            error="\n".join(f"{o.path.name}: {o.error}" for o in failed),
            next_steps=_import_fixes(config, failed),
        )


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
            f"The file(s) stayed in {config.imports_path}; logs/audit.log "
            "has the detail. Every other file was imported."
        ),
        "If a file needs a mapping or rule change, fix config.yaml.",
        (
            "If a file's contents are wrong (truncated or garbled), delete it "
            "and download its range again:"
        ),
    ]
    for outcome in failed:
        broker = broker_of_file(outcome.path.name)
        date_range = date_range_of(outcome.path.name)
        if broker and date_range:
            from_date, to_date = date_range
            steps.append(
                f"folio download -b {broker.cli_name} -f {from_date} -t {to_date}",
            )
    steps.extend(
        (
            (
                "Rows that arrive again from a report that did import are "
                "rejected as duplicates. Then run:"
            ),
            "folio update --resume",
        ),
    )
    return tuple(steps)


# -- STATEMENTS -----------------------------------------------------------------


def _statement_stage(
    config: Config,
    report: UpdateReport,
    today: date,
    *,
    offline: bool,
) -> None:
    """Confirm calculated settlement dates from every statement that can exist.

    A month's statement still waiting in the statements folder is imported
    rather than fetched. Every statement read is archived and never read again,
    so a month that keeps calculated dates is downloaded afresh next run.

    Args:
        config: The application configuration.
        report: Where confirmations, concerns and notes are recorded.
        today: The date the run happens on.
        offline: Import only the statements already waiting, fetching none.
    """
    console_rule("Statements", style=RULE_UPDATE)
    calculated = txns.calculated_settlements(BrokerId.WS)
    months = statement_months([txn.settle_date for txn in calculated], today)
    if not months:  # pragma: no cover
        console_info("No calculated settlement date waits on a published statement.")

    changed = False
    imported: set[str] = set()
    for month in months:
        files = statements_for_month(config.statements_path, month)
        if not files and offline:
            report.pending.append(
                f"{month} statement not fetched offline; to fetch it: folio update",
            )
        elif not files:
            files = _download_statement(month, report)
        if files:
            imported.add(month)
        for path in files:
            result = import_single_statement(path, verbose=True, interactive=False)
            archive_statement(path)
            report.confirmed.extend(
                match
                for match in result.settlement_matches
                if match.outcome is SettlementOutcome.CONFIRMED
            )
            report.concerns.extend(
                review_statement(
                    path.name,
                    result.settlement_matches,
                    result.transfers_rejected,
                ),
            )
            changed |= (
                result.settlements_confirmed > 0 or result.transfers_created() > 0
            )

    if changed:
        with spinner(color=SPINNER_STATEMENT) as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            ParquetExporter().export_all()

    remaining = txns.calculated_settlements(BrokerId.WS)
    on_hand = {
        month
        for month in statement_months([txn.settle_date for txn in remaining], today)
        if month in imported or statements_for_month(config.statements_path, month)
    }
    stuck, pending = split_remaining(remaining, on_hand, today)
    if (concern := review_stuck(stuck)) is not None:
        report.concerns.append(concern)
    if pending:
        report.pending.append(
            f"{len(pending)} settlement date(s) wait on statements not out yet",
        )


def _download_statement(month: str, report: UpdateReport) -> list[Path]:
    """Download one month's statement, noting when it is missing or failed.

    Args:
        month: The YYYY-MM month.
        report: Where a failure or an unpublished month is recorded.

    Returns:
        The statement files written, empty when there are none.
    """
    try:
        files = download_monthly_statements(
            BrokerRegistry.load().wealthsimple,
            month,
        )
    except BrokerError as error:
        report.concerns.append(
            Concern(
                stage=UpdateStage.STATEMENTS,
                title=f"{month} statement download failed",
                why=(
                    f"{error.detail or error.kind}. Its settlement dates stay "
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
    if not report.new_txns:
        return
    concern = review_transfer_pairs(
        txns.read(),
        [txn.txn_id for txn in report.new_txns],
    )
    if concern is not None:  # pragma: no cover
        report.concerns.append(concern)


def _check_stage(report: UpdateReport, *, offline: bool) -> None:
    """Run the health checks and raise every failure."""
    console_rule("Check", style=RULE_UPDATE)
    results = run_checks(require_ledger(offline=offline), get_config())
    print_report(CheckReport(tuple(results)), only=None)
    concerns, warned = review_checks(results)
    report.concerns.extend(concerns)
    if warned:
        report.expected.append(f"{warned} check(s) only warned: folio check")


def _generate_stage(report: UpdateReport, *, offline: bool) -> None:
    """Write the workbook and raise any holding it could not price."""
    console_rule("Generate", style=RULE_UPDATE)
    written = write_folio(offline=offline)
    if written is None:  # pragma: no cover
        return
    report.workbook = written.path
    if (concern := review_unpriced(written.unpriced)) is not None:
        report.concerns.append(concern)


def _dashboard_stage(*, offline: bool) -> list[SinceUpdate] | None:
    """Save the new baseline, then show every account type's dashboard.

    Each pool is totalled once: the change since the previous update is read
    from the same totals that are saved, then shown.

    Returns:
        What changed in each account type and the folio since the previous
        update, empty when this is the first, or None for a folio with no
        transactions, which saves nothing.
    """
    console_rule("Dashboard", style=RULE_UPDATE)
    ledger = require_ledger(priced=True, offline=offline)
    if ledger.frame.empty:
        return None
    valuation = FolioValuation.build(ledger, currency="native", offline=offline)
    types = [Pool(Grain.TYPE, name) for name in valuation.account_types()]
    since = record_update(valuation, [*types, FOLIO]) or []
    folio = next((change for change in since if change.pool == FOLIO), None)
    show_types(valuation, offline=offline, since=folio)
    return since
