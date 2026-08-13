# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]

### Added

- `TFR_IN` and `TFR_OUT` actions for moving cash or units between accounts you own.
- Transform conditions accept a `contains:` prefix to match a substring.
- `folio settle-info --import` now also creates `TFR_IN` / `TFR_OUT` transactions
  for Wealthsimple institutional transfers reported in a monthly statement.

### Changed

- Updated example configuration in `configuration.md`
- `folio query` now accepts explicit TxnIds (like `folio edit`/`folio delete`)

### Deprecated

### Removed

### Fixed

- Wealthsimple institutional transfers are now properly captured via monthly statements.
- Dividend amounts are no longer forced positive on import.

### Security

## [0.6.25] - 2026-08-10

### Added

- `folio add` command: manually add transactions to the folio.
- `folio delete` command: delete transactions from the folio by TxnId or query.
- `folio edit` command: edit transactions in the folio by TxnId or query.

### Changed

- Optimized test suite performance by conditional parallelization, splitting non-DB query
  tests and disabling backups.
- Rearranged docuementation pages for better organization and clarity.

### Deprecated

### Removed

### Fixed

- Paginated tables in CLI get their own pager terminal.
- Height calculation for paginated tables is now more accurate.

### Security

## [0.6.21] - 2026-08-07

### Added

- `folio query` command: search transactions with natural-language filters, date
  phrases (e.g. "last month", "since july 2023"), sorting, and result limits
- Added support for renaming tickers by managing ticker aliases with `folio tickers`
- Display detailed audit information for transaction imports
- Import logging will now include the final list of transactions imported
- Completely revamped CLI display for import and settlement info
- `--verbose` flag can be used to display final imported transactions for imports

### Changed

- Improved CLI output formatting and colorization
- `getfx` will default to pulling last 30 days of FX rates if no transactions exist

### Deprecated

### Removed

### Fixed

- Fixed log messages duplicating in console output
- Default `import` now imports from the `imports` folder in the `data` directory
- Mock data generation properly uses negative amounts based on transaction type
- Fixed excessive rows being displayed during import CLI causing pagination issues
- IBKR download will correctly fallback to last business day for Activity statements

### Security

## [0.5.1] - 2025-11-15

### Added

- `settle-info` command now uses `--import` to enable import functionality
- Added `--statement` option for the `download` command to download monthly statements from Wealthsimple
- Default `--import` for `settle-info` will batch import statements from the `download` command

### Changed

- Use `fastparquet` as default Parquet engine instead of `pyarrow` for better performance and lower memory usage
- Consolidated credential management to `--credentials` for `download`
- Enhanced `settle-info` command: `--file/-f` option can only be used in `--import` mode

### Deprecated

### Removed

- Removed `pyarrow` dependency to lower install size

### Fixed

- Fixed `reset` option always applying for `download` command
- Fixed settlement date imports not updating Parquet files

### Security

## [0.4.1] - 2025-11-06

### Added

- `download` now supports Wealthsimple

### Changed

- Credential storage is prompt-based instead of argument-based for better security

### Deprecated

### Removed

### Fixed

- Parquet is automatically generated when only db file is present
- Allow `SPLIT` transactions to be properly imported (Amount and Fee are now optional)
- Only apply console colorization if terminal supports it

### Security

## [0.3.0] - 2025-10-23

### Added

- `download` CLI command to fetch transaction data from brokers
- `folio import -d default` can be used to import from the default import folder

### Changed

- `import` and `processed` folders are now generated in the configured `data` folder

### Deprecated

### Removed

### Fixed

- Just running `folio` will now show help message instead of error about missing subcommand
- Default `folio import` now also exports to Parquet like all other import commands
  
### Security

## [0.2.0] - 2025-10-15

### Added

- `settle-info` has a `--file` option to import settlement dates from monthly statements
- CLI command `generate` to create latest folio from current data
- Transaction transforms: Automatically modify imported transactions based on user-configurable rules
- CLI command `settle-info` to query settlement date statistics
- Settlement Date Auto-Calculation: Automatically calculate settlement dates for transactions based on transaction type and market calendars

### Changed

- Import command will now initialize a folio if it does not already exist.

### Deprecated

### Removed

### Fixed

### Security

## [0.1.0] - 2025-09-25
