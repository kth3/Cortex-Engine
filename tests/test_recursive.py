import sqlite3
import time

def setup_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE edges (
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL
    );
    """)
    num_nodes = 500
    for i in range(num_nodes - 1):
        conn.execute("INSERT INTO edges (from_id, to_id) VALUES (?, ?)",
                     (f"node_{i}", f"node_{i+1}"))
    conn.commit()
    return conn

def find_path_bfs(conn, start_id, end_id):
    from collections import deque
    queue = deque([[start_id]])
    visited = {start_id}
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == end_id:
            return path
        callees = conn.execute("SELECT to_id FROM edges WHERE from_id = ?", (node,)).fetchall()
        for c in callees:
            cid = c[0]
            if cid not in visited:
                visited.add(cid)
                queue.append(path + [cid])
    return None

def find_path_recursive(conn, start_id, end_id):
    query = """
    WITH RECURSIVE
      bfs(id, path_str) AS (
        SELECT ?, ?
        UNION ALL
        SELECT e.to_id, b.path_str || ',' || e.to_id
        FROM bfs b
        JOIN edges e ON b.id = e.from_id
        WHERE b.id != ?
      )
    SELECT path_str FROM bfs WHERE id = ? LIMIT 1;
    """
    row = conn.execute(query, (start_id, start_id, end_id, end_id)).fetchone()
    if row:
        return row[0].split(',')
    return None

conn = setup_db()

start = time.perf_counter()
for _ in range(50):
    find_path_bfs(conn, "node_0", "node_499")
print("BFS:", time.perf_counter() - start)

start = time.perf_counter()
for _ in range(50):
    find_path_recursive(conn, "node_0", "node_499")
print("Recursive:", time.perf_counter() - start)
