# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]

### Added

### Changed

- Improved CLI output formatting and colorization
- `getfx` will default to pulling last 30 days of FX rates if no transactions exist

### Deprecated

### Removed

### Fixed

- Fixed log messages duplicating in console output
- Default `import` now imports from the `imports` folder in the `data` directory

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
