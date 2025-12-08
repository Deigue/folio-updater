# Settlement Date Auto-Calculation

The folio-updater automatically calculates settlement dates for transactions based on transaction type and market calendars. This feature provides two internal data fields that are automatically managed by the system.

## Overview

Settlement dates represent when a transaction is officially settled in the market. Different transaction types have different settlement rules, and the system tries to automatically calculate these dates using business day logic and market calendars.

## Internal Data Fields

The system adds two internal fields to track settlement dates:

### SETTLE_DATE

- **Type**: Date (YYYY-MM-DD format)
- **Description**: The calculated or provided settlement date for the transaction

### SETTLE_CALCULATED

- **Type**: Integer (0 or 1)
- **Description**: Flag indicating whether the settlement date was auto-calculated
- **Values**:
  - `1`: Settlement date was auto-calculated by the system
  - `0`: Settlement date was provided in the import data and preserved

## Settlement Rules

### Same-Day Settlement

These transaction types settle on the same day as the transaction date:

- **DIVIDEND**: Dividend payments
- **BRW**: Borrowing transactions
- **CONTRIBUTION**: Account contributions
- **FCH**: Fee charges
- **ROC**: Return of capital
- **WITHDRAWAL**: Account withdrawals

### T+1/T+2 Settlement

These transaction types settle on business days after the transaction date:

- **BUY**: Stock purchases
- **SELL**: Stock sales
- **FXT**: Foreign exchange transactions
- **SPLIT**: Stock splits

#### Settlement Period Rules

- **T+1 (1 business day)**: Effective May 28, 2024 for USD transactions and May 27, 2024 for CAD transactions
- **T+2 (2 business days)**: For transactions before the T+1 effective dates

## Market Calendar Integration

The system uses market calendars to ensure accurate business day calculation:

- **Market Holidays**: Uses `pandas-market-calendars` library for official market holidays
- **Business Days**: Automatically accounts for weekends and market-specific holidays
- **Timezone**: All calculations use America/Toronto timezone

### Supported Markets

- **NYSE**: New York Stock Exchange calendar for USD transactions
- **TSX**: Toronto Stock Exchange calendar for CAD transactions

## Configuration

### Header Keywords

Settlement dates can be imported from Excel files using configurable header keywords:

```yaml
header_keywords:
  SettleDate: ["settledate", "settlement date", "settle"]
```

### Data Validation

- Settlement dates must follow YYYY-MM-DD format
- Invalid dates (like "INVALID_DATE") are automatically recalculated

## Import Behavior

### When Settlement Dates Are Provided

1. **Valid dates**: Preserved as-is, `settle_calculated = 0`
2. **Invalid dates**: Recalculated using settlement rules, `settle_calculated = 1`
3. **Missing/empty**: Auto-calculated using settlement rules, `settle_calculated = 1`

### When Settlement Dates Are Not Provided

- Settlement date columns are automatically added to the database
- All settlement dates are auto-calculated based on transaction data
- All records have `settle_calculated = 1`

## CLI Commands

### Query Settlement Statistics

Use the `settle-info` command to view settlement date statistics:

```bash
folio settle-info
```

This will provide the number of transactions where settlement date was calculated.
It will also list these transactions for review.

### Logging

Settlement date calculations are logged at DEBUG level:

```log
Settlement date calculated for transaction: 2024-06-15 -> 2024-06-17
```

Enable debug logging to see detailed calculation information.
