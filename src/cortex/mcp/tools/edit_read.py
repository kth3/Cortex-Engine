"""Edit read helpers."""
from __future__ import annotations

from cortex.editing import read_with_hash


def call_read_file_with_hash(ctx, args):
    return read_with_hash(ctx.workspace, args["file_path"])
