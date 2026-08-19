"""Reading a single keypress, for the pager and the audit-block prompts."""

from __future__ import annotations

# Cross-platform single character input
try:
    import msvcrt  # Windows

    def getch() -> str:
        r"""Get a single character from stdin without pressing Enter.

        Special keys (arrows, function keys, etc.) are reported by msvcrt as
        a two-byte sequence: a prefix byte (b"\\x00" or b"\\xe0") followed by
        a scan code byte, neither of which is valid UTF-8. Swallow the pair
        and report no usable input rather than raising UnicodeDecodeError.
        """
        raw = msvcrt.getch()
        if raw in (b"\x00", b"\xe0"):
            msvcrt.getch()  # discard the scan code byte
            return ""
        try:
            return raw.decode("utf-8").lower()
        except UnicodeDecodeError:
            return ""

except ImportError:
    # Unix/Linux/Mac - fallback to regular input for now
    def getch() -> str:
        """Get input (fallback for non-Windows systems)."""
        return input().strip().lower()
