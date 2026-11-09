"""Health checks over a replay, phrased for a reader rather than a machine.

Every check here is a by-product of the replay the cost-base engine already
performs, so this module does **no database access of its own**. If a check
needs a fact the replay does not carry, the fix belongs in `engine/replay.py`,
not in a query here.

The engine's own `detail` strings name diagnostics and pool grains. Nothing in
this module repeats them: a reader is told what is wrong with their folio in
the words they would use themselves, and the vocabulary of the engine (pool,
grain, grain, code) never reaches them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol

from domain import Action, CheckStatus, Currency, DiagnosticCode, Grain
from domain.numeric import ZERO, q2
from engine.accounts import Pool
from engine.replay import DENOMINATED_ACTIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from decimal import Decimal

    from engine.ledger import Ledger
    from engine.types import Diagnostic, LedgerRow
    from models import Txn


class ChecksConfig(Protocol):
    """The settings the checks domain cares about."""

    @property
    def checks_disabled(self) -> list[str]:
        """Slugs of the checks that should not run."""
        ...

    @property
    def checks_ignore_tickers(self) -> list[str]:
        """Securities every check leaves alone, upper-cased."""
        ...

    @property
    def checks_ignore_accounts(self) -> list[str]:
        """Accounts every check leaves alone, upper-cased."""
        ...


@dataclass(frozen=True)
class CheckFinding:
    """One thing a check found.

    Attributes:
        subject: What the line is about: a ticker, an account or a TxnId.
        detail: The sentence shown to the user. May span several lines where a
            finding has figures worth aligning.
        txn_ids: Transactions behind the finding, for `--only` and `--json`.
    """

    subject: str
    detail: str
    txn_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """The results for a single check.

    Attributes:
        name: The heading, as printed.
        slug: Stable name for `--only` and for `checks.disabled`.
        status: Whether it passed, is worth a look, or is wrong.
        summary: The one line printed beside the heading.
        findings: The detail lines, printed only when the check did not pass.
    """

    name: str
    slug: str
    status: CheckStatus
    summary: str
    findings: tuple[CheckFinding, ...] = ()


@dataclass(frozen=True)
class CheckReport:
    """Every check that ran, with the tallies a report or a script reads.

    Attributes:
        results: One result per check that ran.
    """

    results: tuple[CheckResult, ...]

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        """The checks that found something wrong."""
        return tuple(r for r in self.results if r.status is CheckStatus.FAIL)

    @property
    def warned(self) -> tuple[CheckResult, ...]:
        """The checks that found something worth a look."""
        return tuple(r for r in self.results if r.status is CheckStatus.WARN)

    def to_json(self) -> str:
        """Serialise the report for a script to read."""
        return json.dumps(
            {
                "checks": [
                    {**asdict(result), "status": str(result.status)}
                    for result in self.results
                ],
                "failed": len(self.failed),
                "warnings": len(self.warned),
            },
            indent=2,
        )


class UnknownCheckError(ValueError):
    """Raised when a configured or requested check slug does not exist."""

    def __init__(self, slug: str, known: Iterable[str]) -> None:
        """Name the bad slug and every slug that would have worked."""
        super().__init__(
            f"There is no check called '{slug}'. The checks are: "
            f"{', '.join(sorted(known))}.",
        )
        self.slug = slug


# --- FORMATTING ---------------------------------------------------------------


def _money(value: Decimal, currency: Currency | None = None) -> str:
    """Render an amount the way a statement would, with its currency."""
    text = f"{q2(value):,.2f}"
    return f"{text} {currency}" if currency else text


def _units(value: Decimal) -> str:
    """Render units without the trailing zeros a whole number never needs."""
    text = f"{value:,.6f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """Render a count with the right noun after it."""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _joined(names: Sequence[str]) -> str:
    """Render a list of names as English rather than as a Python list."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


