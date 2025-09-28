# Transaction Transformations

Transaction transformations allow you to automatically modify imported transaction data based on configurable rules. This feature is useful when your data source provides transactions in one format, but you need them to be represented differently in your portfolio tracking.

## Overview

Transformation rules are applied after header mapping but before format validation. Each rule consists of:

- **Conditions**: Criteria that must be met for the transformation to apply
- **Actions**: Changes to make to matching transactions

## Configuration

Add transformation rules to your `config.yaml` file under the `transforms` section:

```yaml
transforms:
  rules:
    - conditions:
        Action: ["BUY", "SELL"]
        Ticker: ["USD.CAD"]
      actions:
        Action: "FXT"
        Ticker: ""
    - conditions:
        Action: ["DIVIDEND"]
      actions:
        Commission: "0"
```

### Rule Structure

Each rule has two main parts:

#### Conditions

- **Field Name**: The transaction field to check (e.g., `Action`, `Ticker`)
- **Values**: List of values that match (e.g., `["BUY", "SELL"]`)
- **Logic**: ALL conditions must be met for the rule to apply (AND logic)

#### Actions

- **Field Name**: The transaction field to modify
- **New Value**: The value to set (as string, use `""` for empty)

## Common Use Cases

### 1. Foreign Exchange Transactions

Convert currency trades to FX transaction type:

```yaml
transforms:
  rules:
    - conditions:
        Action: ["BUY", "SELL"]
        Ticker: ["USD.CAD", "EUR.USD", "GBP.USD"]
      actions:
        Action: "FXT"
        Ticker: ""
```

**Use case**: Your broker records currency exchanges as BUY/SELL of currency pairs, but you want them categorized as FX transactions.

### 2. Set Default Commission

Set commission to zero for dividend transactions:

```yaml
transforms:
  rules:
    - conditions:
        Action: ["DIVIDEND"]
      actions:
        Commission: "0"
```

**Use case**: You want to ensure that Dividend transactions don't have any fees associated to them.

### 3. Normalize Action Types

Convert broker-specific action types to standard ones:

```yaml
transforms:
  rules:
    - conditions:
        Action: ["PURCHASE", "ACQUIRE"]
      actions:
        Action: "BUY"
    - conditions:
        Action: ["SALE", "DISPOSE"]
      actions:
        Action: "SELL"
```

### 4. Account-Specific Transformations

Apply different rules based on account:

```yaml
transforms:
  rules:
    - conditions:
        Account: ["TRADING_ACCOUNT"]
        Action: ["BUY", "SELL"]
      actions:
        Commission: "9.95"
```

### 5. Numeric Value Matching

Match transactions based on numeric values like price, quantity, or fees:

```yaml
transforms:
  rules:
    - conditions:
        Price: [100.5, 200]        # Match specific prices
        Quantity: [10, 50, 100]    # Match specific quantities
      actions:
        Description: "High Volume Trade"
        Fee: 0                     # Set numeric fee to zero
    - conditions:
        Commission: [0, 0.0]       # Match zero commission trades
      actions:
        Note: "Fee-free transaction"
```

**Use case**: Apply special handling for transactions with specific numeric values, such as zero-commission trades or round-number quantities.

## Processing Order

Transformations are applied in this order within the transaction preparation pipeline:

1. **Header Mapping**: Map Excel columns to internal field names
2. **⭐ Transformations**: Apply transformation rules (THIS STEP)
3. **Format & Validation**: Format data types and validate required fields
4. **Duplicate Filtering**: Remove duplicate transactions
5. **Database Insertion**: Insert into database

## Examples

### Complete Example Configuration

```yaml
# config.yaml
transforms:
  rules:
    # Convert USD.CAD trades to FX transactions
    - conditions:
        Action: ["BUY", "SELL"]
        Ticker: ["USD.CAD"]
      actions:
        Action: "FXT"
        Ticker: ""
        
    # Set zero commission for dividends
    - conditions:
        Action: ["DIVIDEND"]
      actions:
        Commission: "0"
        
    # Normalize broker action names
    - conditions:
        Action: ["PURCHASE"]
      actions:
        Action: "BUY"
        
    # Set default commission for specific account
    - conditions:
        Account: ["CASH_ACCOUNT"]
        Action: ["BUY", "SELL"]
      actions:
        Commission: "4.95"
```

## Logging

Transformation activity is logged at INFO level:

```log
INFO - Applying 2 transformation rule(s)
INFO - Transforming 3 row(s) matching conditions: {'Action': ['BUY', 'SELL'], 'Ticker': ['USD.CAD']}
```

Use DEBUG level for detailed field-by-field transformation logging.
