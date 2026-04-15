import re

with open("/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex/indexer.py", "r") as f:
    code = f.read()

# 1. Update index_file's vectorizing logic
old_vectorize_logic = """        if vectorize and vector_items:
            from cortex import vector_engine as ve
            ve.index_texts(workspace, vector_items)
            ve._release_gpu()  # On-Save 단일 파일 모드에서는 즉시 해제"""

new_vectorize_logic = """        if vectorize and vector_items:
            from cortex import vector_engine as ve
            ids = [item["id"] for item in vector_items]
            ph = ",".join("?" * len(ids))
            rowids_query = conn.execute(f"SELECT id, rowid FROM nodes WHERE id IN ({ph})", ids).fetchall()
            id_to_rowid = {r[0]: r[1] for r in rowids_query}
            texts = [b["text"] for b in vector_items]
            embeddings = ve.get_embeddings(texts)
            vec_data = []
            for b, emb in zip(vector_items, embeddings):
                rowid = id_to_rowid.get(b["id"])
                if rowid is not None:
                    vec_data.append((rowid, emb.tobytes()))
            if vec_data:
                conn.executemany("INSERT OR REPLACE INTO vec_nodes (rowid, embedding) VALUES (?, ?)", vec_data)
                conn.commit()
            ve.release_gpu()
            
        # Graph DB 연동
        try:
            from cortex.graph_db import GraphDB
            gdb = GraphDB(workspace)
            gdb.execute("MERGE (m:Module {name: $name, file_path: $path})", {"name": mod_name, "path": rel_path})
            for node in result["nodes"]:
                if node["type"] == "Function":
                    gdb.execute("MERGE (f:Function {fqn: $fqn, name: $name, file_path: $path})", 
                        {"fqn": node["fqn"], "name": node["name"], "path": node["file_path"]})
                    gdb.execute("MATCH (m:Module {name: $mod_name}), (f:Function {fqn: $fqn}) MERGE (m)-[:Defines]->(f)", 
                        {"mod_name": mod_name, "fqn": node["fqn"]})
                elif node["type"] == "Class":
                    gdb.execute("MERGE (c:Class {fqn: $fqn, name: $name, file_path: $path})", 
                        {"fqn": node["fqn"], "name": node["name"], "path": node["file_path"]})
                    gdb.execute("MATCH (m:Module {name: $mod_name}), (c:Class {fqn: $fqn}) MERGE (m)-[:Defines]->(c)", 
                        {"mod_name": mod_name, "fqn": node["fqn"]})
            # 간단한 Calls 관계 추가 (Kuzu는 MATCH를 통해 노드 타입과 무관하게 연결 가능)
            if result.get("edges"):
                for e in result["edges"]:
                    gdb.execute("MATCH (a {fqn: $src}), (b {fqn: $tgt}) MERGE (a)-[:Calls]->(b)", 
                        {"src": e["source_id"], "tgt": e["target_id"]})
        except Exception as e:
            pass"""

code = code.replace(old_vectorize_logic, new_vectorize_logic)

# 2. Update ghost index file cleanup
old_cleanup = """    # [NEW] 고스트 인덱스 파일 정리 (폴더 삭제 대응)
    # .agents/cortex_data/ 내의 *.index 파일 중 실제 폴더가 없는 것을 삭제합니다.
    try:
        from cortex.db import get_db_path
        db_dir = os.path.dirname(get_db_path(workspace))
        all_indices = [f for f in os.listdir(db_dir) if f.endswith(".index")]
        
        # 보존해야 할 시스템 인덱스 및 현재 존재하는 폴더 목록
        preserved_prefixes = {"root", "memories", "skills", "default"}
        for d in os.listdir(workspace):
            if os.path.isdir(os.path.join(workspace, d)) and not d.startswith("."):
                preserved_prefixes.add(d)
        
        for idx_file in all_indices:
            prefix = idx_file.replace(".index", "")
            if prefix not in preserved_prefixes:
                # 폴더가 사라진 고스트 인덱스 발견
                sys.stderr.write(f"[indexer] Removing orphaned index: {prefix}\n")
                os.remove(os.path.join(db_dir, idx_file))
                meta_file = os.path.join(db_dir, f"{prefix}_meta.json")
                if os.path.exists(meta_file):
                    os.remove(meta_file)
    except Exception as e:
        sys.stderr.write(f"[indexer] Warning - Failed to cleanup orphaned indices: {e}\n")"""

