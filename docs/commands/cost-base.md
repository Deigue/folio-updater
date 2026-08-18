# Adjusted Cost Base

`folio acb` replays your whole transaction ledger into an adjustedcostbase-style
buildup: units held, adjusted cost base, average cost and realized gains, one row per
transaction.

```bash
folio acb NVDA
```

## Three pools from one replay

Every invocation computes three grains at once and caches the result, so switching
between them costs nothing beyond re-rendering:

| Grain            | Flag                    | What it pools                          |
| ---------------- | ----------------------- | -------------------------------------- |
| **Account type** | `-t/--type` *(default)* | Every account of one tax type together |
| **Account**      | `-a/--account`          | A single broker account                |
| **Portfolio**    | `--folio`               | Everything you own                     |

A bare `folio acb MSFT` reports the **non-registered** pool, which is where the
CRA-relevant figures live. `--type tfsa`, `--type rrsp` and so on switch tax type;
`nreg`, `personal`, `cash` and `taxable` are all accepted for non-registered.

```bash
folio acb NVDA                        # non-registered pool
folio acb NVDA --type tfsa            # every TFSA pooled together
folio acb NVDA --account IBKR-TFSA    # one broker account
folio acb NVDA --folio                # portfolio-wide
```

## Options

| Option            | Effect                                                         |
| ----------------- | -------------------------------------------------------------- |
| `--currency`      | `CAD`, `USD`, or `both` (default: both, for USD holdings)      |
| `--from` / `--to` | Restrict to a date range, `YYYY-MM-DD`                         |
| `--year YYYY`     | Shorthand for a whole calendar year                            |
| `--all`           | Include `DIVIDEND` and `FCH` rows, which never touch cost base |
| `--summary`       | One row per symbol instead of a per-transaction buildup        |
| `--export PATH`   | Write the reported rows to a `.csv` or `.parquet` file         |
| `--refresh`       | Rebuild the cache before reporting                             |

`SYMBOL` may be omitted only with `--summary` or `--export`.

## Reading the table

Rows are dated and ordered by **settle date**, i.e. the date the cash moved and the date
whose FX rate converted it, showing the **newest first**.
The cost base itself is still replayed in trade-date order; the ordering
here is presentation only.

`Held`, `ACB` and `Avg` are running figures: the state of the pool *after* that row. A
`▲` or `▼` beside the average marks which way it moved against the chronologically
preceding row, so it still reads correctly with the newest row on top.

> [!NOTE]
> `--from`, `--to` and `--year` filter on the **trade** date, not the settle date shown
> in the table. A trade made late in December and settling in January therefore appears
> under the earlier year.

## Summary

`--summary` collapses the buildup to one row per symbol, carrying each pool's closing
units, cost base, average cost and realized gain:

```bash
folio acb --summary                   # every symbol in the non-registered pool
folio acb --summary --type tfsa       # ... pooled across every TFSA
folio acb --summary --account WS-TFSA # ... in one account
```

**Open positions sort first**, alphabetical within each group, with closed ones
underneath.

The `USD` columns will only appear if the pool contains USD-denominated holdings.

`--currency USD` additionally **drops the CAD-denominated holdings**

`--currency CAD` keeps both denominated holdings, but only shows the CAD columns. Since
CAD is the tax currency, the converted figures for a USD holding belong in it.

## Export

`--export PATH` exports all rows to `.csv` or `.parquet`, chosen
based on the suffix. (.csv by default)

What is exported: every column the engine computed, at all three grains at once.
Only the row filters (`SYMBOL`, `--type`,
`--account`, `--folio`, `--from`, `--to`, `--year`, `--all`) remain applicable.

```bash
folio acb --export acb.parquet            # every symbol, non-registered rows
folio acb NVDA --export nvda.csv          # one symbol, still writes all scopes
folio acb --folio --year 2025 --all --export 2025.csv
```

`SYMBOL` may be omitted with `--export`, which is how you dump the whole ledger:

```bash
folio acb --folio --all --export full.parquet
```

## Currency

**CAD is the tax currency and is always populated.** Columns with a `USD` header are
the original-currency variant and appear only for USD-denominated holdings; for a CAD
holding they would be blank by design, so they are suppressed along with the FX rate
column.

**The CAD cost base is converted at each transaction's settle date, not at today's
rate.**

| Event         | USDCAD | ACB (USD) | ACB (CAD) | Avg (USD) |  Avg (CAD) |
| ------------- | ------ | --------: | --------: | --------: | ---------: |
| Buy 10 @ $100 | 1.30   |     1,000 |     1,300 |    100.00 |     130.00 |
| Buy 10 @ $100 | 1.40   |     2,000 |     2,700 |    100.00 | **135.00** |

