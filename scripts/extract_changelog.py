#!/usr/bin/env python3
"""Script to extract changelog entries for a given version.

Used in GitHub Actions to generate release notes.
"""

import re
import sys
from pathlib import Path

EXPECTED_ARGS = 2


def extract_changelog_for_version(
    version: str,
    changelog_path: Path = Path("CHANGELOG.md"),
) -> str:
    """Extract changelog entries for a specific version."""
    if not changelog_path.exists():
        return ""

    content = changelog_path.read_text(encoding="utf-8")
    clean_version = version.lstrip("v")

    # Pattern to match version sections
    # Matches: ## [1.0.0] - 2025-09-25 or ## [1.0.0]
    version_pattern = rf"^## \[{re.escape(clean_version)}\].*?$"
    lines = content.split("\n")
    start_idx = None
    end_idx = None

    # Find the start of our version section
    for i, line in enumerate(lines):
        if re.match(version_pattern, line):
            start_idx = i + 1
            break

    if start_idx is None:
        return ""

    # Find the end (next version section or end of file)
    for i in range(start_idx, len(lines)):
        if lines[i].startswith("## [") and not lines[i].startswith("## [Unreleased]"):
            end_idx = i
            break

    if end_idx is None:
        end_idx = len(lines)

    # Extract the content between start and end
    changelog_lines = lines[start_idx:end_idx]

    # Remove empty lines at the end
    while changelog_lines and not changelog_lines[-1].strip():
        changelog_lines.pop()

    return "\n".join(changelog_lines).strip()


def main() -> None:
    """Extract and display changelog for specified version."""
    if len(sys.argv) != EXPECTED_ARGS:
        print("Usage: python extract_changelog.py <version>")
        sys.exit(1)

    version = sys.argv[1]
    changelog_content = extract_changelog_for_version(version)

    if not changelog_content:
        print(f"No changelog found for version {version}")
        sys.exit(1)

    print(changelog_content)


if __name__ == "__main__":
    main()