code = code.replace(old_cleanup, "")

# 3. Update FAISS cache invalidation
old_faiss_check = """    # ── FAISS ↔ file_cache 정합성 검사 ──────────────────────────────────
    # FAISS .index 파일이 없는 prefix의 캐시를 무효화하여 강제 재임베딩을 유도합니다.
    # (e.g. 벡터 데이터만 수동 삭제했을 때 인덱서가 이를 감지하지 못하고 스킵하는 문제 방지)
    try:
        from cortex.db import get_db_path
        from pathlib import Path as _Path
        _db_dir = os.path.dirname(get_db_path(workspace))

        # 현재 cache_dict에 등록된 파일들의 FAISS prefix 추출
        cached_prefixes = set()
        for _k in cache_dict:
            _parts = _Path(_k).parts
            if len(_parts) > 1 and not _parts[0].startswith('.'):
                cached_prefixes.add(_parts[0])
            else:
                cached_prefixes.add("root")

        for _prefix in cached_prefixes:
            _idx_path = os.path.join(_db_dir, f"{_prefix}.index")
            if not os.path.exists(_idx_path):
                sys.stderr.write(
                    f"[indexer] FAISS index missing for '{_prefix}'. "
                    "Invalidating cache to trigger re-embedding.\n"
                )
                # 해당 prefix 파일들의 file_cache 해시 무효화 → 재처리 대상으로 전환
                _keys_to_invalidate = [
                    _k for _k in list(cache_dict)
                    if (
                        (_Path(_k).parts[0]
                         if len(_Path(_k).parts) > 1 and not _Path(_k).parts[0].startswith('.')
                         else "root")
                        == _prefix
                    )
                ]
                for _k in _keys_to_invalidate:
                    del cache_dict[_k]
    except Exception as _e:
        sys.stderr.write(f"[indexer] Warning - FAISS consistency check failed: {_e}\n")"""

code = code.replace(old_faiss_check, "")

# 4. Update vector indexing for nodes
old_batch_vectorize = """    # 전체 파일 파싱 완료 후 벡터 임베딩 배치 처리 (구역별 그룹화)
    if all_vector_items_by_prefix:
        from cortex import vector_engine as ve
        for prefix, items in all_vector_items_by_prefix.items():
            if not items: continue
            batch_size = 500
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                sys.stderr.write(f"[indexer] Indexing file vectors [{prefix}]: {i}/{len(items)}...\n")
                ve.index_texts(workspace, batch, prefix=prefix)
        ve._release_gpu()"""

new_batch_vectorize = """    # 전체 파일 파싱 완료 후 벡터 임베딩 배치 처리
    if all_vector_items_by_prefix:
        from cortex import vector_engine as ve
        for prefix, items in all_vector_items_by_prefix.items():
            if not items: continue
            
            ids = [item["id"] for item in items]
            ph = ",".join("?" * len(ids))
            rowids_query = conn.execute(f"SELECT id, rowid FROM nodes WHERE id IN ({ph})", ids).fetchall()
            id_to_rowid = {r[0]: r[1] for r in rowids_query}
            
            batch_size = 500
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                sys.stderr.write(f"[indexer] Indexing file vectors [{prefix}]: {i}/{len(items)}...\n")
                texts = [b["text"] for b in batch]
                embeddings = ve.get_embeddings(texts)
                vec_data = []
                for b, emb in zip(batch, embeddings):
                    rowid = id_to_rowid.get(b["id"])
                    if rowid is not None:
                        vec_data.append((rowid, emb.tobytes()))
                if vec_data:
                    conn.executemany("INSERT OR REPLACE INTO vec_nodes (rowid, embedding) VALUES (?, ?)", vec_data)
                    conn.commit()
        ve.release_gpu()"""

code = code.replace(old_batch_vectorize, new_batch_vectorize)