class _LedgerIndex:
    """The ledger's diagnostics, cash and row counts, indexed for the checks."""

    def __init__(self, ledger: Ledger, config: ChecksConfig) -> None:
        self.ledger = ledger
        self.rows: Mapping[int, LedgerRow] = ledger.diagnosed_rows
        self._ignored_tickers = set(config.checks_ignore_tickers)
        self._ignored_accounts = set(config.checks_ignore_accounts)
        self.accounts = sorted(
            account
            for account in ledger.totals.accounts()
            if not self._account_ignored(account)
        )
        self._by_code: dict[DiagnosticCode, list[Diagnostic]] = {}
        # Indexed rather than searched. `fired_at` is asked, per diagnostic,
        # whether a code also fired at another grain, so scanning the list each
        # time makes the whole check quadratic in its own diagnostics, which
        # bites hardest on exactly the badly-broken folio that raises the most.
        self._fired: set[tuple[DiagnosticCode, Grain | None, int | None]] = set()
        for diagnostic in ledger.diagnostics:
            if self._suppressed(diagnostic):
                continue
            self._by_code.setdefault(diagnostic.code, []).append(diagnostic)
            self._fired.add((diagnostic.code, diagnostic.grain, diagnostic.txn_id))

    def _account_ignored(self, account: str | None) -> bool:
        return bool(account) and str(account).upper() in self._ignored_accounts

    def _suppressed(self, diagnostic: Diagnostic) -> bool:
        """Whether config says to leave this diagnostic alone."""
        return self._account_ignored(self.account_of(diagnostic)) or (
            (self.symbol_of(diagnostic) or "").upper() in self._ignored_tickers
        )

    def account_of(self, diagnostic: Diagnostic) -> str | None:
        """Return the account a diagnostic is about, falling back to its row's."""
        if diagnostic.account:
            return diagnostic.account
        computed = self.row_of(diagnostic)
        return computed.row.account if computed else None

    def symbol_of(self, diagnostic: Diagnostic) -> str | None:
        """Return the security a diagnostic is about, falling back to its row's."""
        if diagnostic.symbol:
            return diagnostic.symbol
        computed = self.row_of(diagnostic)
        return computed.symbol if computed else None

    def row_of(self, diagnostic: Diagnostic) -> LedgerRow | None:
        """Return the transaction a diagnostic tags, where it tags one."""
        if diagnostic.txn_id is None:
            return None
        return self.rows.get(diagnostic.txn_id)

    def having(self, *codes: DiagnosticCode) -> list[Diagnostic]:
        """Every surviving diagnostic carrying one of these codes."""
        diagnostics = []
        for code in codes:
            diagnostics.extend(self._by_code.get(code, []))
        return diagnostics

    def tagged(
        self,
        *codes: DiagnosticCode,
        grain: Grain | None = None,
    ) -> Iterator[tuple[Diagnostic, LedgerRow]]:
        """Pair each surviving diagnostic with the transaction it tags.

        Args:
            *codes: The diagnostic codes to read.
            grain: Keep only diagnostics at this pool grain.

        Yields:
            Each diagnostic with its transaction, in the order a reader scans
            them. These codes always tag a transaction, so none is skipped in
            practice.
        """
        diagnostics = self.having(*codes)
        if grain is not None:
            diagnostics = [
                diagnostic for diagnostic in diagnostics if diagnostic.grain is grain
            ]
        pairs = (
            (diagnostic, self.row_of(diagnostic))
            for diagnostic in _ordered(diagnostics)
        )
        yield from ((diagnostic, row) for diagnostic, row in pairs if row is not None)

    def at(self, grain: Grain, *codes: DiagnosticCode) -> list[Diagnostic]:
        """Every surviving diagnostic at one pool grain."""
        return [
            diagnostic
            for diagnostic in self.having(*codes)
            if diagnostic.grain is grain
        ]

    def fired_at(self, grain: Grain, code: DiagnosticCode, txn_id: int | None) -> bool:
        """Whether one code fired against one row at a given grain."""
        return (code, grain, txn_id) in self._fired

    def counting(self, *actions: Action) -> int:
        """How many surviving rows carry one of these actions."""
        return self.ledger.totals.counting(
            actions,
            self._ignored_accounts,
            self._ignored_tickers,
        )

    def currency_split(self, symbol: str) -> list[tuple[str, int]]:
        """Count how the given symbol's money rows divide across currencies.

        Only the actions whose `$` denominates real money are counted, matching
        what the engine judged.

        Args:
            symbol: The canonical security.

        Returns:
            `(currency, row count)` commonest first.
        """
        tally = self.ledger.totals.currencies_for(symbol, DENOMINATED_ACTIONS)
        return sorted(
            ((str(currency), count) for currency, count in tally.items()),
            key=lambda pair: (-pair[1], pair[0]),
        )

    def cash(self, account: str, currency: Currency) -> Decimal:
        """Cash state of an account for the given currency."""
        state = self.ledger.cash.get((Pool(Grain.ACCOUNT, account), currency))
        return state.cash if state else ZERO


