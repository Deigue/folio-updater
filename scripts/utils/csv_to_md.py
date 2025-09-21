"""CSV to Markdown converter with YAML frontmatter.

This module reads a CSV file and converts each row to a Markdown file
with YAML frontmatter containing the row data.
"""

from __future__ import annotations

import csv
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TextIO


class CSVProcessingError(Exception):
    """Exception raised when CSV processing fails."""

    def __init__(self, message: str) -> None:
        """Initialize the exception with a message."""
        super().__init__(message)


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by replacing invalid characters with safe alternatives.

    Args:
        filename: The filename to sanitize

    Returns:
        A sanitized filename safe for filesystem use.
    """
    sanitized = filename.strip()
    sanitized = sanitized.replace(":", " -")
    sanitized = re.sub(r"\s+", " ", sanitized)
    sanitized = sanitized.replace('"', "").replace("'", "")

    # Replace filesystem-invalid characters with hyphens
    sanitized = re.sub(r'[\\/*?:"<>|]', "-", sanitized)

    # Handle special cases for better readability
    sanitized = sanitized.replace("&", "and")
    sanitized = sanitized.replace("#", "")

    # Remove leading/trailing hyphens and spaces
    sanitized = sanitized.strip(" -")

    # Replace multiple consecutive hyphens with single hyphen
    sanitized = re.sub(r"-+", "-", sanitized)

    # Ensure filename isn't empty
    if not sanitized:
        sanitized = "untitled"

    return sanitized


def _get_title_for_filename(
    row: dict[str, str],
    headers: list[str] | None,
    row_num: int,
) -> str:
    """Extract and sanitize title for filename from CSV row.

    Args:
        row: CSV row data
        headers: CSV headers
        row_num: Row number for fallback naming

    Returns:
        Sanitized title for use as filename
    """
    title_value = None

    # Try to get the Title column
    if headers and "Title" in headers:
        title_value = row.get("Title", "").strip()

    # Fallback to first column if Title is empty or doesn't exist
    if not title_value and headers:
        title_value = row.get(headers[0], f"row_{row_num}")

    # Final fallback if still empty
    if not title_value:
        title_value = f"row_{row_num}"

    return sanitize_filename(str(title_value))


def _write_yaml_frontmatter(
    md_file: TextIO,
    headers: list[str] | None,
    row: dict[str, str],
) -> None:
    """Write YAML frontmatter to markdown file.

    Args:
        md_file: Open file handle
        headers: CSV headers
        row: CSV row data
    """
    md_file.write("---\n")
    if headers:
        for header in headers:
            # Always include the original Title in frontmatter,
            # even if used for filename
            if row[header]:
                # Handle titles with special characters that might break YAML
                if header == "Title":
                    # Escape quotes in YAML values
                    title_value = row[header].replace('"', '\\"')
                    md_file.write(f'{header}: "{title_value}"\n')
                else:
                    md_file.write(f"{header}: {row[header]}\n")
    md_file.write("---\n")


def process_csv_to_md(csv_file_path: Path, output_dir: Path) -> None:
    """Process a CSV file and convert each row to a Markdown file.

    Args:
        csv_file_path: Path to the CSV file to process
        output_dir: Directory where to store the generated MD files

    Raises:
        FileNotFoundError: If the CSV file doesn't exist
        CSVProcessingError: If the CSV file has no headers or is empty
    """
    # Make sure the output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read the CSV file
    with csv_file_path.open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames

        if headers is None:
            msg = "CSV file has no headers"
            raise CSVProcessingError(msg)

        if not headers:
            msg = "CSV file has empty headers"
            raise CSVProcessingError(msg)

        # Iterate through each row in the CSV
        for row_num, row in enumerate(reader, start=1):
            headers_list = list(headers) if headers else []
            title = _get_title_for_filename(row, headers_list, row_num)
            md_filename = output_dir / f"{title}.md"

            # Open the .md file for writing
            with md_filename.open("w", encoding="utf-8") as md_file:
                _write_yaml_frontmatter(md_file, headers_list, row)


def select_csv_file() -> Path | None:
    """Open a file dialog to select a CSV file.

    Returns:
        Path to the selected CSV file, or None if cancelled
    """
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    file_path = filedialog.askopenfilename(
        title="Select CSV file",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
    )

    root.destroy()

    return Path(file_path) if file_path else None


def select_output_directory() -> Path | None:
    """Open a folder dialog to select the output directory.

    Returns:
        Path to the selected directory, or None if cancelled
    """
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    folder_path = filedialog.askdirectory(
        title="Select output directory for Markdown files",
    )

    root.destroy()

    return Path(folder_path) if folder_path else None


def main() -> None:
    """Run the CSV to MD converter."""
    try:
        # Select CSV file
        csv_file = select_csv_file()
        if csv_file is None:
            print("No CSV file selected. Exiting.")
            return

        if not csv_file.exists():
            messagebox.showerror("Error", f"CSV file not found: {csv_file}")
            return

        # Select output directory
        output_dir = select_output_directory()
        if output_dir is None:
            print("No output directory selected. Exiting.")
            return

        # Process the CSV file
        print(f"Processing CSV file: {csv_file}")
        print(f"Output directory: {output_dir}")

        process_csv_to_md(csv_file, output_dir)

        messagebox.showinfo(
            "Success",
            f"Successfully converted CSV to Markdown files in {output_dir}",
        )
        print("Conversion completed successfully!")

    except (CSVProcessingError, OSError) as e:
        error_msg = f"Error processing CSV file: {e}"
        messagebox.showerror("Error", error_msg)
        print(error_msg)


if __name__ == "__main__":
    main()
