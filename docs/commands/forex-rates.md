# Forex Rate Management

The folio-updater application includes foreign exchange (FX) rate management functionality that automatically fetches, stores, and exports USD/CAD currency conversion rates.

## Data Source

FX rates are sourced from the **Bank of Canada** (BoC) official API:

- **URL**: `https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/csv`
- **Currency Pair**: USD/CAD (US Dollar to Canadian Dollar)
- **Update Frequency**: Daily (business days)
- **Data Quality**: Official central bank rates

## Features

### Automatic Data Management

The system automatically:

1. **Checks for missing data** based on transaction dates in your portfolio
2. **Fetches new rates** from Bank of Canada API when needed
3. **Stores rates locally** in SQLite database for fast access
4. **Maintains data** up to the current business day

### Currency Conversion

- **FXUSDCAD**: US Dollar to Canadian Dollar rate
- **FXCADUSD**: Canadian Dollar to US Dollar rate (automatically calculated as 1/FXUSDCAD)

## Configuration

### Excel Sheet Configuration

The FX rates are exported to a dedicated sheet in your portfolio Excel file. The sheet name is configurable in `config.yaml`:

```yaml
sheets:
  fx: FX  # Default sheet name for forex rates
```

### Data Structure

The exported FX data includes three columns:

| Column     | Description                   | Example      |
| ---------- | ----------------------------- | ------------ |
| `Date`     | Transaction date (YYYY-MM-DD) | `2025-09-20` |
| `FXUSDCAD` | USD to CAD exchange rate      | `1.3542`     |
| `FXCADUSD` | CAD to USD exchange rate      | `0.7384`     |
