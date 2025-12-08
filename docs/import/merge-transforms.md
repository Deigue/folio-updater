# Merge Transforms

The folio-updater allows creating rules to automatically merge transactions based on specified
rules. This will run through an example of how merge transformations work.

## Dividends and Withholding Tax Merge

Some brokers report dividend transactions as two separate rows:

1. A "Dividends" row with the gross dividend amount
2. A "Withholding Tax" row with a negative amount representing the tax withheld

The folio-updater can automatically merge these related transactions into a single `DIVIDEND` transaction with the net amount.

## Configuration

Add a `merge_groups` section to your `config.yaml` under `transforms`:

```yaml
transforms:
  merge_groups:
    - name: "Dividend Withholding Tax Merge"
      match_fields: ["TxnDate", "Account", "Ticker"]
      source_actions: ["Dividends", "Withholding Tax"]
      target_action: "DIVIDEND"
      amount_field: "Amount"
      operations:
        Fee: 0
        Units: 0
```

## Configuration Parameters

### `name` (required)

Descriptive name for this merge group

### `match_fields` (required)

List of fields that must match for transactions to be grouped together. Typically:

- `TxnDate`: Ensures transactions are from the same day
- `Account`: Ensures transactions are from the same account
- `Ticker`: Ensures transactions are for the same security

### `source_actions` (required)

List of Action types to merge. Must have at least 2 values. Examples:

- `["Dividends", "Withholding Tax"]`
- `["Dividend", "Tax"]`

### `target_action` (required)

The final Action type for the merged transaction. Typically `"DIVIDEND"`.

### `amount_field` (required)

The field containing the amount to sum. Usually `"Amount"`.

### `operations` (optional)

Additional field transformations to apply to the merged row. Common examples:

- `Fee: 0` - Set fee to 0 for dividends
- `Units: 0` - Set units to 0 (dividends don't involve unit purchases)

## Processing Order

Merge groups are processed **before** regular transformation rules. This means:

1. Dividends and withholding taxes are merged first
2. Then regular transforms (like setting Fee=0 for DIVIDEND) are applied
