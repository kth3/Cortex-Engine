import json

path = "/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex/persistent_memory.py"
with open(path, "r") as f:
    content = f.read()

old_vec = """        # 1. 벡터 검색 (의미 기반)
        try:
            vector_results = ve.search_similar(self.workspace, query, top_k=limit)
            missing_keys = [vr.get("id") for vr in vector_results if vr.get("id")]

            if missing_keys:
                fetched_data = self.read_batch(project_id, missing_keys)
                for key in missing_keys:
                    if key in fetched_data:
                        d = fetched_data[key]
                        if not category or d.get("category") == category:
                            results_map[key] = d
        except Exception as e:
            import sys
            sys.stderr.write(f"[persistent_memory] Vector search failed: {e}\\n")"""

new_vec = """        # 1. 벡터 검색 (sqlite-vec 기반)
        conn = get_connection(self.workspace)
        try:
            query_vec = ve.get_embeddings([query])[0]
            vec_rows = conn.execute("SELECT rowid FROM vec_memories WHERE embedding MATCH ? AND k = ?", (query_vec.tobytes(), limit * 2)).fetchall()
            if vec_rows:
                ph = ",".join(["?"] * len(vec_rows))
                rowids = [r[0] for r in vec_rows]
                db_rows = conn.execute(f"SELECT * FROM memories WHERE rowid IN ({ph})").fetchall()
                for r in db_rows:
                    d = dict(r)
                    if not category or d.get("category") == category:
                        d["tags"] = json.loads(d.get("tags") or "[]")
                        d["relationships"] = json.loads(d.get("relationships") or "{}")
                        results_map[d["key"]] = d
        except Exception as e:
            import sys
            sys.stderr.write(f"[persistent_memory] Vector search failed: {e}\\n")"""

content = content.replace(old_vec, new_vec)

old_search_know = """        # 3. FAISS 벡터 검색
        vec_results = []
        if ve_module is not None:
            try:
                vec_results = ve_module.search_similar(self.workspace, query, top_k=limit, use_gpu=False)
            except Exception:
                pass"""

new_search_know = """        # 3. 벡터 검색은 self.search에서 이미 통합 처리됨
        vec_results = []"""

content = content.replace(old_search_know, new_search_know)

with open(path, "w") as f:
    f.write(content)


path_sm = "/home/developer/.gemini/tmp/my-project/cortex-temp/scripts/cortex/skill_manager.py"
with open(path_sm, "r") as f:
    content_sm = f.read()

# skill_manager.py의 search_similar 제거
content_sm = content_sm.replace("vec_results = ve.search_similar(self.workspace, query, top_k=limit, use_gpu=False)", "vec_results = [] # FAISS removed")
with open(path_sm, "w") as f:
    f.write(content_sm)

