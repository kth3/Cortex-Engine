"""
Cortex 인덱싱 엔진 (v3.1 — Modularized + Logging)
파일 스캔 → 지능형 필터링 → 파서 호출 → DB 저장 → 벡터 임베딩 → 증분 인덱싱

유틸리티: indexer_utils.py
벡터 배치: vectorizer.py
"""
import os
import sys
import time
import datetime
import hashlib
from pathlib import Path

# 패키지 내부 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex.logger import get_logger
log = get_logger("indexer")
from cortex import db
from cortex.indexer_utils import (
    strip_frontmatter, compute_hash, load_settings,
    get_module_name, scan_files,
)
from cortex.embeddings import (
    batch_vectorize_nodes, batch_vectorize_memories, detect_gpu,
)

from cortex.indexing import SUPPORTED_EXTENSIONS
from cortex.indexing.cleanup import cleanup_deleted_files, cleanup_file_records
from cortex.indexing.edge_resolver import resolve_unresolved_edges
from cortex.indexing.graph_sync import sync_file_graph
from cortex.indexing.queries import (
    DELETE_FILE_CACHE_SQL,
    FILE_CACHE_HASH_BY_PATH_SQL,
    LAST_INDEXED_AT_SQL,
    SELECT_FILE_CACHE_SQL,
    UPSERT_LAST_INDEXED_AT_SQL,
)
from cortex.indexing.records import (
    build_node_rows,
    insert_edges,
    insert_nodes,
    upsert_file_cache,
)
from cortex.indexing.rules_sync import sync_rules_to_memories
from cortex.indexing.vector_store import dedupe_vector_items, persist_node_vectors


# ==============================================================================
# 핵심 인덱싱 로직
# ==============================================================================

def _read_text_file(path):
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
        source = _read_text_file(full_path)
    except Exception as e:
        return {"error": str(e)}

    settings = load_settings(workspace)
    workspace_id = hashlib.md5(workspace.encode()).hexdigest()[:8]
    ext = os.path.splitext(rel_path)[1]
    mod_name = get_module_name(rel_path, settings)
    _, parser_func = SUPPORTED_EXTENSIONS.get(ext, (None, None))
    
    if not parser_func:
        return {"status": "skipped", "reason": "unsupported extension"}

    # hash pre-check: file_cache에 동일 hash가 있으면 임베딩 없이 즉시 반환
    try:
        current_hash = compute_hash(source)
        _close_check = conn is None
        _check_conn  = conn if conn is not None else db.get_connection(workspace)
        try:
            cached = _check_conn.execute(
                FILE_CACHE_HASH_BY_PATH_SQL, (rel_path,)
            ).fetchone()
            if cached and cached[0] == current_hash:
                return {"status": "skipped", "reason": "hash unchanged", "chunks": 0}
        finally:
            if _close_check:
                _check_conn.close()
    except Exception:
        pass  # 체크 실패 시 정상 인덱싱 진행

    close_conn = False
    if conn is None:
        conn = db.get_connection(workspace)
        close_conn = True

    try:
        result = parser_func(rel_path, source)
        clean_source = strip_frontmatter(source) if rel_path.startswith(".agents/") else source
        
        # 기존 노드/엣지 삭제
        old_ids = cleanup_file_records(conn, rel_path)
        # 기존 데이터 존재 여부 확인 (CREATED vs UPDATED 구분용)
        is_update = bool(old_ids)
        
        # 신규 노드 저장
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
        
        # Deduplicate vector_items by id to prevent UNIQUE constraint failed on vec_nodes
        if vector_items:
            vector_items = dedupe_vector_items(vector_items)

        if vectorize and vector_items:
            persist_node_vectors(conn, vector_items, use_gpu=use_gpu)
            
        # Graph DB 연동 (UNWIND 배치 upsert — N+1 제거)
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
            # 배치 모드: 호출자가 일괄 처리하도록 vector_items 반환
            result["_vector_items"] = vector_items
        return result
    finally:
        if close_conn:
            conn.close()


def _sync_rules_to_memories(workspace: str, conn):
    """규칙/프로토콜 .md 문서를 memories 테이블에 동기화."""
    return sync_rules_to_memories(workspace, conn)


def _cleanup_deleted_files(workspace: str, conn, current_files: list):
    """DB에는 등록되어 있으나 현재 디스크에는 없는 파일을 찾아 제거"""
    return cleanup_deleted_files(workspace, conn, current_files)


# ==============================================================================
# 기회적 증분 인덱싱 (Opportunistic Incremental Indexing)
# ==============================================================================

