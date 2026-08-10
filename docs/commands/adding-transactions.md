# Adding Transactions Manually

`folio add` inserts a single transaction directly into the folio, without going through a
spreadsheet or a broker download.

It exists for the transactions your brokers may not report cleanly:

- **`SPLIT`** — stock splits.
- **`ROC`** — return of capital, which reduces your cost basis and often only shows up on a year-end
  tax slip.
- Corrections and one-off entries you know about but no broker file records.

Any supported action can be added this way, not just the awkward ones.

## Basic Usage

Supply any information you have, `folio add` will prompt for what remains.

```bash
# Fully specified, no prompting
folio add --action BUY --ticker AAPL --date 2025-08-15 \
          --account TFSA --currency USD \
          --amount -1502.50 --price -150.25 --units 10

# Partially specified - prompts for the rest
folio add --action ROC
```

```text
Transaction date (YYYY-MM-DD) [2026-08-08]: 2025-08-20
Account: RRSP
Currency (USD/CAD/EUR): CAD
Ticker: O
Amount: 42.15
✅ Added transaction (TxnId 62)
```

## Options

| Option | Short | Meaning |
| --- | --- | --- |
| `--action` | `-a` | `BUY`, `SELL`, `SPLIT`, `ROC`, `DIVIDEND`, `BRW`, `CONTRIBUTION`, `FCH`, `FXT`, `WITHDRAWAL` |
| `--date` | `-d` | Transaction date, `YYYY-MM-DD` (prompt defaults to today) |
| `--account` | `-n` | Account alias |
| `--currency` | `-c` | `USD`, `CAD` or `EUR` |
| `--ticker` | `-t` | Security ticker |
| `--amount` | `-m` | Total transaction amount |
| `--price` | `-p` | Price per unit |
| `--units` | `-u` | Number of units |
| `--fee` | | Transaction fee |
| `--set` | | `KEY=VALUE` for optional columns, repeatable |
| `--force` | | Add even when it duplicates an existing transaction |
| `--dry-run` | | Validate and preview without writing |

## Amount and Units Signs

Each action has a real cash and share direction, and Amount/Units are corrected to match it. A BUY
spends cash and gains shares; a SELL is the reverse. You do not have to get the sign right — enter
`--amount 1502.50` on a BUY and it is stored as `-1502.50`.

| Action | Amount | Units |
| --- | --- | --- |
| `BUY` | negative (cash out) | positive (shares in) |
| `SELL` | positive (cash in) | negative (shares out) |
| `WITHDRAWAL` | negative | — |
| `CONTRIBUTION` | positive | — |
| `DIVIDEND` | positive | — |
| `ROC` | positive | — |
| `SPLIT` | — | positive (ratio) |
| `FCH`, `FXT`, `BRW` | either | either |

`FCH`, `FXT` and `BRW` are left alone deliberately: both directions are legitimate for them — a fee
versus interest earned, or the two opposing legs of an FX trade or a Norbert's Gambit journal.

These rules apply to every ingest path, not just `folio add` — imports from brokers and
spreadsheets are corrected the same way, and the correction is recorded in the importer audit log.

## Recording a Stock Split

Splits store the **ratio** rather than a money value:

- `Price` is the number of shares **before** the split.
- `Units` is the number of shares **after** the split.

For a 1:10 split, that is `--price 1 --units 10`:

```bash
folio add --action SPLIT --ticker NVDA --date 2024-06-10 \
          --price 1 --units 10 --account TFSA --currency USD
```

For a 4:1 reverse split, `--price 4 --units 1`.

The prompts spell this out when you do not pass the values on the command line.

> [!NOTE]
> Adding a `SPLIT` row records that the split happened. It does **not** rewrite your existing
> transactions for that ticker. If your history was previously adjusted by hand to account for the
> split, those rows stay as they are and might need to be fixed via
> [`folio edit`](editing-transactions.md).

## Optional and Custom Columns

`--set` populates any other column, including [optional columns](../configuration.md) configured for
your folio. A column that does not exist yet is added to the transactions table automatically.

```bash
folio add -a FCH -d 2025-08-15 -n RRSP -c CAD -m -9.99 \
          --set Description="Annual account fee"
```

Repeat `--set` for multiple columns.

## Duplicates

A transaction that matches an existing one on all of its essential fields (date, action, amount,
currency, price, units, ticker, account) is treated as a duplicate. `folio add` shows you what it
matched and asks before writing:

```text
⚠️  This transaction matches 1 existing transaction(s) in the folio:
┏━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━┓
┃ TxnId ┃ SettleDate ┃ TxnDate    ┃ Action ┃ Amount ┃ $   ┃ Price ┃ Units ┃ Ticker ┃ Account ┃  Fee ┃
┡━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━┩
│ 61    │ 2025-08-18 │ 2025-08-15 │ SPLIT  │   0.00 │ USD │   1.0 │   4.0 │ AAPL   │ TFSA    │ 0.00 │
└───────┴────────────┴────────────┴────────┴────────┴─────┴───────┴───────┴────────┴─────────┴──────┘
Add it anyway as an intentional duplicate? [y/N]:
```

Answering `n` leaves the folio untouched. Use `--force` to add without being asked — useful in
scripts, and for genuinely repeated transactions like two identical contributions on the same day.

This is the same approval mechanism as
[duplicate approval](../import/duplicate-approval.md) for imports.

## Validation Failures

If a value cannot be understood, nothing is written and the reason is reported:

```bash
$ folio add -a BUY -t AAPL -d notadate -p 1 -u 4 -n TFSA -c USD -m 4
❌ Transaction was rejected:
❌   INVALID TxnDate
```

Use `--dry-run` to check a transaction, including its calculated settlement date, before committing
to it:

```bash
folio add -a BUY -t AAPL -d 2025-08-15 -n TFSA -c USD \
          -m -1502.50 -p 150.25 -u 10 --dry-run
```

## Notes

- **Transform rules apply.** Any `transforms` rules in your config run against manually added
  transactions too, exactly as they do for imports. See
  [Transaction Transformation](../import/transformations.md).
- **Settlement dates are calculated.** Actions that settle on a business-day delay (`BUY`, `SELL`,
  `FXT`, `SPLIT`) get a calculated settlement date; the rest settle same day. See
  [Settlement Date Calculation](../import/settlement-dates.md).
- **A backup is taken** before database changes as *always*, subject to your `backup` config.
- **It is logged** to the same importer audit log as imports.

## Related

- [Smart Transaction Querying](querying.md) — find transactions, including the one you just added
- [Editing Transactions](editing-transactions.md) — correct a transaction after the fact
- [Deleting Transactions](deleting-transactions.md) — remove one that should not be there
- [Configuration Reference](../configuration.md) — optional columns, duplicate approval, backups
