#!/usr/bin/env python3
"""Script to prepare a new release.

This script:
1. Asks whether to bump the project version (pyproject.toml and uv.lock)
2. Moves the unreleased changelog entries under the release version
3. Creates a fresh [Unreleased] section for future changes

Run it with no arguments to be prompted, or pass a version to skip the prompt.
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from domain import TORONTO_TZ

CHANGELOG_PATH = Path("CHANGELOG.md")
MAX_ARGS = 2
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")

NEW_UNRELEASED_SECTION = """## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

"""


def run_uv_version(*args: str) -> str:
    """Run `uv version` and return its stripped stdout.

    Args:
        *args: Extra arguments for `uv version`.

    Returns:
        The command's standard output.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["uv", "version", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Error: uv version failed:\n{exc.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def choose_version() -> str:
    """Ask whether to bump the version, and return the release version.

    Returns:
        The version to release, either the current one or the chosen new one.
    """
    current = run_uv_version("--short")
    suggested = run_uv_version("--bump", "patch", "--dry-run", "--short")
    answer = (
        input(
            f"pyproject.toml is at {current}. Update it to {suggested} for this "
            f"release? [Y/n, or type another version]: ",
        )
        .strip()
        .lstrip("v")
    )

    if answer.lower() in {"", "y", "yes"}:
        return suggested
    if answer.lower() in {"n", "no"}:
        return current
    if VERSION_PATTERN.fullmatch(answer):
        return answer
    print(f"Error: '{answer}' is not a version like 1.2.3")
    sys.exit(1)


def apply_version(version: str) -> None:
    """Write the version to pyproject.toml and uv.lock if it differs.

    Args:
        version: The release version.
    """
    if run_uv_version("--short") == version:
        print(f"Version stays at {version}")
        return
    run_uv_version(version)
    print(f"Updated pyproject.toml and uv.lock to {version}")


def prepare_release(version: str) -> None:
    """Prepare changelog for a new release version.

    Args:
        version: The release version, with or without a leading "v".
    """
    if not CHANGELOG_PATH.exists():
        print(f"Error: {CHANGELOG_PATH} not found")
        sys.exit(1)

    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    clean_version = version.lstrip("v")
    current_date = datetime.now(tz=TORONTO_TZ).strftime("%Y-%m-%d")
    new_version_header = f"## [{clean_version}] - {current_date}"
    unreleased_pattern = r"^## \[Unreleased\]"

    if not re.search(unreleased_pattern, content, re.MULTILINE):
        print("Error: No [Unreleased] section found in changelog")
        sys.exit(1)

    # Put a fresh [Unreleased] block in front of the old one, relabelled as the
    # release. The block ends with exactly one blank line, so no lint error.
    updated_content = re.sub(
        unreleased_pattern,
        lambda _: NEW_UNRELEASED_SECTION + new_version_header,
        content,
        count=1,
        flags=re.MULTILINE,
    )

    CHANGELOG_PATH.write_text(updated_content, encoding="utf-8")
    print(f"✅ Prepared changelog for version {clean_version}")
    print(f"📝 Updated {CHANGELOG_PATH}")
    print("\n📋 Next steps:")
    print("1. Review the changelog entries")
    print("2. Edit any entries in the new version section as needed")
    print("3. Commit the version and changelog changes")
    print(
        f"4. Create and push the git tag: "
        f"git tag v{clean_version} && git push origin v{clean_version}",
    )


def main() -> None:
    """Prepare the version and changelog for a new release."""
    if len(sys.argv) > MAX_ARGS:
        print("Usage: python prepare_changelog.py [version]")
        print("Example: python prepare_changelog.py v1.2.0")
        sys.exit(1)

    version = sys.argv[1].lstrip("v") if len(sys.argv) == MAX_ARGS else choose_version()
    apply_version(version)
    prepare_release(version)


if __name__ == "__main__":
    main()
