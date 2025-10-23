"""Shared test type definitions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import _GeneratorContextManager

from app.app_context import AppContext

# Type alias for temp_ctx fixture
TempContext = Callable[..., _GeneratorContextManager[AppContext, None, None]]
