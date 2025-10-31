# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Features

### CLI Tool

**Folio** is now available as a command-line tool for managing your portfolio:

- **`folio import`**: Import transactions from files
- **`folio getfx`**: Update foreign exchange rates automatically
- **`folio generate`**: Generate the latest portfolio
- **`folio demo`**: Create a demo portfolio with mock data for testing
- **`folio version`**: Show the version of the folio-updater
- **`folio settle-info`**: Retrieve and update settlement date information
- **`folio download`**: Download statements from brokers (e.g., Interactive Brokers)

### Import and Processing Features

- **[Account Management](docs/transactions/account-management.md)**: Support for multiple account aliases/identifiers
- **Data Validation**: Comprehensive data formatting and constraint checking
- **Duplicate Detection**: Duplicate filtering both within imports and against existing data
- **[Duplicate Approval](docs/transactions/duplicate-approval.md)**: Manual approval mechanism for legitimate duplicate transactions
- **[Transaction Transformation](docs/transactions/transformations.md)**:  Apply custom rules to transform transactions
- **[Merge Transforms](docs/transactions/merge-transforms.md)**: Automatically combine transactions based on custom defined rules
- **[Settlement Date Calculation](docs/transactions/settlement-dates.md)**: Uses market calendars to estimate settlement dates for transactions
- **Flexible Schema**: Dynamic column addition while maintaining essential field ordering
- **Logging**: Comprehensive audit trail of import operations
- **Automatic Backup**: All updates are automatically backed up (configurable)

### Export Functionality

- **Transaction Export**: Export transactions from database to Excel sheets
- **[Forex Rate Export](docs/forex-rates.md)**: Automatic FX Rate management

### Download Statements

- **[Interactive Brokers Integration](docs/transactions/ibkr-integration.md)**: Download Flex query statements directly using IBKR Flex API

## Usage

  1. Download and extract `folio-windows-x64.zip`
  2. Run `folio.exe --help` to see available commands

Once installed, you can use the `folio` command-line tool:

### Import Transactions

Import transaction files into your portfolio:

```bash
# Default: Import from configured folio Excel file
folio import

# Import specific file
folio import --file path/to/your/transactions.xlsx

# Import all files from directory
folio import --dir C:\path\to\import\folder
```

### Update FX Rates

Keep your foreign exchange rates current:

```bash
folio getfx
```

This command automatically fetches latest FX rates and updates your portfolio. If no FX data exists, it performs a full historical export.

### Generate Portfolio

Creates portfolio Excel file

```bash
folio generate
```

This retrieves the latest data from the Parquet data files in the configured `data_path` and combines them into a Excel workbook at `folio_path`. Use this whenever you want to view or analyze your data in Excel.

### Create Demo Portfolio

Set up a demo portfolio with sample data:

```bash
folio demo
```

Perfect for testing and getting familiar with the system. Creates folio with sample transactions and FX rates.

### Settlement Date Info

Retrieve settlement date information:

```bash
folio settle-info
```

Calculated settlement dates can be updated with actual values by importing broker monthly statements. (.csv or .xlsx)

```bash
folio settle-info -f path/to/statement.xlsx
```

**Expected Statement Format:**

- `date`: Settlement date from the statement
- `amount`: Transaction amount (used for matching)
- `currency`: Transaction currency
- `transaction`: Action type (BUY, SELL, etc.)
- `description`: Contains ticker symbol, units, and original transaction date

**Statement Description Format Examples:**

- `"AAPL - BUY 100 SHARES ON 2024-01-15"`
- `"DOL - Dollarama Inc: Bought 1.0000 shares (executed at 2029-02-05)"`

### Download Transactions

