import sqlite3
import time
import uuid

# Create an in-memory DB and setup tables
conn = sqlite3.connect(":memory:")
conn.execute("""
    CREATE TABLE IF NOT EXISTS file_cache (
        file_path TEXT PRIMARY KEY,
        hash TEXT,
        last_indexed_at INTEGER,
        workspace_id TEXT
    )
""")
conn.execute("""
    CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY,
        file_path TEXT,
        name TEXT
    )
""")

# Setup data
# Let's say we have 10,000 files in cache, and 9000 of them are to be deleted.
current_files = [f"file_{i}.py" for i in range(1000)]
cached_paths = [f"file_{i}.py" for i in range(10000)]

for path in cached_paths:
    conn.execute("INSERT INTO file_cache (file_path, hash) VALUES (?, ?)", (path, "hash"))
    for j in range(5): # 5 nodes per file
        conn.execute("INSERT INTO nodes (id, file_path, name) VALUES (?, ?, ?)", (str(uuid.uuid4()), path, "node"))

conn.commit()


def cleanup_original(conn, current_files):
    cached_files = conn.execute("SELECT file_path FROM file_cache").fetchall()
    current_set = set(current_files)
    for (cached_path,) in cached_files:
        if cached_path not in current_set:
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (cached_path,))
            conn.execute("DELETE FROM file_cache WHERE file_path = ?", (cached_path,))

start_time = time.time()
cleanup_original(conn, current_files)
end_time = time.time()

print(f"Original cleanup took: {end_time - start_time:.4f} seconds")

# Reset data
conn.execute("DELETE FROM file_cache")
conn.execute("DELETE FROM nodes")

for path in cached_paths:
    conn.execute("INSERT INTO file_cache (file_path, hash) VALUES (?, ?)", (path, "hash"))
    for j in range(5): # 5 nodes per file
        conn.execute("INSERT INTO nodes (id, file_path, name) VALUES (?, ?, ?)", (str(uuid.uuid4()), path, "node"))
conn.commit()

def cleanup_optimized(conn, current_files):
    cached_files = conn.execute("SELECT file_path FROM file_cache").fetchall()
    current_set = set(current_files)
    paths_to_delete = [(cached_path,) for (cached_path,) in cached_files if cached_path not in current_set]

    if paths_to_delete:
        conn.executemany("DELETE FROM nodes WHERE file_path = ?", paths_to_delete)
        conn.executemany("DELETE FROM file_cache WHERE file_path = ?", paths_to_delete)

start_time = time.time()
cleanup_optimized(conn, current_files)
end_time = time.time()

print(f"Optimized cleanup took: {end_time - start_time:.4f} seconds")
