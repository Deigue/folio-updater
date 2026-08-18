# Wealthsimple Integration Guide

This guide explains how to set up and use the Wealthsimple integration to automatically download transactions and activity data.

## Prerequisites

1. **Wealthsimple Account**: You need a Wealthsimple account with trade and/or activity history
2. **Wealthsimple Login Credentials**: Your email/username and password for Wealthsimple
3. **Multi-Factor Authentication** *(Optional)*: If your account uses TOTP-based 2FA, you'll be prompted to enter the code during login

## Setting Up

### Initial Authentication

The first time you download from Wealthsimple, you'll be prompted to log in:

```bash
folio download --broker wealthsimple
```

You will be asked for:

- **Username**: Your Wealthsimple email/username
- **Password**: Your Wealthsimple password
- **TOTP Code** *(if enabled)*: Your authenticator app code (if 2FA is enabled)

Your authentication session will be securely stored in your system's keyring for future use.

### Configuration

Wealthsimple integration is configured automatically and doesn't require additional setup in `config.yaml` beyond the broker configuration. However, you can optionally configure account exclusions:

```yaml
brokers:
  wealthsimple:
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
    exclude_accounts: ["Cash"]  # Optional: exclude specific accounts
```

| Key                    | Description                                                               |
| ---------------------- | ------------------------------------------------------------------------- |
| **`user_agent`**       | HTTP User-Agent header for API requests. Default is a Firefox user agent. |
| **`exclude_accounts`** | *(Optional)* List of account descriptions to exclude from downloads       |

## Usage

### Basic Download

Download all transactions from the latest transaction date to today:

```bash
folio download --broker wealthsimple
```

This will:

1. Authenticate (or use existing session from keyring)
2. Retrieve all accounts
3. Download transactions for the date range
4. Export as CSV file to `data/imports/`

### Specify Date Range

Download transactions for a specific date range:

```bash
folio download --broker wealthsimple --from 2024-01-01 --to 2024-12-31
```

### Import Downloaded Files

After downloading, import the files into your folio database:

```bash
folio import
```

### Download Monthly Statements

Monthly statements can be downloaded which contain settlement date information

```bash

# Download monthly statement for April 2024
folio download --broker wealthsimple --statement --from 2024-04-01
```

Statement files are saved as `ws_statement_{account}_{yyyymm}.csv`
`folio settle-info --import` recovers Account from the filename.

### Import Settlement Dates and Transfers from Statements

After downloading monthly statements, import them back into the folio:

```bash
folio settle-info --import
```

This does two things:

- **Settlement dates**: Trades already in the folio get their calculated settlement
  date replaced with the real one from the statement.
- **Institutional transfers**: The statement is the only source with a real amount and date, so this
  command creates the `TFR_IN` / `TFR_OUT` transaction directly from it.

Cash moved between your bank and Wealthsimple also appears on the statement as a
`TRFIN` / `TRFOUT` row, described as "Money transfer ...". Those rows are skipped,
since the activities download already brings them in as `CONTRIBUTION` /
`WITHDRAWAL` transactions.

### Resetting Credentials

If you need to reset your Wealthsimple credentials (e.g., to switch accounts or fix authentication issues):

```bash
folio download --broker wealthsimple --credentials
```
