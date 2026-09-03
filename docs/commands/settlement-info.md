# Settlement Date Info

Retrieve and update settlement date information for transactions.

```bash
# Check current settlement date info
folio settle-info

# Import settlement date info from downloaded statements
folio settle-info --import

# Import specified monthly statement
folio settle-info --import -f path/to/statement.xlsx

# List every statement row and how it was resolved
folio settle-info --import --verbose
```

Calculated settlement dates can be updated with actual values by importing broker monthly statements.

## Expected Statement Format

- `date`: Settlement date from the statement
- `amount`: Transaction amount (used for matching)
- `currency`: Transaction currency
- `transaction`: Action type (BUY, SELL, etc.)
- `description`: Contains ticker symbol, units, and original transaction date

## Overriding a Date by Hand

Only transactions still carrying a *calculated* settlement date are considered,
so anything set by hand is left alone. To set one yourself, use `folio edit`
with `SettleDate`: supplying the date marks it authoritative and takes the row
out of every later statement import.

## Statement Description Format Examples

- `"AAPL - BUY 100 SHARES ON 2024-01-15"`
- `"DOL - Dollarama Inc: Bought 1.0000 shares (executed at 2029-02-05)"`
