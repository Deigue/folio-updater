#!/usr/bin/env python3
"""Script to help prepare changelog entries for a new release.

This script helps manage the CHANGELOG.md file by:
1. Moving unreleased changes to a versioned section
2. Creating a new [Unreleased] section for future changes
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from utils.constants import TORONTO_TZ

CHANGELOG_PATH = Path("CHANGELOG.md")
EXPECTED_ARGS = 2


def prepare_release(version: str) -> None:
    """Prepare changelog for a new release version."""
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

    # Replace [Unreleased] with the new version
    updated_content = re.sub(
        unreleased_pattern,
        new_version_header,
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Add a new [Unreleased] section at the top
    new_unreleased_section = """## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

"""

    # Find where to insert the new unreleased section
    # Insert after the header but before the new version
    lines = updated_content.split("\n")
    insert_idx = None

    for i, line in enumerate(lines):
        if line.startswith("## [") and clean_version in line:
            insert_idx = i
            break

    if insert_idx is not None:
        # Insert the new unreleased section with proper spacing
        unreleased_lines = new_unreleased_section.split("\n")
        for unreleased_line in reversed(unreleased_lines):
            lines.insert(insert_idx, unreleased_line)
        updated_content = "\n".join(lines)

    # Write the updated content back
    CHANGELOG_PATH.write_text(updated_content, encoding="utf-8")
    print(f"✅ Prepared changelog for version {clean_version}")
    print(f"📝 Updated {CHANGELOG_PATH}")
    print("\n📋 Next steps:")
    print("1. Review the changelog entries")
    print("2. Edit any entries in the new version section as needed")
    print("3. Commit the changelog changes")
    print(
        f"4. Create and push the git tag: "
        f"git tag v{clean_version} && git push origin v{clean_version}",
    )


def main() -> None:
    """Prepare changelog for a new release."""
    if len(sys.argv) != EXPECTED_ARGS:
        print("Usage: python prepare_changelog.py <version>")
        print("Example: python prepare_changelog.py v1.2.0")
        sys.exit(1)

    version = sys.argv[1]
    prepare_release(version)


if __name__ == "__main__":
    main()
