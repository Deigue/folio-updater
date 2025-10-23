# IBKR Integration Guide

This guide explains how to set up and use the Interactive Brokers (IBKR) integration to automatically download Flex Query statements.

## Prerequisites

1. **IBKR Account**: You need an Interactive Brokers account with access to Flex Queries
2. **Flex Token**: You need to generate a Flex Token from your IBKR account
3. **Flex Query IDs**: Create and configure Flex Queries for the data you want to download (see below)

## Setting Up Flex Queries in IBKR

1. Log into your IBKR account
2. Go to **Performance & Reports** > **Flex Queries**
3. Create new Flex Queries for:
   - **Trade Confirmation Flex Query**: For trade transactions
   - **Activity Flex Query**: For dividend, withholding tax, contributions, withdrawals etc.
4. Click the **"i"** icon next to each Flex Query to get the **Query ID**

### Recommended Flex Query Configuration

**For Trade Reports:**

- Create a Trade Confirmation Flex Query named `Trade Confirmation` with:
- Include fields: Settle Date, Trade Date, Buy/Sell, Proceeds, Currency, Price, Quantity, Commission, Account Alias, Account ID, Symbol, Other Commission
- Select all account(s) you want to include
- Set format to CSV
- Ensure column headers are included
- Set Date format: yyyy-MM-dd
- Set Date/Time separator: ' '(single-space)

**For Cash Activity:**

- Create a Activity Flex Query named `Cash Activity` with:
- Select only Section: Cash Transactions
- Ensure Option: Summary is unchecked
- Include fields: Settle Date, Report Date, Type, Amount, Currency, Put/Call, Expiry, Multiplier, Account Alias, Account ID, Symbol, Description
- Select all account(s) you want to include
- Set format to CSV
- Ensure column headers are included
- Set Date format: yyyy-MM-dd
- Set Date/Time separator: ' '(single-space)

## Configuration

1. **Update config.yaml**: Add your Flex Query IDs to the brokers section:

    ```yaml
    brokers:
      ibkr:
        TradeConfirmation: "987654321"      # Your actual Flex Query ID for trades
        CashActivity: "123456789"    # Your actual Flex Query ID for cash activities
    ```

    > [!IMPORTANT]
    > When the query name contains `Activity`, it will be treated as Cash Activity; otherwise, it will be treated as Trade Confirmation.
    <!-- Blockquote separator -->
    > [!CAUTION]
    > `Activity` Flex Queries can only be produced upto yesterday's date. Attempting to download today's date will result in an error. \
    > The `to_date` fallback mechanism will handle this automatically, as long as the query name contains `Activity`.

2. **Set Flex Token**: Store your IBKR Flex Token securely:

    ```bash
    folio download --broker ibkr --token YOUR_FLEX_TOKEN_HERE
    ```

    > [!TIP]
    > Your token will be saved securely in your system's keyring, so you won't need to enter it again.

3. *(Optional)* Use the Example [config.yaml](/README.md#example-structure) that is compatible with the Recommended Flex Query setup.

## Usage

### Basic Download

Download statements from the latest transaction date to today:

```bash
folio download --broker ibkr
```

### Specify Date Range

Download statements for a specific date range:

```bash
folio download --broker ibkr --from 2024-01-01 --to 2024-12-31
```

### Import Downloaded Files

After downloading, import the files into your folio database:

```bash
folio import --dir data/imports/
```

## Command Reference

```bash
folio download [OPTIONS]
```

**Options:**

- `--broker, -b`: Broker to download from (e.g. ibkr)
- `--from, -f`: Date in YYYY-MM-DD format (default: latest transaction from broker)
- `--to, -t`: Date in YYYY-MM-DD format (default: today)
- `--token`: Set the flex token for IBKR (will be stored securely)
- `--reference, -r`: Reference code to retry download for IBKR

## Troubleshooting

### Token Issues

If you get authentication errors:

1. Verify your token is correct
2. Re-set the token: `folio download --broker ibkr --token YOUR_TOKEN`
3. Check that your IBKR account has Flex Query access

### Query ID Issues

If downloads fail with "no valid query ID":

1. Verify your Flex Query IDs in `config.yaml`
2. Ensure the Flex Queries are active in your IBKR account
3. Check that the Query IDs are numeric strings, not the default placeholders

### Statement Not Ready

If you get timeout errors:

- The IBKR system may be busy generating your statement
- Try again in a few minutes
- For large date ranges, consider breaking into smaller periods

## Data Flow

1. **Download**: `folio download` → Downloads CSV files to `data/imports/`
2. **Import**: `folio import --dir data/imports/` → Imports CSV data to database
3. **Generate**: `folio generate` → Creates updated Excel portfolio file

This integration allows you to automatically sync your IBKR transactions to the folio.
