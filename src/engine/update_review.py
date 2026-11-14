"""Reviewing the effects of what `folio update` run did.

Every stage of an update prints its own audit. These rules read those
results and pick out noticable anomolies afterwards: rows that never made it in,
rows that arrived twice, cash that was really a transfer, settlement dates no
statement could confirm. Each is labeled a `Concern` carrying the rows involved and
the commands required to fix them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from domain import (
    NOTHING_TO_VOID,
    TXN_ESSENTIALS,
    Action,
    CheckStatus,
    Column,
    SettlementOutcome,
)
from domain.cells import has_value
from domain.dates import iso_day
from domain.numeric import CENT
from models import Concern, Txn, UpdateStage, txns_frame

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import date

    from engine.checks import CheckResult
    from models import CancelEvent, ImportResults, MergeEvent, SettlementMatch

# Cash moves between two of the user's own accounts land a few days apart at most.
LOOKALIKE_TRANSFER_DAYS = 3
# Two transfer legs carry the same amount to within a cent.
_AMOUNT_TOLERANCE = float(CENT)
_MONTH = "%Y-%m"

# Every `folio add` needs these; a row lacking one gets a placeholder.
_ADD_REQUIRED = frozenset(
    {
        Column.Txn.ACTION,
        Column.Txn.TXN_DATE,
        Column.Txn.ACCOUNT,
        Column.Txn.CURRENCY,
        Column.Txn.AMOUNT,
    },
)

# How a placeholder names a column whose header is not a word.
_PLACEHOLDERS = {Column.Txn.CURRENCY: "currency"}

# The `folio add` option for each column, in the order the command reads best.
_ADD_OPTIONS: tuple[tuple[str, str], ...] = (
    (Column.Txn.ACTION, "-t"),
    (Column.Txn.TXN_DATE, "-d"),
    (Column.Txn.ACCOUNT, "-a"),
    (Column.Txn.CURRENCY, "-c"),
    (Column.Txn.TICKER, "-s"),
    (Column.Txn.AMOUNT, "-m"),
    (Column.Txn.PRICE, "-p"),
    (Column.Txn.UNITS, "-u"),
)


def _essentials(df: pd.DataFrame) -> list[str]:
    """Return the essential transaction columns a frame actually carries."""
    return [str(column) for column in TXN_ESSENTIALS if column in df.columns]


def add_command(row: pd.Series, *, force: bool = False) -> str:
    """Spell out the `folio add` that would recreate a row as it would be stored.

    A value every add needs but the row lacks becomes a `<placeholder>` for the
    user to fill in; an optional one the row lacks (an FXT's ticker) is left
    out, the way the import would have stored it.

    Args:
        row: The transaction to recreate.
        force: Add `--force`, for a row the folio would otherwise call a duplicate.

    Returns:
        The command, ready to copy.
    """
    parts = ["folio add"]
    for column, option in _ADD_OPTIONS:
        value = row.get(column)
        if has_value(value):
            shown = str(value).strip()
        elif column in _ADD_REQUIRED:
            shown = f"<{_PLACEHOLDERS.get(column, column.lower())}>"
        else:
            continue
        parts.append(f'{option} "{shown}"' if " " in shown else f"{option} {shown}")
    if force:
        parts.append("--force")
    return " ".join(parts)


# -- IMPORT ---------------------------------------------------------------------


def header_rows(invalid: pd.DataFrame, source_columns: Iterable[str]) -> pd.Series:
    """Mark invalid rows that are repeated header lines from the source file.

    Args:
        invalid: Rows the formatter rejected as invalid.
        source_columns: Column names of the file as read.

    Returns:
        A boolean mask over `invalid`, True for header rows.
    """
    if invalid.empty:
        return pd.Series(dtype=bool)
    names = {str(name).strip() for name in source_columns}
    essentials = _essentials(invalid)

    def is_header(row: pd.Series) -> bool:
        values = [str(row[c]).strip() for c in essentials if has_value(row[c])]
        return bool(values) and all(value in names for value in values)

    return invalid.apply(is_header, axis=1).astype(bool)


def upcoming_dividends(invalid: pd.DataFrame) -> pd.Series:
    """Mark invalid dividends that carry no amount yet.

    A broker can list a dividend once its ex-dividend date passes, before the
    payment lands, with no amount (Wealthsimple does). The import rightly rejects
    it; the paid dividend arrives in a later download.

    Args:
        invalid: Rows the formatter rejected as invalid.

    Returns:
        A boolean mask over `invalid`, True for announced, unpaid dividends.
    """
    amounts = pd.to_numeric(
        invalid.get(Column.Txn.AMOUNT, pd.Series(index=invalid.index)),
        errors="coerce",
    )
    dividend = invalid[Column.Txn.ACTION].astype(str).str.upper() == Action.DIVIDEND
    return dividend & (amounts.isna() | (amounts == 0))


def _reasons(rows: pd.DataFrame) -> tuple[str, ...]:
    """Say why each invalid row was rejected, one line per row."""
    return tuple(
        f"{_row_summary(row)}: {row[Column.REJECTION_REASON]}"
        for row in rows.to_dict("records")
    )


def _row_summary(row: Mapping[Any, Any]) -> str:
    """Render a row as date ticker amount currency account, skipping blanks."""
    columns = (
        Column.Txn.TXN_DATE,
        Column.Txn.ACTION,
        Column.Txn.TICKER,
        Column.Txn.AMOUNT,
        Column.Txn.CURRENCY,
        Column.Txn.ACCOUNT,
    )
    return " ".join(
        str(row.get(column)).strip() for column in columns if has_value(row.get(column))
    )


def odd_merges(events: Iterable[MergeEvent]) -> list[MergeEvent]:
    """Pick out merges whose shape suggests a rule folded in the wrong rows.

    Args:
        events: Merges performed during an import.

    Returns:
        The merges worth a second look.
    """
    odd: list[MergeEvent] = []
    for event in events:
        sources = event.source_rows
        actions = list(sources.get(Column.Txn.ACTION, []))
        repeated = len(actions) != len(set(actions))
        amounts = pd.to_numeric(
            sources.get(Column.Txn.AMOUNT, pd.Series(dtype=float)),
            errors="coerce",
        )
        merged = pd.to_numeric(
            pd.Series([event.merged_row.get(Column.Txn.AMOUNT)]),
            errors="coerce",
        ).iloc[0]
        flipped = bool((amounts > 0).any()) and pd.notna(merged) and merged < 0
        if repeated or flipped:
            odd.append(event)
    return odd


def before_overlap_day(
    rejected: pd.DataFrame,
    overlap_day: str | None,
) -> pd.DataFrame:
    """Return database duplicates dated before the broker's overlap day.

    A download restarts on the overlap day, the broker's latest stored trade
    date, so that day always arrives twice and is rightly rejected. A duplicate
    from any earlier day means an old or overlapping file was imported.

    Args:
        rejected: Rows rejected as already in the database.
        overlap_day: The broker's latest stored transaction date before this run, or
            None when the file is not a broker download.

    Returns:
        The rejected rows that are not explained by the overlap day.
    """
    if rejected.empty or overlap_day is None:
        return rejected
    dates = rejected[Column.Txn.TXN_DATE].astype(str)
    return rejected[dates < overlap_day]


@dataclass(frozen=True)
class ImportReview:
    """What one import, or one part of it, leaves for the update report.

    Attributes:
        concerns: Cocerns that need to be reviewed by hand.
        pending: One-line notes on what a later update resolves on its own.
        expected: One-line notes on what was expected and needs nothing.
    """

    concerns: tuple[Concern, ...] = ()
    pending: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()


def review_import(
    filename: str,
    results: ImportResults,
    overlap_day: str | None,
) -> ImportReview:
    """Review one file's import.

    Args:
        filename: The file imported.
        results: What the import pipeline produced.
        overlap_day: The broker's latest stored transaction date before this run, or
            None when the file is not a broker download.

    Returns:
        The concerns, and the pending and expected notes, in the order found.
    """
    parts = (
        _review_cancellations(filename, results.cancel_events),
        _review_invalid(filename, results),
        _review_intra_duplicates(filename, results.intra_rejected_df),
        _review_db_duplicates(filename, results.db_rejected_df, overlap_day),
        _review_merges(filename, results.merge_events),
        _review_tally(filename, results),
    )
    return ImportReview(
        concerns=tuple(concern for part in parts for concern in part.concerns),
        pending=tuple(line for part in parts for line in part.pending),
        expected=tuple(line for part in parts for line in part.expected),
    )


def _review_cancellations(
    filename: str,
    events: Iterable[CancelEvent],
) -> ImportReview:
    """Note each cancellation that voided a row in the same file."""
    return ImportReview(
        expected=tuple(
            f"{filename}: cancelled by the broker, both left out: "
            f"{_row_summary(event.cancelled)}"
            for event in events
        ),
    )


def _review_invalid(filename: str, results: ImportResults) -> ImportReview:
    """Split invalid rows into header lines, unpaid dividends and real rows.

    An unpaid dividend is pending: a later download brings it paid.
    """
    invalid = results.invalid_df
    if invalid.empty:
        return ImportReview()
    headers = header_rows(invalid, results.read_df.columns)
    expected = (
        (f"{filename}: {int(headers.sum())} repeated header row(s)",)
        if headers.any()
        else ()
    )
    # A cancellation is never a dividend waiting to pay, whatever its action.
    nothing_to_void = invalid[Column.REJECTION_REASON] == NOTHING_TO_VOID
    unpaid = upcoming_dividends(invalid) & ~headers & ~nothing_to_void
    pending = tuple(
        f"Dividend announced, not paid yet: {_row_summary(row)}"
        for row in invalid[unpaid].to_dict("records")
    )
    concerns = _cancellations_with_nothing_to_void(filename, invalid[nothing_to_void])
    real = invalid[~headers & ~unpaid & ~nothing_to_void]
    if real.empty:
        return ImportReview(concerns=concerns, pending=pending, expected=expected)
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(real)} invalid row(s) rejected from {filename}",
        why=(
            "These rows failed validation and are not in the folio. If one is "
            "genuine, add it by hand, or fix the source and re-import it. A bare "
            "transfer may instead arrive with a later monthly statement."
        ),
        rows=real[_essentials(real)],
        details=_reasons(real),
        commands=tuple(add_command(row) for _, row in real.iterrows()),
    )
    return ImportReview(
        concerns=(*concerns, concern),
        pending=pending,
        expected=expected,
    )


def _cancellations_with_nothing_to_void(
    filename: str,
    rows: pd.DataFrame,
) -> tuple[Concern, ...]:
    """Raise cancellations rejected for having nothing in their file to void.

    Each never becomes a transaction, so `folio add` is no fix: the row it
    cancels may be in the folio already, from an earlier import.
    """
    if rows.empty:
        return ()
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(rows)} cancellation(s) with nothing to void in {filename}",
        why=(
            "The broker cancelled a row that is not in this file. If that row "
            "was imported earlier, find it with `folio query` and delete it. If "
            "the rejected row is a genuine transaction, add it by hand."
        ),
        rows=rows[_essentials(rows)],
    )
    return (concern,)


def _review_intra_duplicates(filename: str, intra: pd.DataFrame) -> ImportReview:
    """Raise rows rejected for repeating within the file."""
    if intra.empty:
        return ImportReview()
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(intra)} identical row(s) rejected from {filename}",
        why=(
            "Rows that repeat within one file are all rejected, every copy. If "
            "they were separate fills of the same order, add each back."
        ),
        rows=intra,
        commands=tuple(add_command(row, force=True) for _, row in intra.iterrows()),
    )
    return ImportReview(concerns=(concern,))


def _review_db_duplicates(
    filename: str,
    duplicates: pd.DataFrame,
    overlap_day: str | None,
) -> ImportReview:
    """Note duplicates from the overlap day; raise older ones."""
    if duplicates.empty:
        return ImportReview()
    old = before_overlap_day(duplicates, overlap_day)
    overlapping = len(duplicates) - len(old)
    expected = (
        (f"{filename}: {overlapping} row(s) re-sent for the overlap day",)
        if overlapping
        else ()
    )
    if old.empty:
        return ImportReview(expected=expected)
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(old)} older row(s) in {filename} already in the folio",
        why=(
            "These predate the broker's latest stored transaction, so the file "
            "overlaps an earlier import. They were rejected. Only a genuine "
            "repeat trade needs adding back."
        ),
        rows=old,
        commands=tuple(add_command(row, force=True) for _, row in old.iterrows()),
    )
    return ImportReview(concerns=(concern,), expected=expected)


def _review_merges(filename: str, events: Iterable[MergeEvent]) -> ImportReview:
    """Raise merges whose shape suggests the wrong rows were combined."""
    odd = odd_merges(events)
    if not odd:
        return ImportReview()
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(odd)} unusual merge(s) in {filename}",
        why=(
            "A merge folded in a repeated action, or netted negative from a "
            "positive payment, which usually means a correction or reversal was "
            "combined with the original. These are the source rows."
        ),
        rows=pd.concat([event.source_rows for event in odd], ignore_index=True),
        commands=tuple(
            f"folio query {e.merged_row.get(Column.Txn.TICKER, '')} "
            f"{e.merged_row.get(Column.Txn.TXN_DATE, '')}".strip()
            for e in odd
        ),
    )
    return ImportReview(concerns=(concern,))


def _review_tally(filename: str, results: ImportResults) -> ImportReview:
    """Raise an import whose rows do not add up stage by stage."""
    if results.tally_matches():
        return ImportReview()
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"Import tally does not add up for {filename}",
        why=(
            f"{results.imported_count()} row(s) imported where the stages account "
            f"for {results.expected_count()}. Compare the file with logs/audit.log "
            "to find the rows that went astray."
        ),
    )
    return ImportReview(concerns=(concern,))


def transfer_like_pairs(
    txns: Sequence[Txn],
    new_ids: Iterable[int],
    days: int = LOOKALIKE_TRANSFER_DAYS,
) -> pd.DataFrame:
    """Find withdrawals and contributions that look like one transfer.

    Args:
        txns: Every stored transaction.
        new_ids: TxnIds added by this run.
        days: Largest gap between the two legs.

    Returns:
        One row per pair: both TxnIds, dates, accounts, currency and amount.
    """
    out_cols = ["Out", "In", "Date Out", "Date In", "From", "To", "$", "Amount"]
    new = set(new_ids)
    by_id = sorted(txns, key=lambda txn: txn.txn_id)
    pairs = [
        _pair_row(out, into)
        for out in by_id
        if out.action is Action.WITHDRAWAL and out.amount is not None
        for into in by_id
        if into.action is Action.CONTRIBUTION
        and into.amount is not None
        and _is_transfer_like(out, into, days)
        and {out.txn_id, into.txn_id} & new
    ]
    return pd.DataFrame(pairs, columns=out_cols)


def _is_transfer_like(out: Txn, into: Txn, days: int) -> bool:
    """Whether a withdrawal and a contribution could be one transfer."""
    day_out, day_in = iso_day(out.txn_date), iso_day(into.txn_date)
    return (
        out.currency is into.currency
        and out.account != into.account
        and abs(abs(float(out.amount or 0)) - abs(float(into.amount or 0)))
        < _AMOUNT_TOLERANCE
        and day_out is not None
        and day_in is not None
        and abs((day_out - day_in).days) <= days
    )


def _pair_row(out: Txn, into: Txn) -> list[object]:
    """Describe a withdrawal and contribution pair on one row."""
    return [
        out.txn_id,
        into.txn_id,
        out.txn_date,
        into.txn_date,
        out.account,
        into.account,
        str(out.currency),
        abs(float(out.amount or 0)),
    ]


def review_transfer_pairs(
    txns: Sequence[Txn],
    new_ids: Iterable[int],
) -> Concern | None:
    """Raise the withdrawal/contribution pairs that look like transfers.

    Args:
        txns: Every stored transaction.
        new_ids: TxnIds added by this run.

    Returns:
        The concern, or None when no pair was found.
    """
    pairs = transfer_like_pairs(txns, new_ids)
    if pairs.empty:
        return None
    commands: list[str] = []
    for _, pair in pairs.iterrows():
        commands.append(f"folio edit {pair['Out']} --set Action=TFR_OUT")
        commands.append(f"folio edit {pair['In']} --set Action=TFR_IN")
    return Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(pairs)} withdrawal/contribution pair(s) look like transfers",
        why=(
            "The same amount left one of your accounts and arrived in another. "
            "As a WITHDRAWAL and CONTRIBUTION it counts as money leaving and "
            "re-entering the folio. If it was a transfer, relabel both legs."
        ),
        rows=pairs,
        commands=tuple(commands),
    )


# -- STATEMENTS -----------------------------------------------------------------


def _month(settle_date: object) -> str | None:
    """Return the YYYY-MM a date falls in, or None when it is not a date."""
    parsed = pd.to_datetime(str(settle_date), errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime(_MONTH)


def statement_months(settle_dates: Iterable[object], today: date) -> list[str]:
    """Return the months whose monthly statement could confirm given dates.

    Args:
        settle_dates: Calculated settlement dates still awaiting confirmation.
        today: The date the run happens on.

    Returns:
        Distinct YYYY-MM months, oldest first.
    """
    current = today.strftime(_MONTH)
    months = {_month(value) for value in settle_dates}
    return sorted(m for m in months if m is not None and m < current)


def split_remaining(
    calculated: Sequence[Txn],
    months_on_hand: Iterable[str],
    today: date,
) -> tuple[list[Txn], list[Txn]]:
    """Split the calculated dates left after an update into stuck and pending.

    A date is stuck when its month's statement is already on hand and still
    could not confirm it: no later run will either. Anything else waits on a
    statement that is not out yet.

    Args:
        calculated: Transactions whose settlement date is still calculated.
        months_on_hand: Months with a statement file stored locally.
        today: The date the run happens on.

    Returns:
        The stuck transactions, then the pending ones.
    """
    current = today.strftime(_MONTH)
    on_hand = set(months_on_hand)
    stuck: list[Txn] = []
    pending: list[Txn] = []
    for txn in calculated:
        month = _month(txn.settle_date) or ""
        (stuck if month in on_hand and month < current else pending).append(txn)
    return stuck, pending


def review_statement(
    filename: str,
    matches: Iterable[SettlementMatch],
    transfers_rejected: int,
) -> list[Concern]:
    """Review one statement's import.

    Args:
        filename: The statement imported.
        matches: Every row weighed for a settlement date.
        transfers_rejected: Transfers the statement held that could not be made.

    Returns:
        The concerns: unplaced rows and rejected transfers.
    """
    concerns: list[Concern] = []
    confirmed = (SettlementOutcome.CONFIRMED, SettlementOutcome.ALREADY_CONFIRMED)
    unplaced = [m for m in matches if m.outcome not in confirmed]
    if unplaced:
        rows = pd.DataFrame(
            {
                "Result": [
                    f"ambiguous ({m.candidates})"
                    if m.outcome is SettlementOutcome.AMBIGUOUS
                    else "no match"
                    for m in unplaced
                ],
                str(Column.Txn.TXN_DATE): [m.txn_date for m in unplaced],
                "Settles": [m.settle_date for m in unplaced],
                str(Column.Txn.ACTION): [m.action for m in unplaced],
                str(Column.Txn.TICKER): [m.ticker for m in unplaced],
                str(Column.Txn.AMOUNT): [m.amount for m in unplaced],
                str(Column.Txn.ACCOUNT): [m.account or "" for m in unplaced],
            },
        )
        commands: list[str] = []
        for m in unplaced:
            commands.append(f"folio query {m.ticker} {m.txn_date}")
            commands.append(f"folio edit <TxnId> --set SettleDate={m.settle_date}")
        concerns.append(
            Concern(
                stage=UpdateStage.STATEMENTS,
                title=f"{len(unplaced)} statement row(s) in {filename} placed nowhere",
                why=(
                    "No single calculated transaction matched these rows, often a "
                    "ticker spelled differently or two identical trades. Find the "
                    "transaction and set its settlement date by hand."
                ),
                rows=rows,
                commands=tuple(commands),
            ),
        )

    if transfers_rejected:
        concerns.append(
            Concern(
                stage=UpdateStage.STATEMENTS,
                title=f"{transfers_rejected} transfer(s) in {filename} not created",
                why=(
                    "The statement lists transfers that could not be turned into "
                    "transactions; logs/audit.log names them. Add each by hand."
                ),
                commands=(
                    (
                        "folio add -t TFR_IN -d <date> -a <account> -c <currency> "
                        "-s <ticker> -m <amount> -u <units>"
                    ),
                ),
            ),
        )
    return concerns


def review_stuck(stuck: Sequence[Txn]) -> Concern | None:
    """Raise the calculated dates their own month's statement could not confirm.

    Args:
        stuck: Rows from `split_remaining`.

    Returns:
        The concern, or None when nothing is stuck.
    """
    if not stuck:
        return None
    return Concern(
        stage=UpdateStage.STATEMENTS,
        title=f"{len(stuck)} settlement date(s) the statements could not confirm",
        why=(
            "Their month's statement is already imported but has no row for "
            "them. Check the broker and set the date by hand."
        ),
        rows=txns_frame(stuck),
        commands=tuple(
            f"folio edit {txn.txn_id} --set SettleDate=<date>" for txn in stuck
        ),
    )


# -- CHECK AND GENERATE ---------------------------------------------------------


def review_checks(results: Iterable[CheckResult]) -> tuple[list[Concern], int]:
    """Raise every failing health check.

    Args:
        results: Every check that ran.

    Returns:
        One concern per failing check, and how many checks only warned.
    """
    concerns: list[Concern] = []
    warned = 0
    for result in results:
        if result.status is CheckStatus.WARN:
            warned += 1
        if result.status is not CheckStatus.FAIL:
            continue
        concerns.append(
            Concern(
                stage=UpdateStage.CHECK,
                title=f"{result.name}: {result.summary}",
                why="A health check failed; the folio needs a correction.",
                details=tuple(f"{f.subject}: {f.detail}" for f in result.findings),
                commands=(f"folio check --only {result.slug}",),
            ),
        )
    return concerns, warned


def review_unpriced(symbols: Iterable[str]) -> Concern | None:
    """Raise open holdings for which no quote could be found.

    Args:
        symbols: Held symbols left unpriced by the valuation.

    Returns:
        The concern, or None when every holding was priced.
    """
    unpriced = sorted(set(symbols))
    if not unpriced:
        return None
    return Concern(
        stage=UpdateStage.GENERATE,
        title=f"{len(unpriced)} open holding(s) unpriced: {', '.join(unpriced)}",
        why=(
            "These are left out of every market total. A symbol that trades "
            "under another listing (a .TO that is really a .V) needs a Ticker "
            "transform in config.yaml or an alias, and its rows corrected."
        ),
        commands=(
            *(f"folio ticker {symbol}" for symbol in unpriced),
            "folio symbol --add <OLD> <NEW> <YYYY-MM-DD>",
        ),
    )
