# Folio Updater

A template for a portfolio updater project.

## Features

- Reads excel data

## Requirements

See `requirements.txt` for the full list of dependencies.

## Setup

1. Clone the repository.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Configuration (`config.yaml`)

This project uses a `config.yaml` file at the root of the repository.  
It is **auto-generated** with default values the first time you run the application.

### Default structure

```yaml
folio_path: data/folio.xlsx
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
```

| Key                   | Description                                                                                                                                                                                                                                                            |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`folio_path`**      | Path to your portfolio Excel file. If this is **relative**, it is treated as relative to the project root (default: `data/folio.xlsx`). If you set an **absolute path** (e.g. `C:/Finance/folio.xlsx`), the project will use it directly without creating any folders. |
| **`log_level`**       | Sets the application’s logging verbosity. Recommended values: ERROR for minimal user-facing logs, INFO for normal operation details, DEBUG for full development troubleshooting.                                                                                       |
| **`sheets`**          | A mapping of logical sheet names (keys) to actual Excel sheet names (values). This allows you to rename sheets without touching the code.                                                                                                                              |
| **`header_keywords`** | Maps internal field names (left) to a list of possible header variations that might appear in your Excel Txns sheet. This allows the importer to automatically match differently-named columns to the required internal schema.                                        |

Essential fields (internal names):

- `TxnDate` - transaction date (YYYY-MM-DD)
- `Action` - BUY or SELL
- `Amount` - Total amount (Price * Units)
- `$` - currency code (e.g., USD)
- `Price` - price per unit
- `Units` - number of units
- `Ticker` - security symbol

Flexibility:

- These columns may be named differently and appear in any order in your Excel `Txns` sheet.
- `config.yaml` contains `header_keywords` to map Excel header labels to the internal fields.
- The app will attempt to map headers; if any essential field cannot be matched, import will fail with a clear error.

Default behavior:

- If `data/folio.xlsx` does not exist, the app will create a default file.

## Usage

Run the notebook `tickers.ipynb` to test.

## Development

### Python Dependency Management

- **[uv](https://github.com/astral-sh/uv)** – Manage project dependencies and virtual environments.

  Recommended usage:
  
  ```bash
  # Sync all dependencies into your local .venv
  uv sync --all-groups
  
  # Add new dependencies to the project
  uv add <package-name>

### Code Searching Tool

- **ripgrep (`rg`)** – A fast, recursive search tool for code and text.

### Setting up nbstripout for Contribution

```bash
nbstripout --install
```

This will automatically strip output cells from Jupyter notebooks before committing changes.

Note: Add to IDE path (.venv $env:PATH) if needed by virtual environment terminal.