def _ordered(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Sort findings the way a reader scans them: by row, then by pool."""
    return sorted(
        diagnostics,
        key=lambda diagnostic: (diagnostic.txn_id or 0, diagnostic.pool or ""),
    )


@dataclass(frozen=True)
class _Outcome:
    """What one check concluded, before `_CHECKS` gives it a name and a slug.

    Attributes:
        findings: The detail lines, empty when the check passed.
        passed: The summary printed when there are no findings.
        failed: The summary printed when there are some.
        status: The status of a check that has findings.
    """

    findings: list[CheckFinding]
    passed: str
    failed: str
    status: CheckStatus = CheckStatus.FAIL

    def to_result(self, name: str, slug: str) -> CheckResult:
        """Assemble the check's result, choosing its summary from the findings."""
        if not self.findings:
            return CheckResult(name, slug, CheckStatus.OK, self.passed)
        return CheckResult(name, slug, self.status, self.failed, tuple(self.findings))


# --- CHECK FUNCTIONS ---------------------------------------------------------


def _check_account_types(folio: _LedgerIndex) -> _Outcome:
    """Every account has to resolve to an account type before anything else counts."""
    findings = [
        CheckFinding(
            subject=str(diagnostic.account),
            detail=(
                f"{diagnostic.account} does not look like any account type. Rename it "
                f"<BROKER>-<TYPE>, or give it a type under accounts.map in "
                f"config.yaml."
            ),
        )
        for diagnostic in _ordered(folio.having(DiagnosticCode.UNKNOWN_ACCOUNT_TYPE))
    ]
    return _Outcome(
        findings,
        f"{_plural(len(folio.accounts), 'account')}, all types inferred",
        f"{_plural(len(findings), 'account has', 'accounts have')} no type",
    )


def _check_fee_conventions(folio: _LedgerIndex) -> _Outcome:
    """Report trades that fit neither fee convention, or go against their account's."""
    findings = [
        CheckFinding(
            subject=f"TxnId {computed.row.txn_id}",
            detail=(
                f"{computed.row.account} {computed.row.txn_date}\n{diagnostic.detail}."
            ),
            txn_ids=(computed.row.txn_id,),
        )
        for diagnostic, computed in folio.tagged(DiagnosticCode.FEE_CONVENTION_MISMATCH)
    ]
    for _diagnostic, computed in folio.tagged(DiagnosticCode.AMBIGUOUS_FEE_CONVENTION):
        row = computed.row
        units, price = row.units or ZERO, row.price or ZERO
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} {row.txn_date}\n"
                    f"recorded  {_money(row.amount or ZERO)} {row.currency}\n"
                    f"{_units(units)} x {price} = "
                    f"{_money(abs(units * price))}, and the "
                    f"{_money(abs(row.fee or ZERO))} fee does not close the gap either."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return _Outcome(
        findings,
        f"{_plural(len(folio.accounts), 'account')} agree with their own trades",
        f"{_plural(len(findings), 'trade does', 'trades do')} not add up",
        CheckStatus.WARN,
    )


