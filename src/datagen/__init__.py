"""Demo folio generation for folio-updater.

This module exports the demo folio, built from real market history.
"""

from datagen.demo import (
    DemoFolio,
    DemoScenarioError,
    ensure_data_exists,
    is_demo_folio,
)

__all__ = [
    "DemoFolio",
    "DemoScenarioError",
    "ensure_data_exists",
    "is_demo_folio",
]
