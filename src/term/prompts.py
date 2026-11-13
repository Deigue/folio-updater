"""Questions asked at the terminal."""

from __future__ import annotations

import typer


def prompt_secret(text: str) -> str:
    """Ask for a secret twice, hiding what is typed, and return it."""
    return typer.prompt(text, hide_input=True, confirmation_prompt=True)
