# Dashboard

`folio dash` prices what you currently hold: units and average cost from the
cost-base replay, live prices from Yahoo Finance, and a panel of cash and
cumulative flows above the table.

By default, `folio dash` is **portfolio-wide**

## Scoping

| Grain            | Flag           | What it shows                      |
| ---------------- | -------------- | ---------------------------------- |
| **Portfolio**    | *(default)*    | Everything you own, pooled         |
| **Account type** | `-t/--type`    | Every account of one type together |
| **Account**      | `-a/--account` | A single broker account            |
| **Every type**   | `--by-type`    | One panel per tax type, tiled      |

```bash
folio dash                      # everything
folio dash --type tfsa          # every TFSA pooled
folio dash --account IBKR-TFSA  # one broker account
folio dash --by-type            # a panel per tax type, side by side
```

`-t` and `-a` are mutually exclusive, and `--by-type` takes neither.

## Options

| Option             | Effect                                                |
| ------------------ | ----------------------------------------------------- |
| `-c`, `--currency` | `native` *(default)*, `CAD` or `USD`                  |
| `-s`, `--sort`     | Order by a column, e.g. `-s total`                    |
| `-r`, `--reverse`  | Flip the sort direction                               |
| `-w`, `--wide`     | Add the columns a narrow terminal cannot fit          |
| `--show-closed`    | Break the aggregate `Closed` row into one row each    |
| `-o`, `--offline`  | Use cached quotes only, never touching the network    |
| `-e`, `--export`   | Write the valued holdings to a `.csv` or `.xlsx` file |
| `--refresh`        | Refetch quotes and rebuild the cost-base cache        |

## Sorting

```bash
folio dash -s total      # biggest total return first
folio dash -s total -r   # worst first
folio dash -s symbol     # A to Z
```

**Numbers sort largest-first and text A-to-Z**, which is what you want almost
every time. `-r` flips whichever applies. Unpriced holdings sink to the bottom.
Money measures sort on the CAD value, so a mixed-currency pool orders by what each
position is really worth.

Sort by any of: `symbol`, `name`, `units`, `avg`, `last`, `change`, `change%`,
`pnl`, `pnl%`, `unreal`, `unreal%`, `realized`, `divs`, `total`, `total%`, `book`,
`market`, `wt%`, `folio%`.

## Currency

**Every holding is reported in the currency it trades in.** A US stock shows its
average cost, price, book value and market value in USD, consistent with your brokers.
Rows are grouped by currency, each group having its own subtotal.

```text
| Symbol    |  Units |      Avg |     Last |     PnL |    Unreal |      Book |    Market |    Wt% |
|-----------+--------+----------+----------+---------+-----------+-----------+-----------+--------|
| XYZ.TO    |     10 |   200.00 |   220.00 |    5.00 |    200.00 |  2,000.00 |  2,200.00 |  1.69% |
| ABC.TO    |     40 |   100.00 |   125.00 |    3.00 |  1,000.00 |  4,000.00 |  5,000.00 |  3.85% |
| ...       |        |          |          |         |           |           |           |        |
|-----------+--------+----------+----------+---------+-----------+-----------+-----------+--------|
| 5 held    |    CAD |          |          | -100.00 |  6,000.00 | 25,000.00 | 31,000.00 | 23.85% |
|-----------+--------+----------+----------+---------+-----------+-----------+-----------+--------|
| QRS       |     80 |   200.00 |   280.00 |   40.00 |  6,400.00 | 16,000.00 | 22,400.00 | 24.37% |
| UVW       |     30 |   400.00 |   450.00 |    8.00 |  1,500.00 | 12,000.00 | 13,500.00 | 14.69% |
| ...       |        |          |          |         |           |           |           |        |
|-----------+--------+----------+----------+---------+-----------+-----------+-----------+--------|
| 7 held    |    USD |          |          |   60.00 | 15,000.00 | 55,000.00 | 70,000.00 | 76.15% |
|-----------+--------+----------+----------+---------+-----------+-----------+-----------+--------|
| Total     |  (CAD) |          |          |  -30.00 | 29,000.00 |101,000.00 |130,000.00 |100.00% |
```

**The grand total is the only converted figure on the page**, and its label indicates so.

`--currency CAD` converts everything to CAD as one ungrouped block instead.
`--currency USD` **drops CAD-denominated holdings**, whose USD figures are blank by
design, and totals in USD.

> [!NOTE]
> `display.currency` in `config.yaml` does not apply here. It exists to pick a single
> cost-base currency for `folio acb`; a dashboard's job is to show you what your
> broker shows you.

