# Editing Transactions

`folio edit` corrects transactions already in the folio, one at a time or in batch. Changes are
expressed with repeatable `--set`, where a value is either a literal or an arithmetic operation
applied to whatever the row currently holds.

The arithmetic form is what makes batch corrections practical

## Basic Usage

```bash
# Several fields at once
folio edit 1234 --set Price=175.20 --set Units=20

# Batch by query terms, computed against each row's current value
folio edit NVDA before 2024-06-10 --set Price*=10 --set Units/=10
```

```text
                             Pending Changes
  TxnId  TxnDate     Action  Amount    $    Price           Units
  1234   2024-01-05  BUY     -1752.00  USD  17.52 -> 175.20  100 -> 10
  1235   2024-03-11  BUY      -876.00  USD  17.52 -> 175.20   50 -> 5
Edit 2 transaction(s)? [y/N]: y
✅ Updated 2 transaction(s)
```

The preview shows the whole transaction, not just the changed fields.

## Options

| Option      | Meaning                                               |
| ----------- | ----------------------------------------------------- |
| `--set`     | `Field=VALUE` or `Field<op>=N`, repeatable. Required. |
| `--force`   | Apply without confirming, and accept duplicates       |
| `--dry-run` | Show the before/after without writing                 |

## Setting Values

### Arithmetic

| Operator | Meaning     | Example           |
| -------- | ----------- | ----------------- |
| `*=`     | multiply by | `--set Price*=10` |
| `/=`     | divide by   | `--set Units/=10` |
| `+=`     | add         | `--set Fee+=5`    |
| `-=`     | subtract    | `--set Fee-=2`    |

Arithmetic is only accepted on numeric fields - `Amount`, `Price`, `Units`, `Fee`, and any
[optional column](../configuration.md) configured as numeric. Applying it elsewhere is an error:

```text
$ folio edit 1234 --set Ticker*=2
❌ Cannot apply '*=2' to Ticker: not a numeric field.
```

Calculations run in exact decimal arithmetic, not floating point, so `17.52 *= 10` stores exactly
`175.2`.

### What Cannot Be Edited

- **`TxnId`** identifies the row and is assigned by the database.
- **`SettleCalculated`** is derived - see below.
- **Columns your folio does not have.** Unlike `folio add --set`, editing never creates a column

## Settlement Dates

Settlement dates carry a "calculated" tag recording whether the folio worked the date out from
market calendars or you supplied it yourself. `folio edit` keeps that honest:

| You do this                         | Result                                                                                 |
| ----------------------------------- | -------------------------------------------------------------------------------------- |
| `--set SettleDate=2024-06-12`       | The date is stored and the calculated tag is **cleared** - it is yours now             |
| Edit`TxnDate` on a calculated row   | The settlement date is **recalculated** from the new date, and stays tagged calculated |
| Edit`TxnDate` on a manually set row | Both the settlement date and the tag are left alone                                    |

Editing `Action` or the currency also triggers recalculation on a calculated row, since the
settlement rule depends on them.

An invalid settlement date is rejected rather than quietly recalculated:

```text
$ folio edit 1234 --set SettleDate=notadate
❌ 'notadate' in 'SettleDate=notadate' is not a valid date.
```

## Validation

Edited rows go back through the same formatter and validation rules that imports and `folio add`
use, so **an edit cannot write a row that `folio add` would have rejected**. That includes:

- required fields for the action (a `BUY` cannot lose its `Amount`),
- date, action, currency and ticker normalization,
- **sign normalization** - `--set Amount=3000` on a `BUY` stores `-3000`, because a buy spends cash.
  See [Amount and Units Signs](adding-transactions.md#amount-and-units-signs).

If any row in the batch fails, the whole edit is abandoned and nothing is written. The set that gets
applied is always exactly the set you previewed.

## Duplicates

An edit that would make a row identical to another transaction - on date, action, amount, currency,
price, units, ticker and account - shows the clash and asks first, the same way `folio add` does.
`--force` answers yes.

Rows being edited are excluded from that check, so editing a row's fee, or setting a field to the
value it already has, never reports the row as a duplicate of its own former self. A change that
turns out to be no change at all just says so:

```text
$ folio edit 1234 --set Price=150.25
No changes to apply.
```

## Notes

- The folio database is backed up before the update, subject to your
  [backup configuration](../configuration.md).
- Every edit is written to the importer audit log with a per-field diff plus the complete before and
  after state of each row.

## Related

- [Deleting Transactions](deleting-transactions.md)
- [Adding Transactions Manually](adding-transactions.md)
- [Smart Transaction Querying](querying.md)
- [Settlement Dates](../import/settlement-dates.md)
