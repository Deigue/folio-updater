"""Common test helpers for CLI testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import patch

from typer.testing import CliRunner

from .console import DEFAULT_WIDTH, UNCONSTRAINED_WIDTH, capture_output

__all__ = [
    "UNCONSTRAINED_WIDTH",
    "CliTestResult",
    "assert_cli_success",
    "assert_in_output",
    "assert_not_in_output",
    "run_cli_with_config",
]

if TYPE_CHECKING:
    from typer import Typer

    from config import Config

runner = CliRunner()


@dataclass
class CliTestResult:
    """Dataclass to hold flattened CLI test results for cleaner access."""

    exit_code: int
    stdout: str
    stderr: str | None
    exception: BaseException | None
    plain_output: str


def run_cli_with_config(
    config: Config,
    command_app: Typer,
    args: list[str],
    user_input: str | None = None,
    width: int = DEFAULT_WIDTH,
) -> CliTestResult:
    """Run CLI commands with proper config mocking and capture plain output.

    Args:
        config: The Config object to use for the test.
        command_app: The Typer app to run.
        args: The command and its arguments.
        user_input: Optional stdin text to answer interactive prompts, with each
            response terminated by a newline.
        width: Terminal width to render at. Pass `UNCONSTRAINED_WIDTH` for a
            test about the figures rather than how the terminal squeezed them.

    Returns:
        A CliTestResult object with execution details.
    """
    # Mock bootstrap.reload_config to return our test config
    with (
        patch("app.bootstrap.reload_config") as mock_reload,
        capture_output(width) as bio,
    ):
        mock_reload.return_value = config
        click_result = runner.invoke(command_app, args, input=user_input)
        return CliTestResult(
            exit_code=click_result.exit_code,
            stdout=click_result.stdout,
            stderr=click_result.stderr,
            exception=click_result.exception,
            plain_output=bio.get_text(),
        )


def assert_cli_success(result: CliTestResult) -> None:
    """Assert that a CLI command succeeded.

    Args:
        result: The CliTestResult from the command execution.

    Raises:
        AssertionError: If the command did not succeed.
    """
    assert result.exit_code == 0, (
        f"CLI failed: {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}\n"
        f"PLAIN_OUTPUT:\n{result.plain_output}\n"
        f"EXCEPTION:\n{str(result.exception) if result.exception else 'None'}"
    )


def assert_in_output(expected_substring: str, cli_result: CliTestResult) -> None:
    """Assert that a substring exists in the CLI's plain text output.

    Args:
        expected_substring: The substring to search for.
        cli_result: The CliTestResult to check.

    Raises:
        AssertionError: If the substring is not found.
    """
    assert expected_substring in cli_result.plain_output, (
        f"Expected substring was not found in the command's plain text output.\n"
        f"---EXPECTED SUBSTRING---\n{expected_substring}\n"
        f"---ACTUAL PLAIN_OUTPUT---\n{cli_result.plain_output}"
    )


def assert_not_in_output(
    unexpected_substring: str,
    cli_result: CliTestResult,
) -> None:
    """Assert that a substring does NOT exist in the CLI's plain text output.

    Args:
        unexpected_substring: The substring that should not be present.
        cli_result: The CliTestResult to check.

    Raises:
        AssertionError: If the substring is found.
    """
    assert unexpected_substring not in cli_result.plain_output, (
        f"Unexpected substring was found in the command's plain text output.\n"
        f"---UNEXPECTED SUBSTRING---\n{unexpected_substring}\n"
        f"---ACTUAL PLAIN_OUTPUT---\n{cli_result.plain_output}"
    )
