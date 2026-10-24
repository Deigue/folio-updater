"""Turning a source file's rows into transactions the folio will accept.

`prepare_transactions` runs the whole pipeline. Map the source's headers onto
canonical columns, apply the user's import rules (cancellation, merge, then
transform), validate and coerce the fields, then reject anything already in the
folio. The only writes are schema: tables are created and new optional columns
added. The caller stores whatever is returned.
"""

from ingest.pipeline import prepare_transactions
from ingest.rules import TransactionTransformer
from ingest.validation import ActionValidationRules

__all__ = [
    "ActionValidationRules",
    "TransactionTransformer",
    "prepare_transactions",
]
