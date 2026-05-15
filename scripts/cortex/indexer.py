"""Thin entrypoint for Cortex indexing CLI and legacy imports."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex.indexing import SUPPORTED_EXTENSIONS, incremental_index_changed, index_file, index_workspace
from cortex.indexing.cli import main

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "incremental_index_changed",
    "index_file",
    "index_workspace",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
