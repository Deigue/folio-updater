# Market value

`folio value` shows what every account was worth on a past date: each holding's
units at that day's historical price, converted at that day's Bank of Canada rate,
plus its cash.

```bash
folio value 2026-04             # every account at April's last trading day
folio value 2026-04-15          # every account on one day
folio value 2026-01 2026-04     # every month-end from January through April
folio value 2026-04-30 XEQT.TO  # one holding, per account
```

## Dates

- `YYYY-MM` means the month's last day, `YYYY-MM-DD` that day.
- A weekend takes Friday's close, and a holiday the close before it.
- A month or day not over yet is valued at the latest close, and the title says so.
  A date still to come is refused.
- Units and cash both follow transaction date: a buy on the last day of a month
  counts that month, even though it settles in the next.

## Layouts

| Command                         | Rows            | Columns                          |
| ------------------------------- | --------------- | -------------------------------- |
| `folio value DATE`              | Each account    | Holdings, cash, value            |
| `folio value START END`         | Each month-end  | Each account's value, and Total  |
| `folio value DATE SYMBOL`       | Each account    | Units, price, value              |

An account holding nothing is left out. In a month-end table, an account keeps its
column from its first month-end with anything in it, and shows `-` before that.
Units or cash a transfer has taken out of one account and not yet delivered show
as an **In transit** row, so the rows always add up to the total.

A symbol is the folio's own, such as `XEQT.TO`, as `folio acb` takes it.

## Options

| Option             | Effect                                                        |
| ------------------ | ------------------------------------------------------------- |
| `-t`, `--type`     | Only one account type's accounts: `tfsa`, `rrsp`, `nreg`, ... |
| `-a`, `--account`  | Only one account, in any case                                 |
| `-b`, `--by-type`  | One row (or column) per account type instead of per account   |
| `-c`, `--currency` | `CAD` *(default)*, `USD`, or `native` (a single date only)    |
| `-o`, `--offline`  | Value from the stored prices, fetching none                   |

`-b` takes neither `-t` nor `-a`. `-c native` keeps each currency line apart, with
a total per currency and no grand total.

## Prices

Each run first fetches the historical prices the folio lacks, as `folio update`
does. A US dollar line with no price history of its own, such as `DLR.U.TO`, is
priced from its Canadian dollar line (`DLR.TO`) at that day's rate, and says so.
A holding with no price at all is left out of every value: the value is marked `*`
and a line under the table names the holding and where it was.