On a sale the CAD cost base comes off **proportionally**, while proceeds are converted
at that day's rate. The gap between them is the FX component of the capital gain,
which is what CRA taxes.

## What the engine knows

None of this needs configuration.

- **Account types** come from the `<BROKER>-<TYPE>` naming convention: `IBKR-TFSA` is a
  TFSA, `WS-PERSONAL` is non-registered. See [Configuration](../configuration.md) for
  the override when a name does not follow it.
- **Fee conventions** are detected per account. QuestTrade reports `Amount` net of the
  commission; Interactive Brokers and Wealthsimple report it gross with the fee charged
  separately. Each account's trades are reconciled against `Price * Units` to decide
  which, and the majority wins.
- **The sign of a fee is read, not assumed.** Brokers disagree on which sign means
  "charged", so the prevailing sign is taken per account the same way. A row carrying
  the minority sign is a rebate: Interactive Brokers splits an order across fills and
  refunds part of the commission, and those refunds are credited rather than charged.
- **Splits** only apply to the account that reported them. One corporate action reported
  by three brokers applies once to the pooled type, and three times across the three
  different account pools, since each account really did split.
- **Return of capital** reduces the cost base and moves no cash: it reclassifies a
  distribution that was already paid as a dividend.
- **Transfers** (`TFR_IN` / `TFR_OUT`) carry the cost base across without realizing a
  gain, and consume no contribution room.
- **Ticker renames** resolve through `folio symbol`, and are time-bounded: a symbol used
  *after* its rename date is treated as a different security, because symbols get reused.

## Diagnostics

Rows carrying a problem are tagged in the `Flags` column and rolled up in the footer.
The engine never silently corrects data - an oversell drives units negative rather than
clamping, so the deficit stays visible and the arithmetic recovers once the missing row
is recorded.

| Code                       | Meaning                                                      |
| -------------------------- | ------------------------------------------------------------ |
| `OVERSELL`                 | Replayed units went negative                                 |
| `NEGATIVE_FINAL_POSITION`  | A pool ends below zero                                       |
| `CASH_NEGATIVE`            | Cash went negative in a non-margin account                   |
| `DUPLICATE_SPLIT`          | Two split rows for the same ticker in the same account       |
| `SPLIT_WITHOUT_POSITION`   | A split against a pool holding nothing                       |
| `ROC_EXCEEDS_ACB`          | Return of capital drove the cost base below zero             |
| `INCOME_WITHOUT_POSITION`  | A dividend or return of capital with no position held        |
| `SETTLE_BEFORE_TRADE`      | A trade that settles before it was made                      |
| `AMBIGUOUS_FEE_CONVENTION` | A trade matching neither fee convention                      |
| `FXT_AMOUNT_INCONSISTENT`  | A conversion whose `Amount` and `Units * Price` disagree     |
| `UNKNOWN_ACCOUNT_TYPE`     | An account name the convention could not resolve             |
| `TRANSFER_UNPAIRED`        | A transfer leg with no counterpart                           |
| `SUPERFICIAL_LOSS_SUSPECT` | A realized loss with a buy of the same symbol within 30 days |

`folio acb` reports these; it does not fail on them. It always exits zero once it has
printed a table.

> [!IMPORTANT]
> **`SUPERFICIAL_LOSS_SUSPECT` is a warning only: the cost base and gain figures are
> *not* adjusted for it.** By CRA rule, a superficial loss should be denied and added
> back to the cost base of the shares that caused it. This engine deliberately does not
> do that: it tracks cost base the same way your broker does, matching Units, ACB and
> Avg 1:1 against your account statements. If this flag fires, the realized loss shown
> here is not the one CRA will actually allow - work out the adjustment yourself (or
> with a tax preparer) before filing. This loss should not be reported on your return.

Two of these are deliberately narrower than they first appear:

- **`SETTLE_BEFORE_TRADE` judges trades only.** This diagnostic code is only applicable
  to trade type transactions like `BUY` and `SELL`
- **`INCOME_WITHOUT_POSITION` allows a 60-day tail.** A distribution is earned
  on its ex-date and paid weeks later, during which the position could be sold out. The
  finding is income against a pool that never held the symbol, or has not held it in months.

## Caching

The replay is written to `data/acb.parquet` with a fingerprint of the transactions table
and the config keys that affect the arithmetic. Any `add`, `edit`, `delete` or `import`
moves the fingerprint and the next run rebuilds automatically. Every table says which it
was:

```text
computed just now
cached 15m ago
```

`--refresh` forces a rebuild.

## FX coverage

Cost-base conversion needs a Bank of Canada rate for every settle date. Weekend and
holiday settlements have no rate of their own: those carry back to the previous
business day. `folio acb` tops up missing rates itself unless
`cost_basis.auto_getfx` is turned off, and only when the folio's own dates are not
already covered.
