"""Reviewing the effects of what `folio update` run did.

Every stage of an update prints its own audit. These rules read those
results and pick out noticable anomolies afterwards: rows that never made it in,
rows that arrived twice, cash moves that were really transfers, settlement dates no
statement could confirm. Each is labeled a `Concern` carrying the rows involved and
the commands required to fix them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from domain import TXN_ESSENTIALS, Action, CheckStatus, Column, SettlementOutcome
from models import Concern, UpdateStage

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from engine.checks import CheckResult
    from models import ImportResults, MergeEvent, SettlementMatch

# Cash moves between two of the user's own accounts land a few days apart at most.
TRANSFER_WINDOW_DAYS = 3
_AMOUNT_TOLERANCE = 0.01
_MONTH = "%Y-%m"

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
    """Spell out the `folio add` that would recreate a row.

    Values the row lacks become `<placeholders>` for the user to fill in.

    Args:
        row: The transaction to recreate.
        force: Add `--force`, for a row the folio would otherwise call a duplicate.

    Returns:
        The command, ready to copy.
    """
    parts = ["folio add"]
    for column, option in _ADD_OPTIONS:
        value = row.get(column)
        shown = str(value).strip() if _present(value) else f"<{column.lower()}>"
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


def review_import(
    filename: str,
    results: ImportResults,
    since: str | None,
) -> tuple[list[Concern], list[str]]:
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

    excluded = results.excluded_df
    if not excluded.empty:
        headers = header_rows(excluded, results.read_df.columns)
        if headers.any():
            noise.append(f"{filename}: {int(headers.sum())} repeated header row(s)")
        real = excluded[~headers]
        if not real.empty:
            concerns.append(
                Concern(
                    stage=UpdateStage.IMPORT,
                    title=f"{len(real)} row(s) excluded from {filename}",
                    why=(
                        "These rows failed validation and are not in the folio. "
                        "If one is genuine, add it by hand, or fix the source and "
                        "re-import it. A bare transfer may instead arrive with a "
                        "later monthly statement."
                    ),
                    rows=real,
                    commands=tuple(add_command(row) for _, row in real.iterrows()),
                ),
            )

    intra = results.intra_rejected_df
    if not intra.empty:
        concerns.append(
            Concern(
                stage=UpdateStage.IMPORT,
                title=f"{len(intra)} identical row(s) dropped from {filename}",
                why=(
                    "Rows that repeat within one file are all dropped, every copy. "
                    "If they were separate fills of the same order, add each back."
                ),
                rows=intra,
                commands=tuple(
                    add_command(row, force=True) for _, row in intra.iterrows()
                ),
            ),
        )

    db_dupes = results.db_rejected_df
    if not db_dupes.empty:
        old = old_db_dupes(db_dupes, since)
        expected = len(db_dupes) - len(old)
        if expected:
            noise.append(f"{filename}: {expected} row(s) re-sent for the resume day")
        if not old.empty:
            concerns.append(
                Concern(
                    stage=UpdateStage.IMPORT,
                    title=f"{len(old)} older row(s) in {filename} already in the folio",
                    why=(
                        "These predate the broker's latest stored transaction, so "
                        "the file overlaps an earlier import. They were skipped. "
                        "Only a genuine repeat trade needs adding back."
                    ),
                    rows=old,
                    commands=tuple(
                        add_command(row, force=True) for _, row in old.iterrows()
                    ),
                ),
            )

    odd = odd_merges(results.merge_events)
    if odd:
        rows = pd.concat([event.source_rows for event in odd], ignore_index=True)
        concerns.append(
            Concern(
                stage=UpdateStage.IMPORT,
                title=f"{len(odd)} unusual merge(s) in {filename}",
                why=(
                    "A merge folded in a repeated action, or netted negative from "
                    "a positive payment, which usually means a correction or "
                    "reversal was combined with the original. These are the "
                    "source rows."
                ),
                rows=rows,
                commands=tuple(
                    f"folio query {e.merged_row.get(Column.Txn.TICKER, '')} "
                    f"{e.merged_row.get(Column.Txn.TXN_DATE, '')}".strip()
                    for e in odd
                ),
            ),
        )

    if not results.tally_matches():
        concerns.append(
            Concern(
                stage=UpdateStage.IMPORT,
                title=f"Import tally does not add up for {filename}",
                why=(
                    f"{results.imported_count()} row(s) imported where the stages "
                    f"account for {results.expected_count()}. Compare the file "
                    "with importer.log to find the rows that went astray."
                ),
            ),
        )

    return concerns, noise


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
        rows = pd.DataFrame(
            {
                "Subject": [f.subject for f in result.findings],
                "Detail": [f.detail for f in result.findings],
            },
        )
        concerns.append(
            Concern(
                stage=UpdateStage.CHECK,
                title=f"{result.name}: {result.summary}",
                why="A health check failed; the folio needs a correction.",
                rows=rows,
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
