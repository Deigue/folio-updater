# Account Management

This document explains how the folio-updater system handles account management and the Account column in transaction data.

## Overview

The Account column serves as an essential identifier that distinguishes transactions based on where they occurred. This is crucial for portfolios that span multiple accounts or brokers, as transactions might otherwise appear identical but actually represent different account activities.

## Account Column Mapping

The system automatically maps Excel columns to the internal Account field using the `header_keywords` configuration:

```yaml
header_keywords:
  Account: [account, alias, account id]
```

Common variations that will be automatically recognized:

- "Account"
- "Alias"
- "Account ID"
- "AccountId"
- "account_id"

## Fallback Account Parameter

When importing transactions where the Account column is missing from the Excel file, you can provide a fallback account identifier:

```python
from importers.excel_importer import import_transactions

# Import with account fallback
imported_count = import_transactions(excel_path, account="BROKER-MAIN")
```

### Behavior

- If Account column exists in Excel: Uses the Excel values
- If Account column is missing AND account parameter provided: Uses the parameter value for all rows
- If Account column is missing AND no account parameter: Import fails with error

## Use Cases

### Multiple Brokers

```excel
TxnDate     | Action | Amount  | Ticker | Account
2023-01-01  | BUY    | 1000.00 | AAPL   | BROKER-A
2023-01-01  | BUY    | 1000.00 | AAPL   | BROKER-B
```

These are treated as separate transactions despite having identical other fields.

### Account Migration

When moving from an old system that doesn't track accounts:

```python
# Import old data with account identifier
import_transactions("legacy_data.xlsx", account="LEGACY-MAIN")
```

### File-per-Account Structure

If you maintain separate Excel files per account:

```python
# Import multiple account files
import_transactions("broker_a_txns.xlsx", account="BROKER-A") 
import_transactions("broker_b_txns.xlsx", account="BROKER-B")
```

## Duplicate Detection

The Account field is included in the synthetic key generation for duplicate detection. This means:

```python
# These are considered DIFFERENT transactions
Row 1: Date=2023-01-01, Action=BUY, Amount=1000, Account=BROKER-A
Row 2: Date=2023-01-01, Action=BUY, Amount=1000, Account=BROKER-B

# These are considered DUPLICATE transactions  
Row 1: Date=2023-01-01, Action=BUY, Amount=1000, Account=BROKER-A
Row 2: Date=2023-01-01, Action=BUY, Amount=1000, Account=BROKER-A
```