**`Wt%`, `Folio%` and `PnL%`**, are always calculated against the CAD market value. A weight
answers "how much of what I own is this", which spans currencies by definition.

Book value converts at each transaction's own **historical** settle-date rate, because
that is what CRA taxes. Market value converts at **today's** rate, because it is a
current valuation. The footnote names the rate and its date used.

## Reading the table

| Column     | Meaning                                                       |
| ---------- | ------------------------------------------------------------- |
| `Avg`      | Adjusted cost base per unit                                   |
| `Last`     | Current price                                                 |
| `Change`   | The day's move per share                                      |
| `Change%`  | `Change` over the current price                               |
| `PnL`      | The day's gain or loss on the position: `Change x Units`      |
| `PnL%`     | That gain as a share of **the whole pool's** market value     |
| `Unreal`   | `Market - Book`, the gain you are still holding               |
| `Realized` | Gains already banked on this security in this pool            |
| `Divs`     | Dividends this security has paid you in this pool             |
| `Total`    | `Unreal + Realized + Divs`: everything the holding has earned |
| `Total%`   | `Total` over `Book` (the total line differs, see below)       |
| `Wt%`      | The holding as a percentage of the pool shown                 |
| `Folio%`   | The holding as a percentage of **everything you own**         |

### Closed positions

A position you have fully sold is no longer a holding, but its realized gain and
the dividends it paid are real money the pool earned. These are rolled into a
single **`Closed`** row at the foot of each currency group, and counted in every
subtotal and total.

```text
| Symbol      | Units |  Realized |   Divs |   Total |
|-------------+-------+-----------+--------+---------|
| DEF.TO      |    10 |           |  20.00 | -600.00 |
| Closed (3)  |       |  1,200.00 | 300.00 | 1,500.00|
```

`--show-closed` breaks that one row open into the individual closed positions.

**`Total` column truthfully answers "how has this done".** Unrealized gain flatters a
stock you never sold and punishes one you trim. Adding realized gains and dividends gives
the whole picture.

**The overall `Total%` is measured against net deposits.** We cannot use `Book` because
when we earn dividends, we can reinvest them to buy more stocks, and that adds to `Book`.
Similarly, when we sell a position, its gains are added to `Total`, but the `Book` or
cost basis behind it dissapears, since we sold it all. Thus `Net Deposits`, i.e: What
money we effectively put in; is the right number to denominate against to calculate the
`Total%` of the whole pool.

## Cash and flows

Every scope carries a panel above the table with cash, contributions, withdrawals,
dividends, fees and realized gains for that pool.

```text
+----------- WS-TFSA ------------+
| Cash               5.00  CAD   |
| Contributions 20,000.00  CAD   |
| Withdrawn      2,000.00  CAD   |
| Net Deposited 18,000.00  CAD   |
| Dividends      5,000.00  CAD   |
| Fees               0.00  CAD   |
| Realized       8,000.00  CAD   |
| Room 2026      7,000.00 / 7,000.00   0.00 left |
+--------------------------------+
```

**`Contributions` is the gross total you ever put into the pool**, counting only
true `CONTRIBUTION` rows. It is the figure to read when you want to know what you
have put on per account, per account type or across the whole folio.

**`Net Deposited` is how much of your own money the pool still holds:**

```text
contributions - withdrawals + what transfers carried in or out
```

Transfers move portions of what you contributed in and out of the portfolio. Since
we carry the entire transaction ledger, folio can smartly calculate the portion of
incoming or outgoing transfers that are *actual deposits*. This value is important to
know "how much of the money we put in, exists in this pool"; to compare against its
current-day state, in order to gauge the real performance of its holdings.

The `Room` line appears only for registered pools, and only once
[`contribution_room`](../configuration.md#contribution-room) is configured. It is
keyed by account type and year, because a CRA limit covers every account of that
type at once.

## Cache freshness

Two independent caches feed this table, and the header ages both:

```text
* acb computed just now . ~ quotes cached 15m ago
~ acb cached 4d ago . ! quotes cached 6d ago (offline)
```

A quote past its TTL is highlighted, because a stale price silently makes every
market value and unrealized gain on the page wrong.

## Diagnostics

A pool whose units the replay knows to be wrong (an `OVERSELL`, or a position
ending below zero) is **badged**, and the footer says so. Those units are not
presented as fact. Run [`folio check`](checking.md) to find the missing row.

## Export

`--export PATH` writes every computed field per holding, choosing the format from
the suffix (`.csv` or `.xlsx`). Only the displayed scope's holdings are written.

```bash
folio dash --export holdings.csv
folio dash --type tfsa --export tfsa.xlsx
```
