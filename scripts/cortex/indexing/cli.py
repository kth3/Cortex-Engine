"""Command-line entrypoint for Cortex indexing."""
from __future__ import annotations

import argparse
import json

from cortex import storage as db
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.file_pipeline import index_file
from cortex.indexing.workspace import index_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cortex Indexer")
    parser.add_argument("workspace", help="Path to workspace")
    parser.add_argument("--file", help="Specific file to index (relative path)")
    parser.add_argument("--force", action="store_true", help="Force re-indexing")

    args = parser.parse_args(argv)

    if args.file:
        result = index_file(args.workspace, args.file)
        conn = db.get_connection(args.workspace)
        resolve_unresolved_edges(conn)
        conn.close()
        print(json.dumps(result, indent=2))
        return 0

    stats = index_workspace(args.workspace, force=args.force)
    print(json.dumps(stats, indent=2))
    return 0
