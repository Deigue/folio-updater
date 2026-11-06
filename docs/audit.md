# Audit Log

Every change to your folio is written to `logs/audit.log`, beside `config.yaml`, with the
command that made it and the backup taken before it.

## Setting the level

```yaml
log_level: ERROR        # folio.log
audit_log_level: INFO   # audit.log (same levels)
```

## Example

```text
2025-03-15 09:12:00 INFO    IMPORT TXNS "inbox/broker-2025-03.xlsx" sheet 'Txns' (folio update)
2025-03-15 09:12:00 INFO      READ 6 source rows; folio holds 120 transactions
2025-03-15 09:12:00 INFO      MAP 6 row(s) onto the folio's columns
2025-03-15 09:12:00 INFO        > TxnDate=2025-03-03|Action=BOUGHT|Amount=-1504.95|$=USD|…
2025-03-15 09:12:00 INFO      MERGE 'Withholding merge'
2025-03-15 09:12:00 INFO        + TxnDate=2025-03-14|Action=DIVIDEND|Amount=8.5|…
2025-03-15 09:12:00 INFO          - TxnDate=2025-03-14|Action=Dividends|Amount=10.0|…
2025-03-15 09:12:00 INFO          - TxnDate=2025-03-14|Action=Withholding|Amount=-1.5|…
2025-03-15 09:12:00 WARNING   REJECT 1 invalid row(s)
2025-03-15 09:12:00 WARNING     - TxnDate=N/A|Action=BUY|… (MISSING TxnDate)
2025-03-15 09:12:00 INFO      STORE 4 transaction(s) (TxnIds 121-124); backup folio_20250315_091200_120.db
2025-03-15 09:12:00 INFO        + TxnId=121|TxnDate=2025-03-03|Action=BUY|…|Fee=4.95|SettleDate=2025-03-04
2025-03-15 09:12:00 INFO    DONE 4 stored, 1 rejected; folio holds 124 transactions
2025-03-15 09:12:00 INFO    ================================================================================
```

## Marks

| Mark | Meaning                                                            |
| ---- | ------------------------------------------------------------------ |
| `>`  | As read from the source file, before anything was changed          |
| `+`  | Stored or added, or the new version of a row                       |
| `-`  | Not stored, removed, or the old version of a row                   |
| `~`  | Changed, field by field: `Field old -> new`                        |
| `*`  | An approved duplicate, let through                                 |
| `x`  | Voided by a cancellation                                           |
| `=`  | Left as it was                                                     |
| `?`  | Could not be resolved, such as a statement row with no match       |

## Entries

| Heading                       | Written by                                  | Stages                                                                               |
| ----------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------ |
| `IMPORT TXNS "<file>"`        | `folio import`, `folio update`              | `READ`, `MAP`, `TRANSFORM`, `MERGE`, `CANCEL`, `REJECT`, `APPROVE`, `STORE`          |
| `IMPORT STATEMENT "<file>"`   | `folio update`                              | `READ`, `CONFIRM` (settlement dates, `~ SettleDate old -> new`), `TRANSFER`          |
| `ADD TXN`                     | `folio add`                                 | `REJECT`, `APPROVE`, `STORE`                                                         |
| `EDIT TXNS <selection> SET …` | `folio edit`                                | `UPDATE`: a `~` row per transaction, with its old (`-`) and new (`+`) row under it   |
| `DELETE TXNS <selection>`     | `folio delete`                              | `REMOVE`: each removed row in full                                                   |
| `ADD ALIAS <ticker>`          | `folio symbol --add`                        | `STORE`, `REPLACE` (`~` for what changed) or `KEEP` (already as given)               |
| `REMOVE ALIAS <ticker>`       | `folio symbol --delete`                     | `REMOVE`                                                                             |
| `BUILD DEMO folio`            | `folio demo`                                | none: one summary line, since the demo is a fixed generated set                      |
| `CLEAR DEMO folio`            | importing your own rows over the demo       | `REMOVE`                                                                             |

Stages that set something aside or keep something unexpected are written at `WARNING`:
`REJECT`, `SKIP` (a rule whose fields the file does not have), and an import's `KEEP`
(a column or value kept against a setting). `FAILED` lines are written at `ERROR`.

A `folio add --dry-run` writes no entry. A `folio edit` or `folio delete` dry run never
reaches the log either.

## Undoing a change

1. Find the entry by its heading and command.
2. The stage that wrote to the folio names the backup taken just before it, in your
   [backup folder](configuration.md). Only the first write of a command takes a backup,
   so every stage of one command names the same file.
3. To undo a single change instead, the rows hold what you need: `+` rows of a `STORE`
   carry the TxnIds to delete, `-` rows of a `REMOVE` hold the full rows to add back,
   and an edit's `-` row is the version to restore.
