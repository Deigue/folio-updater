# Deleting Transactions

`folio delete` removes transactions from the folio. It exists for the entries that should never
have been there: a double-counted import, a broker correction, or a test row you added while
learning the tool.

Deletion is permanent, so the command is built around showing you exactly what it is about to
remove before it removes it.

## Basic Usage

```bash
# By TxnId, as shown by `folio query`
folio delete 1234
folio delete 1234 1235 1236

# By query terms - the same syntax `folio query` accepts
folio delete NVDA before 2024-06-10
folio delete DIVIDEND VOO 2024 --dry-run
```

```text
                       Transactions to Delete
  TxnId  SettleDate  TxnDate     Action  Amount    $    Price   Units
  1234   2024-06-12  2024-06-10  BUY     -1502.50  USD  150.25  10
Delete 1 transaction(s)? [y/N]: y
✅ Deleted 1 transaction(s)
```

## Options

| Option      | Meaning                                             |
| ----------- | --------------------------------------------------- |
| `--force`   | Delete without asking for confirmation              |
| `--dry-run` | Show the matched transactions without deleting them |

## Selecting What to Delete

- If **every** term is a plain number, the terms are TxnIds.
- Otherwise the terms are query terms, parsed exactly as `folio query` parses them.

So `folio delete 2024` deletes TxnId 2024. To delete a year's transactions, say so explicitly with
`folio delete date:2024`.

`folio query` displays the TxnId column, which makes finding what to delete a natural first step.

## Confirmation and Safety

Every delete shows the matched transactions first, pages through them if there are many, and asks
before writing. Nothing is truncated - you can always see every row that is about to go.

`--force` skips the confirmation prompt.

## Notes

- The folio database is backed up before the delete, subject to your
  [backup configuration](../configuration.md).
- Every deletion is written to the importer audit log with the full pre-delete state of each row,
  so a mistaken delete can be reconstructed from the log.

## Related

- [Editing Transactions](editing-transactions.md)
- [Adding Transactions Manually](adding-transactions.md)
- [Smart Transaction Querying](querying.md)
