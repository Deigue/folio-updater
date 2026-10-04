"""Reviewing the effects of what `folio update` run did.

Every stage of an update prints its own audit. These rules read those
results and pick out noticable anomolies afterwards: rows that never made it in,
rows that arrived twice, cash moves that were really transfers, settlement dates no
statement could confirm. Each is labeled a `Concern` carrying the rows involved and
the commands required to fix them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from domain import TXN_ESSENTIALS, Action, CheckStatus, Column, SettlementOutcome
from models import Concern, UpdateStage

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from engine.checks import CheckResult
    from models import CancelEvent, ImportResults, MergeEvent, SettlementMatch

# Cash moves between two of the user's own accounts land a few days apart at most.
TRANSFER_WINDOW_DAYS = 3
# How far back a late cancellation looks for the stored row it voids.
CANCEL_WINDOW_DAYS = 31
_AMOUNT_TOLERANCE = 0.01
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


def _present(value: object) -> bool:
    """Report whether a cell holds something, treating blanks as missing."""
    return not pd.isna(value) and str(value).strip() != ""  # ty: ignore[no-matching-overload]


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
        if _present(value):
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


def header_rows(excluded: pd.DataFrame, source_columns: Iterable[str]) -> pd.Series:
    """Mark excluded rows that are repeated header lines from the source file.

    Args:
        excluded: Rows the formatter excluded.
        source_columns: Column names of the file as read.

    Returns:
        A boolean mask over `excluded`, True for header rows.
    """
    if excluded.empty:
        return pd.Series(dtype=bool)
    names = {str(name).strip() for name in source_columns}
    essentials = _essentials(excluded)

    def is_header(row: pd.Series) -> bool:
        values = [str(row[c]).strip() for c in essentials if _present(row[c])]
        return bool(values) and all(value in names for value in values)

    return excluded.apply(is_header, axis=1).astype(bool)


def upcoming_dividends(excluded: pd.DataFrame) -> pd.Series:
    """Mark excluded dividends that carry no amount yet.

    A broker can list a dividend once its ex-dividend date passes, before the
    payment lands, with no amount (Wealthsimple does). The import rightly turns
    it away; the paid dividend arrives in a later download.

    Args:
        excluded: Rows the formatter excluded.

    Returns:
        A boolean mask over `excluded`, True for announced, unpaid dividends.
    """
    amounts = pd.to_numeric(
        excluded.get(Column.Txn.AMOUNT, pd.Series(index=excluded.index)),
        errors="coerce",
    )
    dividend = excluded[Column.Txn.ACTION].astype(str).str.upper() == Action.DIVIDEND
    return dividend & (amounts.isna() | (amounts == 0))


def _reasons(rows: pd.DataFrame) -> tuple[str, ...]:
    """Say why each excluded row was turned away, one line per row."""
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
        str(row.get(column)).strip() for column in columns if _present(row.get(column))
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


def old_db_dupes(rejected: pd.DataFrame, since: str | None) -> pd.DataFrame:
    """Return database duplicates dated before the broker's resume date.

    A download restarts on the day of the broker's latest stored transaction,
    so that day always arrives twice and is rightly rejected. A duplicate from
    any earlier day means an old or overlapping file was imported.

    Args:
        rejected: Rows rejected as already in the database.
        since: The broker's latest stored transaction date before this run, or
            None when the file is not a broker download.

    Returns:
        The rejected rows that are not explained by the resume day.
    """
    if rejected.empty or since is None:
        return rejected
    dates = rejected[Column.Txn.TXN_DATE].astype(str)
    return rejected[dates < since]


# A review of one part of an import: its concerns, and notes on expected noise.
_Review = tuple[list[Concern], list[str]]


def review_import(
    filename: str,
    results: ImportResults,
    since: str | None,
) -> _Review:
    """Review one file's import.

    Args:
        filename: The file imported.
        results: What the import pipeline produced.
        since: The broker's latest stored transaction date before this run.

    Returns:
        The concerns, and one-line notes for what was expected noise.
    """
    concerns: list[Concern] = []
    noise: list[str] = []
    for part_concerns, part_noise in (
        _review_cancellations(filename, results.cancel_events),
        _review_excluded(filename, results),
        _review_intra_dupes(filename, results.intra_rejected_df),
        _review_db_dupes(filename, results.db_rejected_df, since),
        _review_merges(filename, results.merge_events),
        _review_tally(filename, results),
    ):
        concerns.extend(part_concerns)
        noise.extend(part_noise)
    return concerns, noise


def _review_cancellations(filename: str, events: Iterable[CancelEvent]) -> _Review:
    """Note each cancellation that voided a row in the same file."""
    return [], [
        f"{filename}: cancelled by the broker, both left out: "
        f"{_row_summary(event.cancelled)}"
        for event in events
        if event.cancelled is not None
    ]


def _review_excluded(filename: str, results: ImportResults) -> _Review:
    """Split exclusions into header lines, unpaid dividends and real rows."""
    excluded = results.excluded_df
    if excluded.empty:
        return [], []
    noise: list[str] = []
    headers = header_rows(excluded, results.read_df.columns)
    if headers.any():
        noise.append(f"{filename}: {int(headers.sum())} repeated header row(s)")
    unpaid = upcoming_dividends(excluded) & ~headers
    noise.extend(
        f"Dividend announced, not paid yet: {_row_summary(row)}"
        for row in excluded[unpaid].to_dict("records")
    )
    real = excluded[~headers & ~unpaid]
    if real.empty:
        return [], noise
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(real)} row(s) excluded from {filename}",
        why=(
            "These rows failed validation and are not in the folio. If one is "
            "genuine, add it by hand, or fix the source and re-import it. A bare "
            "transfer may instead arrive with a later monthly statement."
        ),
        rows=real[_essentials(real)],
        details=_reasons(real),
        commands=tuple(add_command(row) for _, row in real.iterrows()),
    )
    return [concern], noise


def _review_intra_dupes(filename: str, intra: pd.DataFrame) -> _Review:
    """Raise rows dropped for repeating within the file."""
    if intra.empty:
        return [], []
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(intra)} identical row(s) dropped from {filename}",
        why=(
            "Rows that repeat within one file are all dropped, every copy. If "
            "they were separate fills of the same order, add each back."
        ),
        rows=intra,
        commands=tuple(add_command(row, force=True) for _, row in intra.iterrows()),
    )
    return [concern], []


def _review_db_dupes(
    filename: str,
    db_dupes: pd.DataFrame,
    since: str | None,
) -> _Review:
    """Note duplicates of the resume day; raise older ones."""
    if db_dupes.empty:
        return [], []
    old = old_db_dupes(db_dupes, since)
    expected = len(db_dupes) - len(old)
    noise = [f"{filename}: {expected} row(s) re-sent for the resume day"] * bool(
        expected,
    )
    if old.empty:
        return [], noise
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"{len(old)} older row(s) in {filename} already in the folio",
        why=(
            "These predate the broker's latest stored transaction, so the file "
            "overlaps an earlier import. They were skipped. Only a genuine "
            "repeat trade needs adding back."
        ),
        rows=old,
        commands=tuple(add_command(row, force=True) for _, row in old.iterrows()),
    )
    return [concern], noise


def _review_merges(filename: str, events: Iterable[MergeEvent]) -> _Review:
    """Raise merges whose shape suggests the wrong rows were combined."""
    odd = odd_merges(events)
    if not odd:
        return [], []
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
    return [concern], []


def _review_tally(filename: str, results: ImportResults) -> _Review:
    """Raise an import whose rows do not add up stage by stage."""
    if results.tally_matches():
        return [], []
    concern = Concern(
        stage=UpdateStage.IMPORT,
        title=f"Import tally does not add up for {filename}",
        why=(
            f"{results.imported_count()} row(s) imported where the stages account "
            f"for {results.expected_count()}. Compare the file with importer.log "
            "to find the rows that went astray."
        ),
    )
    return [concern], []


def review_late_cancellations(
    filename: str,
    events: Iterable[CancelEvent],
    txns: pd.DataFrame,
) -> list[Concern]:
    """Raise cancellations whose voided row was not in the same file.

    The row such a cancellation voids was most likely stored by an earlier
    import. Stored rows on the same account and currency, with the opposite
    amount, from the month before the cancellation, are offered for deletion.

    Args:
        filename: The file the cancellations came from.
        events: Cancellations found while importing it.
        txns: Every stored transaction.

    Returns:
        One concern per cancellation that voided nothing in its file.
    """
    concerns: list[Concern] = []
    for event in events:
        if event.cancelled is not None:
            continue
        row = event.cancellation
        candidates = _cancelled_candidates(row, txns)
        found = not candidates.empty
        concerns.append(
            Concern(
                stage=UpdateStage.IMPORT,
                title=f"Cancellation in {filename}: {_row_summary(row)}",
                why=(
                    "The broker cancelled a row that was not in this file, so it "
                    "is probably already in the folio. Delete the one it cancels "
                    "(the earliest, if several are listed)."
                    if found
                    else "The broker cancelled a row that is neither in this file "
                    "nor in the folio. Check the broker's activity for it."
                ),
                rows=candidates,
                commands=tuple(
                    f"folio delete {int(txn_id)}"
                    for txn_id in candidates.get(Column.Txn.TXN_ID, [])
                ),
            ),
        )
    return concerns


def _cancelled_candidates(row: dict[str, object], txns: pd.DataFrame) -> pd.DataFrame:
    """Find stored rows a cancellation could void.

    Args:
        row: The cancelling row.
        txns: Every stored transaction.

    Returns:
        Matching stored rows, earliest first.
    """
    amount = pd.to_numeric(pd.Series([row.get(Column.Txn.AMOUNT)]), errors="coerce")
    when = pd.to_datetime(str(row.get(Column.Txn.TXN_DATE)), errors="coerce")
    if txns.empty or amount.isna().iloc[0] or pd.isna(when):
        return txns.iloc[0:0]
    stored = pd.to_numeric(txns[Column.Txn.AMOUNT], errors="coerce")
    days = pd.to_datetime(txns[Column.Txn.TXN_DATE], errors="coerce")
    match = (
        ((stored + amount.iloc[0]).abs() < _AMOUNT_TOLERANCE)
        & (days <= when)
        & (days >= when - pd.Timedelta(days=CANCEL_WINDOW_DAYS))
    )
    for column in (Column.Txn.ACCOUNT, Column.Txn.CURRENCY):
        if _present(row.get(column)):
            match &= txns[column].astype(str) == str(row.get(column))
    return txns[match].sort_values(Column.Txn.TXN_DATE, kind="stable")


def transfer_like_pairs(
    txns: pd.DataFrame,
    new_ids: Iterable[int],
    days: int = TRANSFER_WINDOW_DAYS,
) -> pd.DataFrame:
    """Find withdrawals and contributions that look like one internal transfer.

    Args:
        txns: Every stored transaction.
        new_ids: TxnIds added by this run.
        days: Largest gap between the two legs.

    Returns:
        One row per pair: both TxnIds, dates, accounts, currency and amount.
    """
    columns = [
        Column.Txn.TXN_ID,
        Column.Txn.TXN_DATE,
        Column.Txn.ACCOUNT,
        Column.Txn.CURRENCY,
        Column.Txn.AMOUNT,
    ]
    out_cols = ["Out", "In", "Date Out", "Date In", "From", "To", "$", "Amount"]
    new = set(new_ids)
    if txns.empty or not new:
        return pd.DataFrame(columns=out_cols)

    action = txns[Column.Txn.ACTION]
    outs = txns.loc[action == Action.WITHDRAWAL, columns].copy()
    ins = txns.loc[action == Action.CONTRIBUTION, columns].copy()
    for frame in (outs, ins):
        frame["_abs"] = pd.to_numeric(frame[Column.Txn.AMOUNT], errors="coerce").abs()
        frame["_day"] = pd.to_datetime(frame[Column.Txn.TXN_DATE], errors="coerce")

    pairs = outs.merge(ins, on=Column.Txn.CURRENCY, suffixes=("_out", "_in"))
    if pairs.empty:
        return pd.DataFrame(columns=out_cols)
    keep = (
        ((pairs["_abs_out"] - pairs["_abs_in"]).abs() < _AMOUNT_TOLERANCE)
        & (pairs[f"{Column.Txn.ACCOUNT}_out"] != pairs[f"{Column.Txn.ACCOUNT}_in"])
        & ((pairs["_day_out"] - pairs["_day_in"]).abs().dt.days <= days)
        & (
            pairs[f"{Column.Txn.TXN_ID}_out"].isin(new)
            | pairs[f"{Column.Txn.TXN_ID}_in"].isin(new)
        )
    )
    pairs = pairs[keep]
    return pd.DataFrame(
        {
            "Out": pairs[f"{Column.Txn.TXN_ID}_out"].astype(int),
            "In": pairs[f"{Column.Txn.TXN_ID}_in"].astype(int),
            "Date Out": pairs[f"{Column.Txn.TXN_DATE}_out"],
            "Date In": pairs[f"{Column.Txn.TXN_DATE}_in"],
            "From": pairs[f"{Column.Txn.ACCOUNT}_out"],
            "To": pairs[f"{Column.Txn.ACCOUNT}_in"],
            "$": pairs[Column.Txn.CURRENCY],
            "Amount": pairs["_abs_out"],
        },
    ).reset_index(drop=True)


def review_transfer_pairs(txns: pd.DataFrame, new_ids: Iterable[int]) -> Concern | None:
    """Raise the withdrawal/contribution pairs that look like internal transfers.

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
            "re-entering the portfolio. If it was a transfer, relabel both legs."
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
    calculated: pd.DataFrame,
    months_on_hand: Iterable[str],
    today: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the calculated dates left after an update into stuck and pending.

    A date is stuck when its month's statement is already on hand and still
    could not confirm it: no later run will either. Anything else waits on a
    statement that is not out yet.

    Args:
        calculated: Transactions whose settlement date is still calculated.
        months_on_hand: Months with a statement file stored locally.
        today: The date the run happens on.

    Returns:
        The stuck rows, then the pending rows.
    """
    if calculated.empty:
        return calculated, calculated
    current = today.strftime(_MONTH)
    on_hand = set(months_on_hand)
    months = calculated[Column.Txn.SETTLE_DATE].map(_month).fillna("")
    stuck = months.isin(on_hand) & (months < current)
    return calculated[stuck], calculated[~stuck]


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
    settled = (SettlementOutcome.MATCHED, SettlementOutcome.ALREADY_SETTLED)
    unplaced = [m for m in matches if m.outcome not in settled]
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
                    "transactions; importer.log names them. Add each by hand."
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


def review_stuck(stuck: pd.DataFrame) -> Concern | None:
    """Raise the calculated dates their own month's statement could not confirm.

    Args:
        stuck: Rows from `split_remaining`.

    Returns:
        The concern, or None when nothing is stuck.
    """
    if stuck.empty:
        return None
    return Concern(
        stage=UpdateStage.STATEMENTS,
        title=f"{len(stuck)} settlement date(s) the statements could not confirm",
        why=(
            "Their month's statement is already imported but has no row for "
            "them. Check the broker and set the date by hand."
        ),
        rows=stuck,
        commands=tuple(
            f"folio edit {int(row[Column.Txn.TXN_ID])} --set SettleDate=<date>"
            for _, row in stuck.iterrows()
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
    """Raise held positions for which no quote could be found.

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
        title=f"{len(unpriced)} held position(s) unpriced: {', '.join(unpriced)}",
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