_last_opportunistic_check = 0.0  # 디바운스용 타임스탬프 (모듈 레벨)
OPPORTUNISTIC_COOLDOWN = 60      # 최소 60초 간격으로만 체크

def incremental_index_changed(workspace: str) -> dict:
    """경량 증분 인덱싱: 마지막 인덱싱 이후 변경된 파일(전체 코드 포함)만 CPU로 즉석 처리.
    
    데몬 없이 다른 MCP 도구(pc_capsule 등) 호출 시 기회적으로 실행.
    전체 문서를 파싱하지 않고, scan_files 결과 전체에 대해 mtime만 확인하여
    변경된 파일만 빠르게 반영. (60초 디바운스로 연속 스캔 방지)
    """
    global _last_opportunistic_check
    
    now = time.time()
    if now - _last_opportunistic_check < OPPORTUNISTIC_COOLDOWN:
        return {"status": "cooldown"}
    _last_opportunistic_check = now
    
    conn = db.get_connection(workspace)
    
    # 마지막 인덱싱 시각 조회
    row = conn.execute(LAST_INDEXED_AT_SQL).fetchone()
    if not row:
        conn.close()
        return {"status": "skip", "reason": "no previous index"}
    
    last_indexed = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").timestamp()
    
    # 프로젝트 전체 파일을 스캔하여 포함 대상 리스트업
    all_files = scan_files(workspace, SUPPORTED_EXTENSIONS)
    
    changed_files = []
    for rel_path in all_files:
        fpath = os.path.join(workspace, rel_path)
        try:
            # mtime만 빠르게 체크
            if os.path.exists(fpath) and os.path.getmtime(fpath) > last_indexed:
                changed_files.append(rel_path)
        except OSError:
            continue
    
    # 삭제된 파일 감지 및 정리 (유령 노드 방지)
    cleanup_deleted_files(workspace, conn, all_files)
    
    if not changed_files:
        conn.close()
        return {"status": "clean", "checked_files": len(all_files)}
    
    # CPU 전용 증분 인덱싱 (GPU 시동 비용 없이 즉시)
    log.info("Opportunistic indexing: %d changed files detected (out of %d).", len(changed_files), len(all_files))
    indexed = 0
    vector_items = []
    for rel_path in changed_files:
        res = index_file(workspace, rel_path, conn=conn, vectorize=False)
        if "error" not in res:
            indexed += 1
            vector_items.extend(res.get("_vector_items", []))
    
    # 규칙/프로토콜 동기화 (memories 테이블 갱신)
    sync_rules_to_memories(workspace, conn)
    
    # CPU 전용 벡터 임베딩 (소량이므로 GPU 불필요)
    if vector_items:
        batch_vectorize_nodes(conn, {"opportunistic": vector_items}, use_gpu=False, workspace=workspace)
    
    try:
        batch_vectorize_memories(conn, use_gpu=False, workspace=workspace)
    except Exception:
        pass
    
    conn.execute(
        UPSERT_LAST_INDEXED_AT_SQL,
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    
    resolve_unresolved_edges(conn)
    conn.close()
    
    log.info("Opportunistic indexing complete: %d files indexed (CPU).", indexed)
    return {"status": "indexed", "changed": len(changed_files), "indexed": indexed}

def _resolve_unresolved_edges(conn) -> None:
    """unresolved 엣지 target_id를 실제 노드 UUID로 교체."""
    return resolve_unresolved_edges(conn)


def _sync_skills(workspace):
    from cortex.skills.manager import SkillManager
    log.info("Auto-syncing skills to memories DB...")
    try:
        sm = SkillManager(workspace)
        sm.sync_skills(workspace)
    except Exception as e:
        log.warning("Skill sync failed: %s", e)


def _load_file_cache(conn, force):
    if force:
        # force=True: index_file 내부 hash 체크도 우회하도록 캐시 전체 초기화
        conn.execute(DELETE_FILE_CACHE_SQL)
        conn.commit()
        return {}

    cached_rows = conn.execute(SELECT_FILE_CACHE_SQL).fetchall()
    return {row[0]: row[1] for row in cached_rows}


def _vector_prefix_for_path(rel_path):
    # 소속 폴더 분석 (최상단 폴더 기준)
    parts = Path(rel_path).parts
    prefix = "root"
    if len(parts) > 1 and not parts[0].startswith("."):
        prefix = parts[0]
    return prefix


def _collect_index_result(stats, all_vector_items_by_prefix, rel_path, res):
    if "error" in res:
        stats["errors"] += 1
        return

    stats["indexed"] += 1
    prefix = _vector_prefix_for_path(rel_path)
    if prefix not in all_vector_items_by_prefix:
        all_vector_items_by_prefix[prefix] = []
    all_vector_items_by_prefix[prefix].extend(res.get("_vector_items", []))


def _release_local_cuda_model_after_indexing() -> None:
    """Release only a local CUDA fallback embedding model.

    The engine worker owns daemon GPU residency. This helper must not imply
    that indexer releases the daemon/worker VRAM.
    """
    try:
        from cortex.embeddings import provider

        if getattr(provider, "_model_device", None) != "cuda":
            return

        from cortex.embeddings.hardware import release_gpu

        release_gpu()
        log.info("Local CUDA embedding model released after full indexing.")
    except Exception:
        log.debug("Local CUDA embedding model release skipped.", exc_info=True)


def _sync_graph_from_sqlite(workspace, conn):
    # SQLite nodes/edges → Kuzu 그래프 DB 동기화
    try:
        from cortex.graph_db import GraphDB
        gdb = GraphDB(workspace)
        log.info("Building Kuzu graph from SQLite edges...")
        g_stats = gdb.build_from_sqlite(conn)
        log.info("Kuzu graph built: %d nodes, %d edges, %d errors", g_stats['nodes'], g_stats['edges'], g_stats['errors'])
    except Exception as e:
        log.warning("Kuzu graph build failed: %s", e)


def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 하이브리드 인덱싱 (전체 indexing orchestration 책임을 가진다).

    최적화:
    - 파싱/DB 저장은 파일별로 수행하되, 벡터 임베딩은 전체 완료 후 1회 배치 처리.
    - 모델 로드 1회 / FAISS 읽기·쓰기 1회 / local CUDA fallback 모델 정리.
    """
    # 0. 사전에 Skills 폴더 자동 동기화
    _sync_skills(workspace)

    files = scan_files(workspace, SUPPORTED_EXTENSIONS)
    conn = db.get_connection(workspace)
    db.init_schema(conn)

    # 삭제된 파일 정리
    cleanup_deleted_files(workspace, conn, files)

    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}
    all_vector_items_by_prefix = {}

    # N+1 최적화: file_cache 일괄 로드
    cache_dict = _load_file_cache(conn, force)

    for rel_path in files:
        full_path = os.path.join(workspace, rel_path)
        try:
            source = _read_text_file(full_path)
        except Exception:
            stats["errors"] += 1
            continue

        file_hash = compute_hash(source)
        if not force:
            cached_hash = cache_dict.get(rel_path)
            if cached_hash == file_hash:
                stats["skipped"] += 1
                continue

        # vectorize=False: DB 저장만 수행, vector_items는 여기서 수집
        res = index_file(workspace, rel_path, conn=conn, vectorize=False)
        _collect_index_result(stats, all_vector_items_by_prefix, rel_path, res)

    # 벡터 임베딩 배치 처리 (vectorizer.py 위임)
    use_gpu = detect_gpu()
    if all_vector_items_by_prefix:
        batch_vectorize_nodes(conn, all_vector_items_by_prefix, use_gpu, workspace=workspace)

    # 규칙/프로토콜 동기화
    sync_rules_to_memories(workspace, conn)

    # memories 벡터 인덱싱 (vectorizer.py 위임)
    try:
        batch_vectorize_memories(conn, use_gpu, workspace=workspace)
    except Exception as e:
        log.error("Failed to index memories table: %s", e)

    _release_local_cuda_model_after_indexing()

    # 전체 인덱싱 완료 시각 기록
    conn.execute(
        UPSERT_LAST_INDEXED_AT_SQL,
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()

    # __unresolved__ 엣지를 실제 노드 UUID로 해소
    resolve_unresolved_edges(conn)

    _sync_graph_from_sqlite(workspace, conn)

    conn.close()
    return stats


if __name__ == "__main__":
    import json
    import argparse
    
    parser = argparse.ArgumentParser(description="Cortex Indexer")
    parser.add_argument("workspace", help="Path to workspace")
    parser.add_argument("--file", help="Specific file to index (relative path)")
    parser.add_argument("--force", action="store_true", help="Force re-indexing")
    
    args = parser.parse_args()
    
    if args.file:
        # 단일 파일 모드
        result = index_file(args.workspace, args.file)
        # 단일 파일 처리 후 엣지 해소
        from cortex import db
        conn = db.get_connection(args.workspace)
        resolve_unresolved_edges(conn)
        conn.close()
        print(json.dumps(result, indent=2))
    else:
        # 전체 워크스페이스 모드
        stats = index_workspace(args.workspace, force=args.force)
        print(json.dumps(stats, indent=2))
