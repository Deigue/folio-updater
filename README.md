# Folio Updater

Keep every account's transactions in one folio, from broker downloads, statements and your own
files, and see your holdings, adjusted cost base and flows from the command line.

## Quick Start

1. Download and extract `folio-windows-x64.zip`.
2. Run `folio.exe --help` to see the commands, grouped by what they are for.

Then take one of two routes.

### Try it: the demo folio

```bash
folio demo
folio dash
folio ticker RY.TO
folio acb NVDA
```

`folio demo` builds two years of transactions across six demo accounts from real market history,
and writes the folio workbook as `folio generate` does. `folio version` shows where the folio and
its workbook are kept.

The demo is only built into an empty folio. When you are ready for your own transactions, your
first import or update offers to replace it.

### Use it: your own accounts

1. Set up your brokers in `config.yaml`, which is written with defaults on the first run:
   [Interactive Brokers](docs/commands/download/ibkr-integration.md#configuration),
   [Wealthsimple](docs/commands/download/wealthsimple-integration.md#configuration).
2. Catch the folio up:

   ```bash
   folio update
   ```

   It downloads from your brokers, imports what arrived, reads statements, checks the folio,
   writes the workbook and reports what changed. Run it again whenever you want to catch up.
3. For any other broker, import its file yourself: `folio import -f FILE`.

## Which command shows what

| To see | Run |
| --- | --- |
| What you hold, valued at quotes, with cash and flows | `folio dash` |
| The same, one account type beside another | `folio dash --by-type` |
| One security in focus, owned or not | `folio ticker SYMBOL` |
| Every holding compared over each range | `folio ticker` |
| A holding's ACB buildup, transaction by transaction | `folio acb SYMBOL` |
| One row per symbol: units, ACB and realized gain | `folio acb --summary` |
| Transactions matching dates or text | `folio query last 3 months` |
| Missing or inconsistent transactions | `folio check` |
| The cached quotes | `folio quotes` |
| Settlement dates, calculated and actual | `folio settle-info` |
| Everything, in one Excel workbook | `folio generate` |
| Where the config, folio and backups are kept | `folio version` |

## Commands

Run `folio <command> --help` for each command's options.

### Daily

- **`folio update`**: Catch up in one go: download, import, statements, check and generate, then report what changed
- **[`folio dash`](docs/commands/dashboard.md)**: Dashboard with holdings, cash and flows, valued at their quotes
- **[`folio ticker`](docs/commands/ticker.md)**: Show one security in focus, or compare several over every range

### Transactions

- **[`folio query`](docs/commands/querying.md)**: Search and filter transactions using natural language or explicit filters
- **[`folio add`](docs/commands/adding-transactions.md)**: Manually add a single transaction
- **[`folio edit`](docs/commands/editing-transactions.md)**: Edit transactions, one at a time or in batch
- **[`folio delete`](docs/commands/deleting-transactions.md)**: Delete transactions, one at a time or in batch
- **`folio import`**: Import transactions from files (broker statements, Excel sheets, CSVs, etc.)

### Analysis

- **[`folio acb`](docs/commands/cost-base.md)**: Adjusted cost base buildup for a symbol, pooled by account, account type or portfolio
- **[`folio check`](docs/commands/checking.md)**: Check the folio for missing or inconsistent transactions

### Data

- **`folio download`**: Download source files and statements from brokers ([Interactive Brokers](docs/commands/download/ibkr-integration.md), [Wealthsimple](docs/commands/download/wealthsimple-integration.md))
- **[`folio getfx`](docs/commands/fx-rates.md)**: Update FX rates automatically
- **[`folio quotes`](docs/commands/dashboard.md#prices)**: Inspect or refresh the cached quotes
- **`folio symbol`**: Alias ticker symbols that are renamed or different to be treated the same
- **[`folio settle-info`](docs/commands/settlement-info.md)**: Show settlement dates, and confirm them from statements

### Workbook & setup

- **`folio generate`**: Write the whole folio to one workbook
- **`folio demo`**: Build a realistic two-year demo folio from real market history, to explore every command
- **`folio version`**: Show the version of folio-updater and where its files are kept

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
- **[Audit Log](docs/audit.md)**: Comprehensive audit trail of folio operations
- **Automatic Backup**: All updates are automatically backed up (configurable)

### Export Functionality

- **Transaction Export**: Export transactions from database to Excel sheets
- **[FX Rate Export](docs/commands/fx-rates.md)**: Automatic FX Rate management

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
