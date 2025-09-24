# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Features

### CLI Tool

**Folio** is now available as a command-line tool for managing your portfolio:

- **`folio import`**: Import transactions from files
- **`folio getfx`**: Update foreign exchange rates automatically  
- **`folio demo`**: Create a demo portfolio with mock data for testing
- **`folio version`**: Show the version of the folio-updater

### Import and Processing Features

- **[Account Management](docs/transactions/account-management.md)**: Support for multiple account aliases/identifiers
- **Data Validation**: Comprehensive data formatting and constraint checking
- **Duplicate Detection**: Duplicate filtering both within imports and against existing data
- **[Duplicate Approval](docs/transactions/duplicate-approval.md)**: Manual approval mechanism for legitimate duplicate transactions
- **Flexible Schema**: Dynamic column addition while maintaining essential field ordering
- **[Logging](docs/transactions/import-logging.md)**: Comprehensive import logging
- **Automatic Backup**: All updates are automatically backed up

### Export Functionality

- **Transaction Export**: Export transactions from database to Excel sheets
- **[Forex Rate Export](docs/forex-rates.md)**: Automatic FX Rate management

## Usage

  1. Download and extract `folio-windows-x64.zip`
  2. Run `folio.exe --help` to see available commands

Once installed, you can use the `folio` command-line tool:

### Import Transactions

Import transaction files into your portfolio:

```bash
# Default: Import from configured folio Excel file
folio import

# Import specific file and export new transactions to folio Excel
folio import --file path/to/your/transactions.xlsx

# Import all files from directory and export new transactions to folio Excel  
folio import --dir C:\path\to\import\folder
```

### Update FX Rates

Keep your foreign exchange rates current:

```bash
folio getfx
```

This command automatically fetches latest FX rates and updates your portfolio. If no FX data exists, it performs a full historical export.

### Create Demo Portfolio

Set up a demo portfolio with sample data:

```bash
folio demo
```

Perfect for testing and getting familiar with the system. Creates folio with sample transactions and FX rates.

## Configuration

This project uses a `config.yaml` file at the root of the repository.  
It is **auto-generated** with default values the first time you run the application.

### Example structure

```yaml
folio_path: data/folio.xlsx
db_path: data/folio.db
log_level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
sheets:
  tickers: Tickers
  txns: Txns
  fx: FX
header_keywords:
  TxnDate: ["Txn Date", "Transaction Date", "TradeDate"]
  Action: ["Action", "Type", "Activity", "Buy/Sell"]
  Amount: ["Amount", "Value", "Total", "Proceeds"]
  $: [ "$", "currency", "curr", "CurrencyPrimary"]
  Price: [ "price", "unit price", "share price" ]
  Units: [ "units", "shares", "qty", "quantity" ]
  Ticker: [ "ticker", "symbol", "stock", "security"]
  Account: ["account", "alias", "account id", "accountalias"]
header_ignore: ["ID", "ClientAccountID", "OtherCommission"]
duplicate_approval:
  column: Duplicate
  value: OK
backup:
  enabled: true
  path: backups
  max_backups: 50
optional_columns:
  Fee:
    keywords: ["Fee", "Commission"]
    type: numeric
  SettleDate:
    keywords: ["SettleDate"]
    type: date
  Description:
    keywords: ["Description"]
    type: string

```

| Key                      | Description                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`folio_path`**         | Path to your portfolio Excel file. If this is **relative**, it is treated as relative to the project root (default: `data/folio.xlsx`). If you set an **absolute path** (e.g. `C:/Finance/folio.xlsx`), the project will use it directly without creating any folders.                                                  |
| **`db_path`**            | Path to the internal database file. This will be automatically created if it does not exist. Relative paths will behave similar to the `folio_path`                                                                                                                                                                     |
| **`log_level`**          | Sets the application's logging verbosity. Recommended values: ERROR for minimal user-facing logs, INFO for normal operation details, DEBUG for full development troubleshooting.                                                                                                                                        |
| **`sheets`**             | A mapping of logical sheet names (keys) to actual Excel sheet names (values). This allows you to rename sheets without touching the code.                                                                                                                                                                               |
| **`header_keywords`**    | Maps internally recognized field names (left) to a list of header variations that might appear in your Excel Txns sheet. This allows the importer to automatically match differently-named columns to the required internal schema.                                                                                     |
| **`header_ignore`**      | List of column names to ignore during import. Essential columns cannot be ignored even if listed here.                                                                                                                                                                                                                  |
| **`duplicate_approval`** | Configuration for the duplicate approval feature. See [Duplicate Configuration](docs/transactions/duplicate-approval.md/#configuration) for more details.                                                                                                                                                               |
| **`backup`**             | Backup configuration settings. `enabled` (boolean): Enable/disable backups (default: true). `path` (string): Backup directory path, relative to project root or absolute (default: "backups"). `max_backups` (integer): Maximum number of backup files to keep (default: 50).                                           |
| **`optional_columns`**   | Optional: configure additional columns with specific data types and header mapping. Each key is the resolved column name, with `keywords` (list of header names to match) and `type` (data type: `date`, `numeric`, `currency`, `action`, or `string`). These fields won't cause import failures if missing or invalid. |

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

> **Note**
> Not all fields under `header_keywords` are necessarily essential.
> Certain fields may be specifyable under `header_keywords` that have internal logic
> associated to them like formatting, calculations etc; but may not be critical to
> importing.

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

Note: Add to IDE path (.venv $env:PATH) if needed by virtual environment terminal.
