# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Features

### Import Transactions

- **[Account Management](docs/transactions/account-management.md)**: Support for multiple account aliases/identifiers
- **Data Validation**: Comprehensive data formatting and constraint checking
- **Duplicate Detection**: Duplicate filtering both within imports and against existing data
- **[Duplicate Approval](docs/transactions/duplicate-approval.md)**: Manual approval mechanism for legitimate duplicate transactions
- **Flexible Schema**: Dynamic column addition while maintaining essential field ordering
- **[Logging](docs/transactions/import-logging.md)**: Comprehensive import logging

## Setup

1. Clone the repository
2. Install dependencies using **[uv](https://github.com/astral-sh/uv)**:

   ```bash
   uv sync
   ```

## Configuration (`config.yaml`)

This project uses a `config.yaml` file at the root of the repository.  
It is **auto-generated** with default values the first time you run the application.

### Default structure

```yaml
folio_path: data/folio.xlsx
db_path: data/folio.db
log_level: ERROR  # DEBUG, INFO, WARNING, ERROR, CRITICAL
sheets:
  tickers: Tickers
  txns: Txns
header_keywords:
  TxnDate: [txndate, transaction date, date]
  Action: [action, type, activity]
  Amount: [amount, value, total]
  $: [$, currency, curr]
  Price: [price, unit price, share price]
  Units: [units, shares, qty, quantity]
  Ticker: [ticker, symbol, stock]
  Account: [account, alias, account id]
header_ignore: []
duplicate_approval:
  column: Duplicate
  value: OK
optional_headers:
  Fees: numeric
  Settle Date: date
  Notes: string
```

| Key                      | Description                                                                                                                                                                                                                                                            |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`folio_path`**         | Path to your portfolio Excel file. If this is **relative**, it is treated as relative to the project root (default: `data/folio.xlsx`). If you set an **absolute path** (e.g. `C:/Finance/folio.xlsx`), the project will use it directly without creating any folders. |
| **`db_path`**            | Path to the internal database file. This will be automatically created if it does not exist. Relative paths will behave similar to the `folio_path`                                                                                                                    |
| **`log_level`**          | Sets the application's logging verbosity. Recommended values: ERROR for minimal user-facing logs, INFO for normal operation details, DEBUG for full development troubleshooting.                                                                                       |
| **`sheets`**             | A mapping of logical sheet names (keys) to actual Excel sheet names (values). This allows you to rename sheets without touching the code.                                                                                                                              |
| **`header_keywords`**    | Maps internal field names (left) to a list of possible header variations that might appear in your Excel Txns sheet. This allows the importer to automatically match differently-named columns to the required internal schema.                                        |
| **`header_ignore`**      | List of column names to ignore during import. Essential columns cannot be ignored even if listed here.                                                                                                                                                                 |
| **`duplicate_approval`** | Configuration for the duplicate approval feature. See [Duplicate Configuration](docs/transactions/duplicate-approval.md/#configuration) for more details.                                                                                                              |
| **`optional_headers`**   | Optional: configure additional columns with specific data types for automatic formatting. Keys are column names, values are data types: `date`, `numeric`, `currency`, `action`, or `string`. These fields won't cause import failures if missing or invalid. |

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

### Flexibility

- These columns may be named differently and appear in any order in your Excel `Txns` sheet
- `config.yaml` contains `header_keywords` to map Excel header labels to the internal fields
- The app will attempt to map headers; if any essential field cannot be matched, import will fail with a clear error
- If the `Account` column is missing but an account parameter is provided to the import function, it will be used as a fallback

### Default Behavior

- If `data/folio.xlsx` does not exist, the app will create a default file with sample data

## Usage

Explore the notebooks in the `notebooks/` directory to see the system in action.

## Development

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
