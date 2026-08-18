# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Features

### CLI Tool

**Folio** is now available as a command-line tool for managing your portfolio:

- **`folio import`**: Import transactions from files (Broker statements, Excel sheets, CSVs, etc.)
- **[`folio add`](docs/commands/adding-transactions.md)**: Manually add a single transaction
- **[`folio edit`](docs/commands/editing-transactions.md)**: Edit transactions, one at a time or in batch
- **[`folio delete`](docs/commands/deleting-transactions.md)**: Delete transactions, one at a time or in batch
- **[`folio getfx`](docs/commands/forex-rates.md)**: Update foreign exchange rates automatically
- **`folio generate`**: Generate the latest portfolio from the database
- **`folio demo`**: Create a demo portfolio with mock data for testing
- **[`folio settle-info`](docs/commands/settlement-info.md)**: Retrieve and update settlement date information
- **`folio download`**: Download statements directly from brokers ([Interactive Brokers](docs/commands/download/ibkr-integration.md), [Wealthsimple](docs/commands/download/wealthsimple-integration.md))
- **`folio symbol`**: Alias ticker symbols that are renamed or different to be treated the same
- **[`folio acb`](docs/commands/cost-base.md)**: Adjusted cost base buildup for a symbol, pooled by account, account type or portfolio
- **[`folio query`](docs/commands/querying.md)**: Search and filter transactions using natural language or explicit filters
- **[`folio check`](docs/commands/checking.md)**: Check the folio for missing or inconsistent transactions
- **`folio version`**: Show the version of the folio-updater and file paths.

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
- **[Forex Rate Export](docs/commands/forex-rates.md)**: Automatic FX Rate management

## Usage

1. Download and extract `folio-windows-x64.zip`
2. Run `folio.exe --help` to see available commands

Once installed, you can use the `folio` command-line tool. See the command list above (with linked docs) or run `folio <command> --help` for usage details.

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

- **[uv](https://github.com/astral-sh/uv)**: Manage project dependencies and virtual environments.

  Recommended usage:

  ```bash
  # Sync all dependencies into your local .venv
  uv sync --all-groups

  # Add new dependencies to the project
  uv add <package-name>
  ```

### Code Quality Tools

- **ripgrep (`rg`)**: A fast, recursive search tool for code and text
- **Linting and formatting**: Configured via project settings using `ruff`
- **Type checking**: Configured via project settings using **[ty](https://github.com/astral-sh/ty)**.

Before submitting changes, run both checks and make sure they're clean:

```bash
uv run ruff check
uv run ty check
```

> [!TIP]
> In VS Code, you can run the **`lint (ruff + ty)`** task instead.

### Testing

Run the test suite after making changes for testing and coverage.

```bash
uv run pytest --cov
```

> [!TIP]
> In VS Code, the **`coverage`** task is available for the same `--cov`
> check with a `term-missing` report.

### Setting up nbstripout for Contribution

```bash
nbstripout --install
```

This will automatically strip output cells from Jupyter notebooks before committing changes.

> [!TIP]
> Add to IDE path (.venv $env:PATH) if needed by virtual environment terminal.
