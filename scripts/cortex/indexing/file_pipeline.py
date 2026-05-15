"""Single-file indexing pipeline."""
from __future__ import annotations

import hashlib
import os
import time

from cortex import storage as db
from cortex.config.settings import load_settings
from cortex.indexing.cleanup import cleanup_file_records
from cortex.indexing.constants import SUPPORTED_EXTENSIONS
from cortex.indexing.graph_sync import sync_file_graph
from cortex.indexing.queries import FILE_CACHE_HASH_BY_PATH_SQL
from cortex.indexing.records import build_node_rows, insert_edges, insert_nodes, upsert_file_cache
from cortex.indexing.vector_store import dedupe_vector_items, persist_node_vectors
from cortex.scanner.filters import get_module_name
from cortex.utils.text import compute_hash, strip_frontmatter


def read_text_file(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def index_file(workspace: str, rel_path: str, conn=None, vectorize: bool = True, use_gpu: bool = None):
    """단일 파일에 대한 정밀 인덱싱 및 임베딩.

    Args:
        vectorize: True(기본) = 즉시 벡터 임베딩까지 수행 (On-Save 단일 파일용).
                   False = 파싱/DB 저장만 수행하고 vector_items를 반환값에 포함
                           (index_workspace의 배치 모드에서 사용).
        use_gpu: None(자동), True(GPU강제), False(CPU강제)
    """
    full_path = os.path.join(workspace, rel_path)
    if not os.path.exists(full_path):
        close_conn = False
        if conn is None:
            conn = db.get_connection(workspace)
            close_conn = True
        try:
            cleanup_file_records(conn, rel_path)
            conn.commit()
            return {"status": "deleted", "reason": f"File removed from DB"}
        except Exception as e:
            return {"error": f"Cleanup failed: {e}"}
        finally:
            if close_conn:
                conn.close()

    try:
        source = read_text_file(full_path)
    except Exception as e:
        return {"error": str(e)}

    settings = load_settings(workspace)
    workspace_id = hashlib.md5(workspace.encode()).hexdigest()[:8]
    ext = os.path.splitext(rel_path)[1]
    mod_name = get_module_name(rel_path, settings)
    _, parser_func = SUPPORTED_EXTENSIONS.get(ext, (None, None))

    if not parser_func:
        return {"status": "skipped", "reason": "unsupported extension"}

    try:
        current_hash = compute_hash(source)
        close_check = conn is None
        check_conn = conn if conn is not None else db.get_connection(workspace)
        try:
            cached = check_conn.execute(
                FILE_CACHE_HASH_BY_PATH_SQL, (rel_path,)
            ).fetchone()
            if cached and cached[0] == current_hash:
                return {"status": "skipped", "reason": "hash unchanged", "chunks": 0}
        finally:
            if close_check:
                check_conn.close()
    except Exception:
        pass

    close_conn = False
    if conn is None:
        conn = db.get_connection(workspace)
        close_conn = True

    try:
        result = parser_func(rel_path, source)
        clean_source = strip_frontmatter(source) if rel_path.startswith(".cortex/") else source

        old_ids = cleanup_file_records(conn, rel_path)
        is_update = bool(old_ids)

        nodes_data, vector_items = build_node_rows(
            result["nodes"],
            rel_path=rel_path,
            clean_source=clean_source,
            module_name=mod_name,
            workspace_id=workspace_id,
        )

        if nodes_data:
            insert_nodes(conn, nodes_data)

        if result.get("edges"):
            insert_edges(conn, result["edges"])

        upsert_file_cache(conn, rel_path, compute_hash(source), int(time.time()), workspace_id)

        if vector_items:
            vector_items = dedupe_vector_items(vector_items)

        if vectorize and vector_items:
            persist_node_vectors(conn, vector_items, use_gpu=use_gpu)

        try:
            sync_file_graph(
                workspace=workspace,
                module_name=mod_name,
                rel_path=rel_path,
                nodes=result["nodes"],
                edges=result.get("edges", []),
            )
        except Exception:
            pass

        conn.commit()

        result = {"status": "updated" if is_update else "created", "nodes": len(nodes_data), "chunks": len(nodes_data)}
        if not vectorize:
            result["_vector_items"] = vector_items
        return result
    finally:
        if close_conn:
            conn.close()
