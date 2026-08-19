"""Turning a source file's rows into transactions the folio will accept.

`prepare_transactions` runs the whole pipeline. Map the source's headers onto
canonical columns, validate and coerce the fields, apply the user's merge and
transform rules, then drop anything already in the folio. Nothing here writes
to the database; the caller does that with whatever is returned.
"""

from ingest.pipeline import prepare_transactions
from ingest.validation import ActionValidationRules

__all__ = ["ActionValidationRules", "prepare_transactions"]