*Refer to [IBKR Integration Usage](docs/transactions/ibkr-integration.md#usage) for detailed information.*

## Configuration

The folio-updater uses a `config.yaml` file to manage configurations.  
It is **auto-generated** with default values the first time you run the application.

### Example structure

```yaml
folio_path: data/folio.xlsx
data_path: data
log_level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
sheets:
  tickers: Tickers
  txns: Txns
  fx: FX
header_keywords:
  TxnDate: ["Txn Date", "Date", "Transaction Date", "TradeDate", "ReportDate"]
  Action: ["Action", "Type", "Activity", "Buy/Sell"]
  Amount: ["Amount", "Value", "Total", "Proceeds"]
  $: [ "$", "currency", "curr", "CurrencyPrimary"]
  Price: [ "price", "unit price", "share price", "Put/Call"]
  Units: [ "units", "shares", "qty", "quantity", "Multiplier"]
  Ticker: [ "ticker", "symbol", "stock", "security"]
  Account: ["account", "alias", "account id", "accountalias", "account name"]
  Fee: ["Fee", "Fees", "Commission"]
  SettleDate: ["SettleDate", "Settlement Date", "Settle"]
header_ignore: ["ID", "ClientAccountID", "OtherCommission", "Account Number", "Expiry"]
duplicate_approval:
  column: Duplicate
  value: OK
backup:
  enabled: true
  path: backups
  max_backups: 50
optional_columns:
  Description:
    keywords: ["Description"]
    type: string
transforms:
  rules:
  - conditions:
      Action: ["BUY", "SELL"]
      Ticker: ["USD.CAD"]
    actions:
      Action: "FXT"
      Ticker: ""
  - conditions:
      Action: ["DIVIDEND"]
    actions:
      Fee: 0
  - conditions:
      Action: "Deposits/Withdrawals"
      Description: "CASH RECEIPTS / ELECTRONIC FUND TRANSFERS"
    actions:
      Action: "CONTRIBUTION"
  merge_groups:
    - name: "Dividend Withholding Tax Merge"
      match_fields: ["TxnDate", "Account", "Ticker"]
      source_actions: ["Dividends", "Withholding Tax"]
      target_action: "DIVIDEND"
      amount_field: "Amount"
      operations:
        Fee: 0
        Units: 0
brokers:
  ibkr:
    FlexReport: "FLEX_QUERY_ID_FOR_TRADES"
    CashActivity: "FLEX_QUERY_ID_FOR_CASH_ACTIVITIES"
  wealthsimple:
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
```

| Key                      | Description                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`folio_path`**         | Path to your portfolio Excel file. If this is **relative**, it will be based on the path of the executable. If you set an **absolute path** (e.g. `C:/Finance/folio.xlsx`), the project will use it directly without creating any folders. Generate this with `folio generate`.                                         |
| **`data_path`**          | Folder path where data files are stored (automatically created if it does not exist). Relative paths will behave similar to the `folio_path`. Default: `data`                                                                                                                                                           |
| **`log_level`**          | Sets the application's logging verbosity. Recommended values: ERROR for minimal user-facing logs, INFO for normal operation details, DEBUG for full development troubleshooting.                                                                                                                                        |
| **`sheets`**             | A mapping of logical sheet names (keys) to actual Excel sheet names (values). This allows you to rename sheets without touching the code.                                                                                                                                                                               |
| **`header_keywords`**    | Maps internally recognized field names (left) to a list of header variations that might appear in your Excel Txns sheet. This allows the importer to automatically match differently-named columns to the required internal schema.                                                                                     |
| **`header_ignore`**      | List of column names to ignore during import. Essential columns cannot be ignored even if listed here.                                                                                                                                                                                                                  |
| **`duplicate_approval`** | Configuration for the duplicate approval feature. See [Duplicate Configuration](docs/transactions/duplicate-approval.md/#configuration) for more details.                                                                                                                                                               |
| **`backup`**             | Backup configuration settings. `enabled` (boolean): Enable/disable backups (default: true). `path` (string): Backup directory path, relative to project root or absolute (default: "backups"). `max_backups` (integer): Maximum number of backup files to keep (default: 50).                                           |
| **`optional_columns`**   | Optional: configure additional columns with specific data types and header mapping. Each key is the resolved column name, with `keywords` (list of header names to match) and `type` (data type: `date`, `numeric`, `currency`, `action`, or `string`). These fields won't cause import failures if missing or invalid. |
| **`transforms`**         | Transaction transformation rules to automatically modify imported data. See [Transaction Transformations](docs/transactions/transformations.md) and [Merge Transforms](docs/transactions/merge-transforms.md) for more details.                                                                                         |
| **`brokers`**            | Configure broker-specific information. See [IBKR Configuration](docs/transactions/ibkr-integration.md#configuration) for more details.                                                                                                                                                                                  |

### Essential Fields

Essential fields (internal names) that must be present in your Excel data:

- `TxnDate` - transaction date (YYYY-MM-DD)
- `Action` - transaction type (BUY, SELL, DIVIDEND, CONTRIBUTION, etc.)
- `Amount` - Total amount (Price * Units)
- `$` - currency code (e.g., USD, CAD)
- `Price` - price per unit
- `Units` - number of units
- `Ticker` - security symbol
- `Account` - account identifier/alias

### Internal Fields

The system automatically manages additional internal fields:

- `Fee` - Fees that may be associated with the transaction. Recognized internally as a numeric value.
- `SettleDate` - settlement date (auto-calculated based on transaction type and market rules)
- `SettleCalculated` - flag (0/1) indicating if settlement date was auto-calculated

> [!NOTE]
> See [Settlement Dates](docs/transactions/settlement-dates.md) for detailed information about automatic settlement date calculation.

### Flexibility

- These columns may be named differently and appear in any order in your Excel `Txns` sheet
- `config.yaml` contains `header_keywords` to map Excel header labels to the internal fields
- The app will attempt to map headers; if any essential field cannot be matched, import will fail with a clear error
- If the `Account` column is missing but an account parameter is provided to the import function, it will be used as a fallback

### Default Behavior

- If `data/folio.xlsx` does not exist, the app will create a default file with sample data

## Development

### Setup

1. Clone the repository
2. Install dependencies using **[uv](https://github.com/astral-sh/uv)**:

   ```bash
   uv sync --all-groups
   ```

3. Install the `folio` CLI tool:

   ```bash
   uv pip install -e .
   ```

### Python Dependency Management

- **[uv](https://github.com/astral-sh/uv)** – Manage project dependencies and virtual environments.

  Recommended usage:
  
  ```bash
  # Sync all dependencies into your local .venv
  uv sync --all-groups
  
  # Add new dependencies to the project
  uv add <package-name>
  ```

### Code Quality Tools

- **ripgrep (`rg`)** – A fast, recursive search tool for code and text
- **Linting and formatting** – Configured via project settings using `ruff`

### Setting up nbstripout for Contribution

```bash
nbstripout --install
```

This will automatically strip output cells from Jupyter notebooks before committing changes.

> [!TIP]
> Add to IDE path (.venv $env:PATH) if needed by virtual environment terminal.
