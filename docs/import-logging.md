# Import Logging Documentation

This document explains the logging system for import operations in the folio-updater application.

## Overview

The application now features a dual-logging system:

1. **General Application Log** (`folio.log`) - Contains all application logs
2. **Import-Specific Log** (`importer.log`) - Contains detailed logging of all import operations for auditing and debugging

## Log Files Location

Both log files are stored in the `logs/` directory:

- `logs/folio.log` - General application logs (14 days retention)
- `logs/importer.log` - Import-specific logs (30 days retention for audit purposes)

## Import Logging Features

### 1. High-Level Import Summary

Every import operation logs:

- Start of import with file path
- Number of existing transactions in database before import
- Number of transactions read from Excel
- Number of transactions successfully imported after filtering
- Total transactions in database after import

Example:

```log
2024-09-01 10:30:15 [INFO] [excel_importer(46)] ============================================================
2024-09-01 10:30:15 [INFO] [excel_importer(47)] Starting import from: data/folio.xlsx
2024-09-01 10:30:15 [INFO] [excel_importer(51)] Existing transactions in database: 124
2024-09-01 10:30:15 [INFO] [excel_importer(59)] Read 25 transactions from Excel sheet 'Txns'
2024-09-01 10:30:15 [INFO] [excel_importer(76)] Import completed: 20 transactions imported
2024-09-01 10:30:15 [INFO] [excel_importer(77)] Total transactions in database: 144
2024-09-01 10:30:15 [INFO] [excel_importer(78)] ============================================================
```

### 2. Header Mapping Logging

The system logs how Excel column headers are mapped to internal field names:

```log
2024-09-01 10:30:15 [DEBUG] [header_mapping(67)] Excel->Internal mappings:
"Transaction Date" -> "TxnDate"
"Action" -> "Action"
"Amount" -> "Amount"
"Currency" -> "$"
"Price" -> "Price"
"Units" -> "Units"
"Ticker" -> "Ticker"
```

### 3. Transaction Detail Logging

All transactions being processed are logged with their essential field values:

```log
2024-09-01 10:30:15 [INFO] - TxnDate=2024-01-01|Action=BUY|Amount=1000.0|$=USD|Price=100.0|Units=10.0|Ticker=AAPL
2024-09-01 10:30:15 [INFO] - TxnDate=2024-01-02|Action=SELL|Amount=500.0|$=USD|Price=50.0|Units=10.0|Ticker=MSFT
```

### 4. Duplicate Transaction Logging

#### Intra-Import Duplicates

These are duplicates within the Excel file itself:

```log
2024-09-01 10:30:15 [INFO] Filtered 2 intra-import duplicate transactions.
2024-09-01 10:30:15 [INFO] - TxnDate=2024-01-01|Action=BUY|Amount=1000.0|$=USD|Price=100.0|Units=10.0|Ticker=AAPL
```

#### Database Duplicates  

These are transactions that already exist in the database:

```log
2024-09-01 10:30:15 [INFO] Filtered 3 database duplicate transactions.
2024-09-01 10:30:15 [INFO] - TxnDate=2024-01-02|Action=SELL|Amount=500.0|$=USD|Price=50.0|Units=10.0|Ticker=MSFT
```

### 5. Schema Management Logging

When new columns are added to the database:

```log
2024-09-01 10:30:15 [DEBUG] Added new column 'Notes' to table 'Txns'
2024-09-01 10:30:15 [DEBUG] Final ordered columns: ['TxnDate', 'Action', 'Amount', '$', 'Price', 'Units', 'Ticker', 'Notes']
```

## Usage

### Getting the Import Logger

```python
from utils.logging_setup import get_import_logger

import_logger = get_import_logger()
import_logger.info("Custom import message")
```

## Benefits

1. **Better Debugging**: Detailed logging of exactly which transactions were filtered and why
2. **Audit Trail**: Clear record of all import operations with 30-day retention
3. **Performance Monitoring**: Track import performance and patterns over time
4. **Separation of Concerns**: Import logs don't clutter general application logs

## Configuration

The logging system is automatically initialized when the application starts. The import logger:

- Uses the same log directory as the main application
- Rotates daily at midnight
- Keeps 30 days of import history (vs 14 days for general logs)
- Does not propagate to the root logger to avoid duplication in console output
