"""Shared test type definitions."""

from __future__ import annotations

from contextlib import _GeneratorContextManager
from typing import Callable

from app.app_context import AppContext

# Type alias for temp_ctx fixture
TempContext = Callable[..., _GeneratorContextManager[AppContext, None, None]]