def _shortfall_reason(folio: _LedgerIndex, diagnostic: Diagnostic) -> str:
    """Say where the missing units went, from the grains the sale broke.

    A sale that overdraws one account but not the account type is a transfer
    nobody wrote down. One that overdraws the type but not the folio crossed
    between types. One that overdraws the folio means the units were never
    there at all.
    """
    computed = folio.row_of(diagnostic)
    acct_type = computed.acct_type if computed else None
    if folio.fired_at(Grain.FOLIO, DiagnosticCode.OVERSELL, diagnostic.txn_id):
        return "Those units are missing from the folio entirely."
    if folio.fired_at(Grain.TYPE, DiagnosticCode.OVERSELL, diagnostic.txn_id):
        return (
            f"The {acct_type} total is short too, so this is either a transfer "
            f"between account types or a transaction that was never recorded."
        )
    return (
        f"The {acct_type} total is correct, so those units were most likely "
        f"transferred from another account without being recorded."
    )


def _check_unit_balances(folio: _LedgerIndex) -> _Outcome:
    """Report holdings that go negative: the proof that a row is missing."""
    findings = []
    explained: set[tuple[str | None, str | None]] = set()
    for diagnostic, computed in folio.tagged(
        DiagnosticCode.OVERSELL,
        grain=Grain.ACCOUNT,
    ):
        row = computed.row
        held = diagnostic.value if diagnostic.value is not None else ZERO
        sold = abs(row.units or ZERO)
        explained.add((diagnostic.symbol, row.account))
        findings.append(
            CheckFinding(
                subject=str(diagnostic.symbol or row.ticker),
                detail=(
                    f"{row.account} sold {_units(sold)} units on {row.txn_date} "
                    f"but only held {_units(max(held, ZERO))} there. "
                    f"{_units(sold - held)} units are unaccounted for. "
                    f"{_shortfall_reason(folio, diagnostic)}"
                ),
                txn_ids=(row.txn_id,),
            ),
        )

    # A holding left negative by a sale already reported above is the same
    # defect seen from the other end. Reporting it twice pads the count and
    # tells the reader nothing they were not just told.
    for diagnostic in _ordered(
        folio.at(Grain.ACCOUNT, DiagnosticCode.NEGATIVE_FINAL_UNITS),
    ):
        if (diagnostic.symbol, diagnostic.account) in explained:
            continue
        findings.append(
            CheckFinding(
                subject=str(diagnostic.symbol),
                detail=(
                    f"{diagnostic.account} ends holding "
                    f"{_units(diagnostic.value or ZERO)} "
                    f"units of it, which cannot happen. A purchase or a transfer "
                    f"in is missing."
                ),
            ),
        )

    subjects = {finding.subject for finding in findings}
    return _Outcome(
        findings,
        "every holding stays above zero",
        f"{_plural(len(subjects), 'security', 'securities')} never balance",
    )


def _check_cash_balances(folio: _LedgerIndex) -> _Outcome:
    """Report the first date each account's cash crosses below zero."""
    findings = []
    for diagnostic in _ordered(folio.having(DiagnosticCode.CASH_NEGATIVE)):
        account = str(diagnostic.account)
        currency = diagnostic.currency or Currency.CAD
        crossed = diagnostic.value if diagnostic.value is not None else ZERO
        ended = folio.cash(account, currency)
        if q2(ended) == q2(crossed):
            # A balance that never moves again is one past error, not a running
            # one, and saying so points at the date rather than at the figure.
            tail = (
                "and has sat there unchanged ever since, so one transaction "
                "around that date is wrong or missing."
            )
        else:
            tail = "so a transaction is missing on or before that date."
        findings.append(
            CheckFinding(
                subject=f"{account} {currency}",
                detail=(
                    f"first goes negative on {diagnostic.as_of}, at "
                    f"{_money(crossed, currency)}, {tail}"
                ),
                txn_ids=(diagnostic.txn_id,) if diagnostic.txn_id else (),
            ),
        )
    return _Outcome(
        findings,
        "no account ever goes below zero",
        f"{_plural(len(findings), 'account goes', 'accounts go')} negative",
    )


