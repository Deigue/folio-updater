# Checking the Folio

`folio check` replays your whole ledger and reports what looks wrong with it, in plain
language, one line per check.

```bash
folio check
```

It is a **reporter, never a fixer**. It opens the folio, reads it, and prints.

## Why it exists

A cash account cannot be overdrawn, and you cannot sell shares you never bought. So when
a replay of your own transactions produces a negative balance or a negative position, it
has *proved* that a transaction is missing, and dated the proof. That is worth far more
than a warning that something "looks unusual". It narrows a discrepancy from "somewhere
in three years" to a single day, without opening a single broker statement.

Every check here is a by-product of the cost-base replay `folio acb` already performs,
so running it costs just one replay. If `folio acb` has already ran and cached,
`folio check` will be instant.

## Check Results

```text
$ folio check

Account types          ✅ 7 accounts, all types inferred
Unit balances          ❌ 3 securities never balance
                       FTS.TO  WS-TFSA sold 22 units on 2023-09-18 but only held
                               4.23 there. 18 units are unaccounted for. The TFSA
                               total is correct, so those units most likely moved
                               from another account without being recorded.
Cash balances          ❌ 1 account goes negative
                       IBKR-TFSA USD  first goes negative on 2024-08-19, at
                                      -12,256.18 USD, so a transaction is missing
                                      on or before that date.
Conversion arithmetic  ❌ 1 conversion does not add up
                       TxnId 1818  IBKR-RRSP 2024-05-31
                                   recorded   -13,000.00 CAD
                                   9,541.98 x 1.3624 = -12,999.99
                                   Open the statement and see which of the three
                                   is right.
Currency consistency   ✅ no security is booked in more than one currency
Split coverage         ✅ 4 splits, every account got one
Income sanity          ✅ all income lands on a position held
Fee conventions        ✅ 7 accounts agree with their own trades
Settlement dates       ✅ every trade settles when it should
Transfers              ⚠️  1 transfer leg has no counterpart

3 checks failed, 1 warning. Run `folio check --only unit-balances` for detail.
```

| Icon | Meaning                                  |
| ---- | ---------------------------------------- |
| ✅    | Nothing to report                        |
| ⚠️    | Probably fine, but worth seeing          |
| ❌    | Wrong, and the folio should be corrected |

`folio check` **exits non-zero when any check fails**, so it can gate a script. Warnings
alone still exit zero.

## The checks

| Check                     | What it proves                                                                    |
| ------------------------- | --------------------------------------------------------------------------------- |
| **Account types**         | Every account resolves to a tax type. Nothing else means much until this passes.  |
| **Unit balances**         | No position ever goes below zero. The check that finds the largest defects.       |
| **Cash balances**         | No account is ever overdrawn.                                                     |
| **Conversion arithmetic** | A currency conversion's amount, units and rate describe one event, so they agree. |
| **Currency consistency**  | No security is booked in two currencies.                                          |
| **Split coverage**        | Every account holding a security through a split has a split row for it.          |
| **Income sanity**         | Dividends and returns of capital land on positions actually held.                 |
| **Fee conventions**       | Every trade's amount reconciles against its price, units and commission.          |
| **Settlement dates**      | No trade settles before it was made, or far outside its account's usual lag.      |
| **Transfers**             | Every `TFR_OUT` has a matching `TFR_IN`, so the cost base can follow the units.   |

### Unit balances

When a sale overdraws a position, the shortfall is measured at three levels at once, and the report says which:

- Short in **one account** but the account **type** still balances → *"The TFSA total is
  correct, so those units most likely moved from another account without being
  recorded."* Almost always an unrecorded transfer between your own accounts.
- Short across the whole **account type** → a transfer that crossed account types, or a
  transaction that was never recorded at all.
- Short across the whole **portfolio** → the units were never there.

### Cash balances

Reports the **first** date a balance crosses below zero, not the deepest point it
reaches. The deepest figure is a consequence, the first date is the lead, and it is the
date the missing transaction belongs to.

Cash is replayed in **settle-date** order, so a purchase and the contribution funding it
on the same day never trip this check. A balance that goes negative once and then never
moves again is reported as *one past error* rather than an ongoing one, which tells you
where to look far better than restating the figure.

### Conversion arithmetic

On a single-row FX transaction, `Amount`, `Units` and `Price` are three views of one event,
so the row can check itself with no outside data. The report prints **all three
numbers** to inform the user. The folio cannot tell which one is wrong, only
that they disagree, and you need the recorded figure and the implied figure side by side
to settle it against a brokerage statement.

### Settlement dates

Compared against each **account's own** prevalent Settlement date lag, so a
broker on a different schedule calibrates itself. An account with fewer than five
trades is exempt from this check.

## Options

| Option        | Effect                                                   |
| ------------- | -------------------------------------------------------- |
| `--only SLUG` | Show full detail for one check, whether it passed or not |
| `--json`      | Machine-readable results, for scripting                  |

```bash
folio check --only unit-balances
folio check --json
```

Combine the two to pull one check's transaction IDs for scripting: `folio check --only
unit-balances --json` narrows the `checks` array to that one entry, findings and all, so a
script does not have to filter the full result set for the `txn_ids` behind one finding.

## Configuration

Every check runs by default. All three keys below are optional.

```yaml
checks:
  disabled: [settlement-dates]          # slugs of checks to turn off
  ignore_tickers: [DLR.TO, DLR.U.TO]    # gambit vehicles, never really held
  ignore_accounts: [QT-TFSA]   # retired, kept for history only
```

An ignored ticker or account is excluded from **every** check. A slug that
is invalid is *rejected* rather than silently ignored, the same applies to `--only`.

The slugs are: `account-types`, `unit-balances`, `cash-balances`,
`conversion-arithmetic`, `currency-consistency`, `split-coverage`, `income-sanity`,
`fee-conventions`, `settlement-dates`, `transfers`.