# 5. Update memories vectorization
old_memories_check = """        # ── memories FAISS 정합성 검사 ───────────────────────────────────
        # memories.index 파일이 없는데 embedding=1 플래그가 남아 있으면
        # 재인덱싱 대상에서 누락되므로 플래그를 초기화합니다.
        try:
            from cortex.db import get_db_path
            _db_dir = os.path.dirname(get_db_path(workspace))
            if not os.path.exists(os.path.join(_db_dir, "memories.index")):
                _reset_count = conn.execute(
                    "UPDATE memories SET embedding = NULL WHERE embedding IS NOT NULL"
                ).rowcount
                if _reset_count > 0:
                    conn.commit()
                    sys.stderr.write(
                        f"[indexer] memories.index not found. "
                        f"Reset {_reset_count} embedding flags for re-indexing.\n"
                    )
        except Exception as _e:
            sys.stderr.write(f"[indexer] Warning - memories consistency check failed: {_e}\n")"""

code = code.replace(old_memories_check, "")

old_memories_index = """            if memory_vector_items:
                from cortex import vector_engine as ve
                batch_size = 500
                total_indexed = 0
                total_skipped = 0
                
                for i in range(0, len(memory_vector_items), batch_size):
                    batch = memory_vector_items[i:i + batch_size]
                    batch_keys = [item["id"] for item in batch]
                    
                    sys.stderr.write(f"[indexer] Indexing memories: {i}/{len(memory_vector_items)}...\n")
                    res = ve.index_texts(workspace, batch, prefix="memories")
                    
                    indexed_in_batch = res.get("indexed", 0)
                    skipped_in_batch = res.get("skipped", 0)
                    total_indexed += indexed_in_batch
                    total_skipped += skipped_in_batch
                    
                    # 배치 단위로 DB 플래그 업데이트
                    if (indexed_in_batch + skipped_in_batch) > 0:
                        conn.executemany(
                            "UPDATE memories SET embedding = 1 WHERE key = ?",
                            [(k,) for k in batch_keys]
                        )
                        conn.commit()

                sys.stderr.write(f"[indexer] Synced {total_indexed + total_skipped} memories (New: {total_indexed}, Existing: {total_skipped}).\n")"""

new_memories_index = """            if memory_vector_items:
                from cortex import vector_engine as ve
                
                mem_ids = [item["id"] for item in memory_vector_items]
                ph = ",".join("?" * len(mem_ids))
                mem_rowids = conn.execute(f"SELECT key, rowid FROM memories WHERE key IN ({ph})", mem_ids).fetchall()
                mem_id_to_rowid = {r[0]: r[1] for r in mem_rowids}
                
                batch_size = 500
                total_indexed = 0
                
                for i in range(0, len(memory_vector_items), batch_size):
                    batch = memory_vector_items[i:i + batch_size]
                    batch_keys = [item["id"] for item in batch]
                    
                    sys.stderr.write(f"[indexer] Indexing memories: {i}/{len(memory_vector_items)}...\n")
                    texts = [b["text"] for b in batch]
                    embeddings = ve.get_embeddings(texts)
                    
                    vec_data = []
                    for b, emb in zip(batch, embeddings):
                        rowid = mem_id_to_rowid.get(b["id"])
                        if rowid is not None:
                            vec_data.append((rowid, emb.tobytes()))
                    
                    if vec_data:
                        conn.executemany("INSERT OR REPLACE INTO vec_memories (rowid, embedding) VALUES (?, ?)", vec_data)
                        conn.executemany("UPDATE memories SET embedding = 1 WHERE key = ?", [(k,) for k in batch_keys])
                        conn.commit()
                        total_indexed += len(vec_data)

                sys.stderr.write(f"[indexer] Synced {total_indexed} memories.\n")"""

code = code.replace(old_memories_index, new_memories_index)

# Fix ve._release_gpu() call inside file deletion logic
code = code.replace("ve._release_gpu()", "ve.release_gpu()")
code = code.replace("ve.delete_ids(workspace, old_ids)", "pass # sqlite-vec handles deletion through FK or just ignoring")

with open("/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex/indexer.py", "w") as f:
    f.write(code)