def _check_split_coverage(folio: _LedgerIndex) -> _Outcome:
    """Report splits missing from an account that held the security.

    A split reaches only its own account, so a missing row is silent forever.
    """
    missing: dict[tuple[str, str], list[str]] = {}
    for diagnostic in folio.having(DiagnosticCode.SPLIT_MISSING_ON_ACCOUNT):
        key = (str(diagnostic.symbol), str(diagnostic.as_of))
        missing.setdefault(key, []).append(str(diagnostic.account))

    findings = [
        CheckFinding(
            subject=symbol,
            detail=(
                f"split on {on}, but {_joined(sorted(accounts))} held it that day "
                f"and got no split row. Those units are still at their pre-split "
                f"count."
            ),
        )
        for (symbol, on), accounts in sorted(missing.items())
    ]
    duplicates = _ordered(folio.having(DiagnosticCode.DUPLICATE_SPLIT))
    findings.extend(
        CheckFinding(
            subject=str(diagnostic.symbol),
            detail=(
                f"is split twice on {diagnostic.as_of} in {diagnostic.account}. "
                f"One of the two rows is a duplicate."
            ),
            txn_ids=(diagnostic.txn_id,) if diagnostic.txn_id else (),
        )
        for diagnostic in duplicates
    )

    if not missing:
        failed = f"{_plural(len(duplicates), 'split is', 'splits are')} recorded twice"
    elif not duplicates:
        failed = (
            f"{_plural(len(missing), 'split is', 'splits are')} missing from an "
            f"account that held it"
        )
    else:
        failed = f"{_plural(len(findings), 'split is', 'splits are')} recorded wrong"

    return _Outcome(
        findings,
        f"{_plural(folio.counting(Action.SPLIT), 'split')}, every account got one",
        failed,
    )


def _check_income_sanity(folio: _LedgerIndex) -> _Outcome:
    """Income arriving where nothing is held usually means the holding is missing."""
    findings = []
    for diagnostic, computed in folio.tagged(DiagnosticCode.INCOME_WITHOUT_HOLDING):
        row = computed.row
        paid = "a return of capital" if row.action is Action.ROC else "a dividend"
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} received {paid} on {diagnostic.symbol} on "
                    f"{row.txn_date} while holding none of it. Either the "
                    f"purchase is missing or the income is on the wrong account."
                ),
                txn_ids=(row.txn_id,),
            ),
        )

    for diagnostic in _ordered(folio.at(Grain.ACCOUNT, DiagnosticCode.ROC_EXCEEDS_ACB)):
        findings.append(  # noqa: PERF401 - the two loops read different findings
            CheckFinding(
                subject=f"TxnId {diagnostic.txn_id}",
                detail=(
                    f"return of capital on {diagnostic.symbol} in {diagnostic.account} "
                    f"runs {_money(diagnostic.value or ZERO)} past what the holding "
                    f"cost. The excess becomes a realized gain, so check it is "
                    f"really a return of capital."
                ),
                txn_ids=(diagnostic.txn_id,) if diagnostic.txn_id else (),
            ),
        )
    return _Outcome(
        findings,
        "all income lands on a holding",
        f"{_plural(len(findings), 'income row needs', 'income rows need')} a look",
        CheckStatus.WARN,
    )


def _check_conversion_arithmetic(folio: _LedgerIndex) -> _Outcome:
    """Report conversions whose amount, units and rate disagree."""
    findings = []
    for _diagnostic, computed in folio.tagged(DiagnosticCode.FXT_AMOUNT_INCONSISTENT):
        row = computed.row
        amount, units, price = row.amount or ZERO, row.units or ZERO, row.price or ZERO
        # The engine compares magnitudes, so the implied figure is shown with
        # the recorded amount's sign.
        implied = abs(units * price) * (-1 if amount < ZERO else 1)
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} {row.txn_date}\n"
                    f"recorded   {_money(amount, row.currency)}\n"
                    f"{_units(units)} x {price} = {_money(implied)}\n"
                    f"Open the statement and see which of the three is right."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return _Outcome(
        findings,
        f"{_plural(folio.counting(Action.FXT), 'conversion')} all add up",
        f"{_plural(len(findings), 'conversion does', 'conversions do')} not add up",
    )


