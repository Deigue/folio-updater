# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Features

### CLI Tool

**Folio** is now available as a command-line tool for managing your portfolio:

- **`folio import`**: Import transactions from files
- **`folio add`**: Manually add a single transaction
- **`folio getfx`**: Update foreign exchange rates automatically
- **`folio generate`**: Generate the latest portfolio
- **`folio demo`**: Create a demo portfolio with mock data for testing
- **`folio settle-info`**: Retrieve and update settlement date information
- **`folio download`**: Download statements from brokers (e.g., Interactive Brokers)
- **`folio tickers`**: Manage ticker symbol aliases
- **`folio query`**: Search and filter transactions using natural language or explicit filters
- **`folio version`**: Show the version of the folio-updater

### Import and Processing Features

- **[Account Management](docs/import/account-management.md)**: Support for multiple account aliases/identifiers
- **Data Validation**: Comprehensive data formatting and constraint checking
- **Duplicate Detection**: Duplicate filtering both within imports and against existing data
- **[Duplicate Approval](docs/import/duplicate-approval.md)**: Manual approval mechanism for legitimate duplicate transactions
- **[Transaction Transformation](docs/import/transformations.md)**:  Apply custom rules to transform transactions
- **[Merge Transforms](docs/import/merge-transforms.md)**: Automatically combine transactions based on custom defined rules
- **[Settlement Date Calculation](docs/import/settlement-dates.md)**: Uses market calendars to estimate settlement dates for transactions
- **Flexible Schema**: Dynamic column addition while maintaining essential field ordering
- **Logging**: Comprehensive audit trail of import operations
- **Automatic Backup**: All updates are automatically backed up (configurable)

### Export Functionality

- **Transaction Export**: Export transactions from database to Excel sheets
- **[Forex Rate Export](docs/forex-rates.md)**: Automatic FX Rate management

### Download Statements

- **[Interactive Brokers Integration](docs/download/ibkr-integration.md)**: Download Flex query statements directly using IBKR Flex API
- **[Wealthsimple Integration](docs/download/wealthsimple-integration.md)**: Download transactions from Wealthsimple accounts

## Usage

  1. Download and extract `folio-windows-x64.zip`
  2. Run `folio.exe --help` to see available commands

Once installed, you can use the `folio` command-line tool:

### Import Transactions

Import transaction files into your portfolio:

```bash
# Default: Import all files from the default import directory
folio import

# Import specific file
folio import --file path/to/your/transactions.xlsx

# Import all files from a custom directory
folio import --dir C:\path\to\import\folder
```

### Add Transactions Manually

Insert a single transaction directly into your folio.

```bash
# Fully specified
folio add --action BUY --ticker AAPL --date 2025-08-15 \
          --account TFSA --currency USD \
          --amount -1502.50 --price 150.25 --units 10

# Supply only the action and get prompted for what that action requires
folio add --action ROC
```

Refer to [Adding Transactions Manually](docs/adding-transactions.md) for detailed information.

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
# Check current settlement date info
folio settle-info

# Import settlement date info from downloaded statements
folio settle-info --import

# Import specified monthly statement
folio settle-info --import -f path/to/statement.xlsx
```

Calculated settlement dates can be updated with actual values by importing broker monthly statements.

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

Download transaction information directly from brokers.

```bash
folio download --broker ibkr --from 2024-01-01 --to 2024-12-31
folio download --broker wealthsimple --statement --from 2024-04-01
```

*Refer to [IBKR Integration Usage](docs/download/ibkr-integration.md#usage) and [Wealthsimple Integration Usage](docs/download/wealthsimple-integration.md#usage) for detailed information.*

### Ticker Alias Management

Ticker symbols could be renamed, or represented differently across brokers. This command allows you to alias tickers
so they are treated as the same security internally.

```bash
folio tickers --add SPLG SPYM 2025-10-31
folio tickers --list
folio tickers --delete SPLG
```

### Query Transactions

Search and filter transactions directly from the command line, using natural language terms, explicit column filters, or a mix of both.

```bash
folio query AAPL BUY last year
folio query Account~RRSP TxnDate>=2024-01-01 sort:-Amount first 10
```

*Refer to [Smart Transaction Querying](docs/querying.md) for the full syntax, including natural language dates, sorting, and advanced filters.*

## Configuration

The folio-updater uses a `config.yaml` file to manage configurations.
It is **auto-generated** with default values the first time you run the application, so you don't need to write one by hand to get started.

It controls things like file paths, logging, how Excel columns are matched to internal fields, duplicate approval, backups, transaction transforms, and broker settings.

*See [Configuration Reference](docs/configuration.md) for the full example file, a key-by-key breakdown, and details on essential/internal fields.*

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
