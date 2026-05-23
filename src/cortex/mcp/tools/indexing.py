"""MCP indexing status handler."""
from cortex import storage as pc_db


def call_get_index_status(ctx, args):
    conn = pc_db.get_connection(ctx.workspace)
    try:
        return pc_db.get_stats(conn)
    finally:
        conn.close()
