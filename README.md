# Folio Updater

A portfolio management system that imports and processes financial transaction data from Excel files into a SQLite database.

## Quick start

Download and extract `folio-windows-x64.zip`, then open a terminal in that folder.
The examples below use `folio`; use `.\folio.exe` for the extracted Windows executable.
Developers can install the CLI with the setup instructions below.

### Try it with a demo

Use a separate extracted copy or development checkout for the demo so it has its own data and configuration.

```bash
folio demo
folio dash
folio ticker AAPL
folio query last 10
```

`demo` downloads real market history and builds two years of sample transactions, then writes a workbook.
It needs network access and only creates the sample folio when the database is empty.
Use `folio version` to see the configuration, database folder and workbook paths.
When you later import real data over the demo, folio asks before replacing it.

### Use it with your accounts

1. Run `folio version` to create the default `config.yaml` and locate it.
2. Follow the [IBKR setup guide](docs/commands/download/ibkr-integration.md) for Flex Query IDs and a token, or the [Wealthsimple setup guide](docs/commands/download/wealthsimple-integration.md) for account names and authentication.
3. Download your first history range explicitly, replacing the example date and choosing your broker:

   ```bash
   folio download --broker ibkr --from 2024-01-01
   # Or:
   folio download --broker wealthsimple --from 2024-01-01
   folio import
   folio check
   folio generate
   ```

4. For regular use with both brokers configured, run `folio update`: it downloads, imports, checks settlement information, runs checks, writes the workbook and shows the result.

`update` currently contacts IBKR and then Wealthsimple. If you use only one broker, keep using its `download` command followed by `import`, `check` and `generate`.
After correcting a rejected downloaded file, `folio update --resume` continues from waiting files without downloading again.
`folio update --offline` also uses only cached quotes and exchange rates.

## Which command shows what

| You want to see | Command |
| --- | --- |
| Current holdings, cash, gains and flows | `folio dash` |
| One security's details, or a comparison | `folio ticker SYMBOL` |
| Stored transactions matching a selection | `folio query last 10` |
| Cost-base buildup for one security | `folio acb SYMBOL` |
| Missing or inconsistent transactions | `folio check` |
| Cached prices and their freshness | `folio quotes` |
| A workbook of the folio | `folio generate` |
| Installed version and data paths | `folio version` |

## Commands

These groups match the panels in `folio --help`. Run `folio COMMAND --help` for options.

### Daily

- **`folio update`**: Download, import, settle, check, generate and report in one run.
- **[`folio dash`](docs/commands/dashboard.md)**: Show holdings, cash and flows valued at their quotes.
- **[`folio ticker`](docs/commands/ticker.md)**: Show a security's details or compare several securities.

### Transactions

- **[`folio query`](docs/commands/querying.md)**: Search and filter stored transactions.
- **[`folio add`](docs/commands/adding-transactions.md)**: Add a single transaction.
- **[`folio edit`](docs/commands/editing-transactions.md)**: Edit transactions individually or in a selection.
- **[`folio delete`](docs/commands/deleting-transactions.md)**: Delete transactions individually or in a selection.
- **`folio import`**: Import transactions from downloaded files, Excel sheets or CSVs.

### Analysis

- **[`folio acb`](docs/commands/cost-base.md)**: Show adjusted cost-base buildup for a security.
- **[`folio check`](docs/commands/checking.md)**: Check for missing or inconsistent transactions.

### Data

- **`folio download`**: Download [IBKR](docs/commands/download/ibkr-integration.md) or [Wealthsimple](docs/commands/download/wealthsimple-integration.md) statements.
- **[`folio getfx`](docs/commands/forex-rates.md)**: Update foreign exchange rates.
- **[`folio quotes`](docs/commands/dashboard.md#prices)**: Inspect or refresh the quote cache.
- **`folio symbol`**: Manage aliases for renamed or equivalent ticker symbols.
- **[`folio settle-info`](docs/commands/settlement-info.md)**: Inspect or import settlement-date information.

### Output

- **`folio generate`**: Write the folio to a workbook.
- **`folio demo`**: Create a sample folio from real market history.
- **`folio version`**: Show the installed version and file paths.

## Features

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
- **[FX Rate Export](docs/commands/forex-rates.md)**: Automatic FX Rate management

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
