# Duplicate Approval

## Overview

The duplicate approval feature allows you to manually verify and import legitimate duplicate transactions while maintaining protection against accidental data duplication.

## Why Duplicate Approval?

Several banks and brokers make activity history available without providing an actual
unique identifier. Taking this into consideration, duplicate transactions can occur.

The system's default behavior is to prevent all duplicates, but sometimes you need to override this for known valid cases.

## How It Works

### Duplicate Detection Logic

The system identifies duplicates based on all transaction essentials:

- `TxnDate` - Transaction date
- `Action` - Transaction type (BUY, SELL, etc.)
- `Amount` - Total transaction amount
- `$` - Currency code
- `Price` - Price per unit
- `Units` - Number of units
- `Ticker` - Security symbol
- `Account` - Account identifier

If **all** these fields match between transactions, they are considered duplicates.

### Types of Duplicates

1. **Database Duplicates**: New transactions that match existing data in the database
2. **Intra-import Duplicates**: Multiple identical transactions within the same Excel file

### Approval Process

1. **Import Attempt**: Try to import your Excel file normally
2. **Review Logs**: Check the import logs for duplicate warnings
3. **Add Approval Column**: Add the configured approval column to your Excel file
4. **Mark Approved Duplicates**: Enter the approval value for legitimate duplicates
5. **Re-import**: Import the same file again - approved duplicates will be processed

## Configuration

Configure the approval mechanism in your `config.yaml`:

```yaml
duplicate_approval:
  column: Duplicate-OK    # Name of the approval column in Excel
  value: OK              # Value that indicates approval
```

### Customization Options

- **Column Name**: Change `column` to any name you prefer (e.g., "Approved", "Valid", "Import")
- **Approval Value**: Change `value` to any text (e.g., "YES", "VALID", "APPROVED")
- **Case Insensitive**: Approval values are case-insensitive ("ok", "OK", "Ok" all work)

## Example

### Initial Import

Your Excel file (`transactions.xlsx`):

```excel
TxnDate    | Action | Amount | $ | Price | Units | Ticker | Account
2024-01-15 | BUY    | 1000   | USD | 100 | 10    | AAPL   | BROKERAGE
2024-01-15 | BUY    | 1000   | USD | 100 | 10    | AAPL   | BROKERAGE
2024-01-16 | SELL   | 1050   | USD | 105 | 10    | AAPL   | BROKERAGE
```

**Import Result:**

- ❌ First AAPL BUY: **Rejected as duplicate**
- ❌ Second AAPL BUY: **Rejected as duplicate**
- ✅ AAPL SELL: Imported

**Log Output:**

```log
WARNING: Filtered 2 intra-import duplicate transactions.
WARNING:  - TxnDate=2024-01-15|Action=BUY|Amount=1000|$=USD|Price=100|Units=10|Ticker=AAPL|Account=BROKERAGE
WARNING:  - TxnDate=2024-01-15|Action=BUY|Amount=1000|$=USD|Price=100|Units=10|Ticker=AAPL|Account=BROKERAGE
```

### Adding Approval

Update your Excel file to approve the legitimate duplicate:

```excel
TxnDate    | Action | Amount | $ | Price | Units | Ticker | Account    | Duplicate-OK
2024-01-15 | BUY    | 1000   | USD | 100 | 10    | AAPL   | BROKERAGE  |
2024-01-15 | BUY    | 1000   | USD | 100 | 10    | AAPL   | BROKERAGE  | OK
2024-01-16 | SELL   | 1050   | USD | 105 | 10    | AAPL   | BROKERAGE  |
```

### Re-import

**Import Result:**

- ❌ First AAPL BUY: **Rejected as duplicate**
- ✅ Second AAPL BUY: **Approved duplicate imported**
- ✅ AAPL SELL: Already in database (skipped)

**Log Output:**

```log
WARNING: Filtered 1 intra-import duplicate transactions.
WARNING:  - TxnDate=2024-01-15|Action=BUY|Amount=1000|$=USD|Price=100|Units=10|Ticker=AAPL|Account=BROKERAGE
INFO   : Approved 1 intra-import duplicate transactions.
INFO   :  - TxnDate=2024-01-15|Action=BUY|Amount=1000|$=USD|Price=100|Units=10|Ticker=AAPL|Account=BROKERAGE
```

## Best Practices

### Review Before Approval

Always review duplicate warnings carefully:

1. **Check transaction details**: Ensure duplicates are truly legitimate
2. **Verify dates and amounts**: Look for typos or data entry errors
3. **Consider business logic**: Do multiple identical trades make sense?

### Logging and Audit

- Import logs provide full audit trail of all duplicate decisions
- Approved duplicates are clearly marked in logs for future reference
- Database maintains separate records for each approved duplicate