def _check_currency_consistency(folio: _LedgerIndex) -> _Outcome:
    """Report securities booked in more than one currency."""
    by_symbol: dict[str, list[Diagnostic]] = {}
    for diagnostic in folio.having(DiagnosticCode.MIXED_CURRENCY):
        by_symbol.setdefault(str(diagnostic.symbol), []).append(diagnostic)

    findings = []
    for symbol, diagnostics in sorted(by_symbol.items()):
        odd = _ordered(diagnostics)
        counts = [
            _plural(count, f"row of {name}")
            for name, count in folio.currency_split(symbol)
        ]
        findings.append(
            CheckFinding(
                subject=symbol,
                detail=(
                    f"is booked in {_joined(counts)}. A security trades in one "
                    f"currency and a dual listing has its own symbol, so the odd "
                    f"rows out are on the wrong one."
                ),
                txn_ids=tuple(w.txn_id for w in odd if w.txn_id is not None),
            ),
        )
    return _Outcome(
        findings,
        "no security is booked in more than one currency",
        f"{_plural(len(findings), 'security is', 'securities are')} booked in "
        f"more than one currency",
    )


def _check_settlement_dates(folio: _LedgerIndex) -> _Outcome:
    """Settlement lags are a broker habit, so a stray one is usually a typo."""
    findings = []
    for _diagnostic, computed in folio.tagged(DiagnosticCode.SETTLEMENT_BEFORE_TRADE):
        row = computed.row
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} settles {row.settle_date}, before the "
                    f"{row.txn_date} trade it settles."
                ),
                txn_ids=(row.txn_id,),
            ),
        )

    for _diagnostic, computed in folio.tagged(DiagnosticCode.SETTLEMENT_LAG_OUTLIER):
        row = computed.row
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} traded {row.txn_date} and settles "
                    f"{row.settle_date}, far longer than that account usually "
                    f"takes. Check the settlement date."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return _Outcome(
        findings,
        "every trade settles when it should",
        _plural(len(findings), "settlement date needs", "settlement dates need")
        + " a look",
        CheckStatus.WARN,
    )


def _what_moved(row: Txn, symbol: str | None) -> str:
    """Name what a transfer leg carried: `40 RY.TO` in kind, `600.00 CAD` in cash."""
    if row.is_in_kind_transfer:
        return f"{_units(abs(row.units or ZERO))} {symbol}"
    return _money(abs(row.amount or ZERO), row.currency)


def _unpaired_legs(folio: _LedgerIndex) -> list[CheckFinding]:
    """Report each transfer leg no other account records the matching side of."""
    findings = []
    for _diagnostic, computed in folio.tagged(DiagnosticCode.TRANSFER_UNPAIRED):
        row = computed.row
        leaving = row.action is Action.TFR_OUT
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{row.account} transferred {_what_moved(row, computed.symbol)} "
                    f"{'out on' if leaving else 'in on'} {row.txn_date}, and no "
                    f"other account records the matching side. Without it the "
                    f"cost base cannot follow the units across."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return findings


def _arrived_first(row: Txn, moved: str, arrived: str) -> str:
    """Say what an out leg coming after its in leg does to the figures."""
    dated_first = arrived < row.txn_date
    when = (
        f"on {row.txn_date}, after they arrived on {arrived}"
        if dated_first
        else f"settling {row.settle_date}, after they had arrived"
    )
    harm = (
        "their cost base is lost and their share of net deposits may be wrong"
        if dated_first and row.is_in_kind_transfer
        else "their share of net deposits may be wrong"
    )
    return f"{row.account} sent {moved} out {when}, so {harm}."


def _misdated_legs(folio: _LedgerIndex) -> list[CheckFinding]:
    """Report each leg that needs the other leg's date."""
    findings = []
    for diagnostic, computed in folio.tagged(
        DiagnosticCode.TRANSFER_IN_BEFORE_OUT,
        DiagnosticCode.TRANSFER_ACROSS_YEAR_END,
    ):
        row = computed.row
        moved = _what_moved(row, computed.symbol)
        if diagnostic.code is DiagnosticCode.TRANSFER_IN_BEFORE_OUT:
            problem = _arrived_first(row, moved, str(diagnostic.as_of))
        else:
            problem = (
                f"{row.account} received {moved} on {row.txn_date}, but they "
                f"left on {diagnostic.as_of}, so at the year end they were in "
                f"neither account."
            )
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{problem} Give this leg {diagnostic.as_of} as its "
                    f"transaction and settlement date."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return findings


