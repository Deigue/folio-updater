"""Common test helpers for CLI testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.test_console import capture_output

if TYPE_CHECKING:
    from typer import Typer

    from utils.config import Config

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
    args: list[str] | None = None,
) -> CliTestResult:
    """Run CLI commands with proper config mocking and capture plain output.

    Args:
        config: The Config object to use for the test.
        command_app: The Typer app to run.
        args: Optional list of command arguments.

    Returns:
        A CliTestResult object with execution details.
    """
    if args is None:
        args = []

    # Mock bootstrap.reload_config to return our test config
    with patch("app.bootstrap.reload_config") as mock_reload, capture_output() as bio:
        mock_reload.return_value = config
        click_result = runner.invoke(command_app, args)
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
    if result.exit_code != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("PLAIN_OUTPUT:\n", result.plain_output)
        if result.exception:
            print("EXCEPTION:", str(result.exception))
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
    if expected_substring not in cli_result.plain_output:
        print("\n---EXPECTED SUBSTRING---")
        print(expected_substring)
        print("\n---ACTUAL PLAIN_OUTPUT---")
        print(cli_result.plain_output)
        print("---END PLAIN_OUTPUT---\n")
        pytest.fail(
            "Expected substring was not found in the command's plain text output.",
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
    if unexpected_substring in cli_result.plain_output:
        print("\n---UNEXPECTED SUBSTRING---")
        print(unexpected_substring)
        print("\n---ACTUAL PLAIN_OUTPUT---")
        print(cli_result.plain_output)
        print("---END PLAIN_OUTPUT---\n")
        pytest.fail(
            "Unexpected substring was found in the command's plain text output.",
        )
