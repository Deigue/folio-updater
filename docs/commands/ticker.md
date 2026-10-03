# Ticker

`folio ticker` shows comprehensive details about a security. It shows
its quote and fundamentals, how far its price has moved over every range, and how
each part of the folio holds it. Name none, or several, and it compares them instead,
one row each, so each range can be read down the column.

```bash
folio ticker MSFT              # one security in focus
folio ticker                   # compare every holding
folio ticker MSFT NVDA META    # compare exactly these, owned or not
```

A symbol the folio has never traded works too: it shows everything but the holdings.
An old symbol from [`folio symbol`](../../README.md) resolves to the one it was
renamed to, and the header makes mention of it.

## One security

```bash
folio ticker MSFT
folio ticker MSFT -b -c both
```

| Option              | What it does                                                         |
| ------------------- | -------------------------------------------------------------------- |
| `-c`, `--currency`  | `native` (default, the security's own currency), `CAD`, `USD` or `both` |
| `-b`, `--by-account`| One row per broker account instead of per account type               |
| `-e`, `--export`    | Write it to a `.xlsx` file instead of printing                       |
| `-o`, `--offline`   | Use cached data only, never touching the network                     |
| `--refresh`         | Refetch the quote, fundamentals and price history                   |

Details shown:

- **Header**: the name, last price and the day's move from the previous close.
- **Fundamentals**: valuation (market cap, P/E, EPS, beta, next earnings), dividends
  (rate, yield, ex-dividend date) and trading (52-week range, moving averages,
  volume). A fund shows its assets, expense ratio and family instead.
- **Performance**: a small chart for every range, as many to a row as the terminal
  fits. Each chart's title carries the move; underneath it, where the range starts
  and the price it starts from. The dotted line is that starting price, and each
  bar is green above it and red below.
- **Holdings**: one row per account type (or account), then **POOLED**: every
  account pooled into one cost base. POOLED is left out when there is only one
  row, which it would merely repeat. The non-registered row is badged **CRA**,
  since its cost base is the one that is taxed.

### The ranges

| Range | Starts at                                                  | Drawn from        |
| ----- | ---------------------------------------------------------- | ----------------- |
| `2h`  | Two hours before the latest trade                          | 1-minute bars     |
| `1d`  | The previous close; today's session from the open          | 5-minute bars     |
| `2d`  | The close before yesterday; yesterday's and today's sessions | 5-minute bars   |
| `1wk` | The close a week ago                                       | 5-minute bars     |
| `1mo` | The close a month ago                                      | hourly bars       |
| `6mo`, `YTD`, `1y` | The close six months ago, at the end of last year, a year ago | daily closes |
| `5y`  | The weekly close five years ago                            | weekly closes     |
| `All` | The first monthly close Yahoo has                          | monthly closes    |

Every move runs from an official close to the last price, price only: dividends are
not added back. The bars only draw a range's shape. `1d` and `2d` sit on a fixed
session axis like an intraday chart, so while the market is open they fill only part
of the way.

Price history is cached and refetched at most once a day, keeping a fixed handful of
points per range whatever the security's age. The intraday bars behind `2h` to `1wk`
are fetched live and never stored, so offline those ranges fall back to the stored
closes, `2h` is blank, and `1d` keeps its move without a chart.

### Why POOLED differs from the rows

POOLED treats every account as one pool, so all the buys share one average cost.
When one account sells, the rows measure the sale against that account's own
average, while POOLED measures it against the pooled one. The two split the same
result differently between realized and unrealized: **Total is the same either
way**, but Book, Avg and Realized are not the sum of the rows.

## Comparing

```bash
folio ticker
folio ticker -t tfsa -s 1wk
folio ticker -s ytd -r
```

| Option             | What it does                                                    |
| ------------------ | --------------------------------------------------------------- |
| `-t`, `--type`     | Only what one account type holds (`tfsa`, `rrsp`, `nreg`, ...)  |
| `-a`, `--account`  | Only what one broker account holds                              |
| `-s`, `--sort`     | Order by `market` (default) or a range, e.g. `1wk`, `ytd`, `all` |
| `-r`, `--reverse`  | Flip the sort direction                                         |
| `-e`, `--export`   | Write it to a `.csv` or `.xlsx` file instead of printing        |
| `-o`, `--offline`  | Use cached data only                                            |
| `--refresh`        | Refetch quotes and price history                                |

Each row carries the last price in the security's own currency, the market value in
CAD (so the column compares), its share of the folio, and its move over every range.
The best and worst of each range are bold. Sorted, the largest comes first; a
security with nothing to measure over that range stays at the bottom.

`-t` and `-a` also add `Wt%`, the security's share of that pool. A symbol named but
unknown to Yahoo is left out with a warning, and nothing about it is kept.

## Export

```bash
folio ticker MSFT -e msft.xlsx      # one sheet: quote, fundamentals, moves, holdings
folio ticker -e performance.csv     # the comparison, one row per security
```

A single security's sheet holds several tables, so it needs a workbook. The
comparison exports to either format. [`folio generate`](../../README.md) also writes it,
as the `Performance` sheet linked from the Summary (`--only perf` for just that).
