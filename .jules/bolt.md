## 2024-05-20 - [SQLite N+1 Query Optimization in Vector Search]
**Learning:** Resolving N+1 database queries by batching IN parameters requires careful consideration of SQLite variable limits.
**Action:** When performing batched lookups with IN clauses in SQLite, chunk the queries (e.g., sizes of 900) to avoid exceeding the historic `SQLITE_MAX_VARIABLE_NUMBER` limit.
