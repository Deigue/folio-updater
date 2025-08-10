"""
Bootstrap the project by ensuring the project root is in sys.path.
This allows importing from src/ anywhere (scripts, notebooks, etc.)
without adjusting PYTHONPATH manually.
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent

# Detect if we've already added the root to avoid duplicates
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))