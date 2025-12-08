# Smart Transaction Querying

The folio-updater offers a flexible and powerful way to query transactions. You can use simple, natural language terms or precise, advanced filters to pinpoint the exact data you need.

## Smart Filtering

The easiest way to query is to combine natural language dates and general text search terms.

### 1. Natural Language Dates

Use common phrases to filter by date.

```sh
# Ranges from a point in the past:
folio query last 7 months
folio query 3 weeks ago
folio query 3 months, 1 week and 3 days ago
folio query since 2022
folio query last year
folio query this year
folio query after November
folio query between 2021 and 2023
folio query between april and september
folio query between october 13 and now
# Specific single days:
folio query yesterday
folio query august 10 2024
folio query 2025-11-01
```

### 2. General Text Search

Terms not related to dates are treated as text searches across key columns

- All searches are case insensitive
- Check match exactly with Ticker, Action, Currency, Account
- Partial matches for Account names
- Apply search to other optional text columns like Description

```sh
folio query MSFT
folio query TFSA
folio query AAPL BUY TFSA
# find all dividends for VOO in accounts containing "PERSON"
folio query DIVIDEND VOO PERSON
folio query USD
folio query CASH RECEIPTS
```

### 3. Combining Smart Filters

You can mix dates and text to quickly narrow down results.

```bash
folio query AAPL PERSO last year
folio query BUY last 3 days
folio query DIVIDEND since 2023
folio query QQQM last 4 months BUY
```

---

## Sorting Results

Control the order of the results using the `sort:` prefix.

- **Syntax:** `sort:column` for ascending, `sort:-column` for descending.
- **Default Order:** If no sort is specified, results are sorted by `TxnDate` descending.
- **Multiple Sorts:** You can provide multiple sort commands. They are applied in order.

```bash
folio query sort:-Amount
# Sort by Ticker (A-Z), then by Transaction Date (newest first):**
folio query sort:Ticker sort:-TxnDate
folio query AAPL PERSON last 2 years sort:units
```

---

## Advanced Filtering

For maximum precision, use explicit filters for specific columns and values.

### Explicit Filter Syntax

The format is `column_name` + `operator` + `value`.

| Operator | Description                    | Example                  |
| :------- | :----------------------------- | :----------------------- |
| `:`      | **Exact match**                | `Action:BUY`             |
| `~`      | **Contains** (for text)        | `Account~RRSP`           |
| `>`      | **Greater than**               | `Amount>1000`            |
| `<`      | **Less than**                  | `Price<50`               |
| `>=`     | **Greater than or equal to**   | `TxnDate>=2024-01-01`     |
| `<=`     | **Less than or equal to**      | `SettleDate<=2024-06-30` |

*(Note: Column names are case-insensitive, so `txndate` works the same as `TxnDate`)*

### Partial Date Filters

You can use explicit filters with partial dates for years or months.

- **Find all transactions in 2023:**

    ```bash
    folio query TxnDate>=2023 TxnDate<2024
    ```

- **Find all transactions in May 2024:**

    ```bash
    folio query TxnDate>=2024-05 TxnDate<2024-06
    ```

---

## Retrieve n transactions

We can limit the number of transactions returned using the `first n` or `last n` notation.

```bash
folio query last 5 
folio query QQQM PERSON from 2025-01 first 4
```

---

## Combining All Features

You can mix and match for ultimate control.

- **Find all `BUY` transactions for `AAPL` in your `PERSO` account from `last year` where the price was less than `150`:**

```bash
folio query BUY AAPL PERSO last year price<150 sort:-txndate first 7
folio query DIVIDEND MSFT since july 2023 sort:amount last 3
```