def _cross_type_pairs(folio: _LedgerIndex) -> list[CheckFinding]:
    """Report each transfer between two account types, by its in leg."""
    findings = []
    for diagnostic, computed in folio.tagged(DiagnosticCode.TRANSFER_ACROSS_TYPES):
        row = computed.row
        findings.append(
            CheckFinding(
                subject=f"TxnId {row.txn_id}",
                detail=(
                    f"{diagnostic.account} moved {_what_moved(row, computed.symbol)} "
                    f"into {row.account} on {row.txn_date}, an account of another "
                    f"type. Moving money or holdings between tax treatments is not "
                    f"a transfer: record it as a sale, a withdrawal, a contribution "
                    f"and a buy."
                ),
                txn_ids=(row.txn_id,),
            ),
        )
    return findings


def _check_transfers(folio: _LedgerIndex) -> _Outcome:
    """Report transfer legs that are unpaired, misdated or cross account types."""
    unpaired = _unpaired_legs(folio)
    misdated = _misdated_legs(folio)
    crossed = _cross_type_pairs(folio)
    problems = []
    if unpaired:
        problems.append(
            f"{_plural(len(unpaired), 'transfer leg has', 'transfer legs have')} "
            f"no counterpart",
        )
    if misdated:
        problems.append(
            f"{_plural(len(misdated), 'transfer leg needs', 'transfer legs need')} "
            f"the other leg's date",
        )
    if crossed:
        problems.append(
            f"{_plural(len(crossed), 'transfer crosses', 'transfers cross')} "
            f"account types",
        )
    return _Outcome(
        [*unpaired, *misdated, *crossed],
        f"{_plural(folio.counting(Action.TFR_IN, Action.TFR_OUT), 'transfer leg')} "
        f"all paired up",
        ", ".join(problems),
        CheckStatus.WARN,
    )


# Printed in this order. Identity first, because an account with no type makes
# every later check unreliable, then the checks that prove a row is missing,
# then the ones that only ask for a second look.
_CHECKS: tuple[tuple[str, str, Callable[[_LedgerIndex], _Outcome]], ...] = (
    ("account-types", "Account types", _check_account_types),
    ("unit-balances", "Unit balances", _check_unit_balances),
    ("cash-balances", "Cash balances", _check_cash_balances),
    ("conversion-arithmetic", "Conversion arithmetic", _check_conversion_arithmetic),
    ("currency-consistency", "Currency consistency", _check_currency_consistency),
    ("split-coverage", "Split coverage", _check_split_coverage),
    ("income-sanity", "Income sanity", _check_income_sanity),
    ("fee-conventions", "Fee conventions", _check_fee_conventions),
    ("settlement-dates", "Settlement dates", _check_settlement_dates),
    ("transfers", "Transfers", _check_transfers),
)

CHECK_SLUGS: tuple[str, ...] = tuple(slug for slug, _name, _run in _CHECKS)


def validate_slugs(slugs: Iterable[str]) -> None:
    """Reject a check name that does not exist, rather than ignoring it.

    Args:
        slugs: Names to validate, from config or from `--only`.

    Raises:
        UnknownCheckError: On the first name that is not a known check.
    """
    for slug in slugs:
        if slug not in CHECK_SLUGS:
            raise UnknownCheckError(slug, CHECK_SLUGS)


def run_checks(ledger: Ledger, config: ChecksConfig) -> list[CheckResult]:
    """Run every enabled check over the ledger's diagnostics.

    Args:
        ledger: The ledger to check.
        config: Supplies `check` related configurations.

    Returns:
        A list of results for each enabled check.

    Raises:
        UnknownCheckError: If `checks.disabled` names a check that not exist.
    """
    disabled = set(config.checks_disabled)
    validate_slugs(disabled)
    folio = _LedgerIndex(ledger, config)
    return [
        run(folio).to_result(name, slug)
        for slug, name, run in _CHECKS
        if slug not in disabled
    ]
