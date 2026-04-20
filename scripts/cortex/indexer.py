"""
Cortex 인덱싱 엔진 (v3.1 — Modularized + Logging)
파일 스캔 → 지능형 필터링 → 파서 호출 → DB 저장 → 벡터 임베딩 → 증분 인덱싱

유틸리티: indexer_utils.py
벡터 배치: vectorizer.py
"""
import gc
import os
import sys
import time
import datetime
import hashlib
from pathlib import Path
from cortex.logger import get_logger

log = get_logger("indexer")

# 패키지 내부 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex import db
from cortex.parsers import registry as parser_registry
from cortex.indexer_utils import (
    strip_frontmatter, compute_hash, load_settings,
    get_module_name, scan_files,
)
from cortex.vectorizer import (
    batch_vectorize_nodes, batch_vectorize_memories, detect_gpu,
)

# ==============================================================================
# 설정 및 지원 확장자 (동적 레지스트리 활용)
# ==============================================================================

SUPPORTED_EXTENSIONS = parser_registry.parsers


# ==============================================================================
# 핵심 인덱싱 로직
# ==============================================================================

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
            old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (rel_path,)).fetchall()
            old_ids = [r[0] for r in old_nodes]
            if old_ids:
                chunk_size = 900
                for i in range(0, len(old_ids), chunk_size):
                    chunk = old_ids[i:i + chunk_size]
                    ph = ",".join("?" * len(chunk))
                    conn.execute(f"DELETE FROM edges WHERE source_id IN ({ph})", chunk)
                    conn.execute(f"DELETE FROM edges WHERE target_id IN ({ph})", chunk)
                conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))
            conn.execute("DELETE FROM file_cache WHERE file_path = ?", (rel_path,))
            conn.commit()
            return {"status": "deleted", "reason": f"File removed from DB"}
        except Exception as e:
            return {"error": f"Cleanup failed: {e}"}
        finally:
            if close_conn:
                conn.close()

    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception as e:
        return {"error": str(e)}

    settings = load_settings(workspace)
    workspace_id = hashlib.md5(workspace.encode()).hexdigest()[:8]
    ext = os.path.splitext(rel_path)[1]
    mod_name = get_module_name(rel_path, settings)
    _, parser_func = SUPPORTED_EXTENSIONS.get(ext, (None, None))
    
    if not parser_func:
        return {"status": "skipped", "reason": "unsupported extension"}

    close_conn = False
    if conn is None:
        conn = db.get_connection(workspace)
        close_conn = True

    try:
        result = parser_func(rel_path, source)
        clean_source = strip_frontmatter(source) if rel_path.startswith(".agents/") else source
        
        # 기존 노드/엣지 삭제
        old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (rel_path,)).fetchall()
        old_ids = [r[0] for r in old_nodes]
        if old_ids:
            chunk_size = 900
            for i in range(0, len(old_ids), chunk_size):
                chunk = old_ids[i:i + chunk_size]
                ph = ",".join("?" * len(chunk))
                conn.execute(f"DELETE FROM edges WHERE source_id IN ({ph})", chunk)
                conn.execute(f"DELETE FROM edges WHERE target_id IN ({ph})", chunk)
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))
        # 기존 데이터 존재 여부 확인 (CREATED vs UPDATED 구분용)
        is_update = bool(old_ids)
        
        # 신규 노드 저장
        nodes_data = []
        vector_items = []
        cat = "SKILL" if "skills/" in rel_path else ("RULE" if rel_path.startswith(".agents/") else "SOURCE")
        
        for node in result["nodes"]:
            nodes_data.append((
                node["id"], node["type"], node["name"], node["fqn"],
                node["file_path"], node["start_line"], node["end_line"],
                node.get("signature"), node.get("return_type"), node.get("docstring"),
                node.get("is_exported", 1), node.get("is_async", 0), node.get("is_test", 0),
                node["raw_body"], node.get("skeleton_standard"),
                node.get("skeleton_minimal"), node["language"],
                mod_name, workspace_id, cat
            ))
            
            vec_text = f"{node['type']} {node['fqn']}\n"
            if node.get('signature'): vec_text += f"Sig: {node['signature']}\n"
            if cat == "RULE":
                vec_text += clean_source[:1200]
            else:
                vec_text += node['raw_body'][:1200]
            
            vector_items.append({
                "id": node["id"], "text": vec_text,
                "meta": {"module": mod_name, "file": rel_path, "type": node["type"], "category": cat}
            })

        if nodes_data:
            conn.executemany("""
                INSERT OR REPLACE INTO nodes
                (id, type, name, fqn, file_path, start_line, end_line,
                 signature, return_type, docstring, is_exported, is_async,
                 is_test, raw_body, skeleton_standard, skeleton_minimal, language,
                 module, workspace_id, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, nodes_data)

        if edges_data := [(e["source_id"], e["target_id"], e.get("type", "CALLS")) for e in result["edges"]]:
            conn.executemany("INSERT OR IGNORE INTO edges (source_id, target_id, type) VALUES (?, ?, ?)", edges_data)

        conn.execute("INSERT OR REPLACE INTO file_cache (file_path, hash, last_indexed_at, workspace_id) VALUES (?, ?, ?, ?)",
                     (rel_path, compute_hash(source), int(time.time()), workspace_id))
        
        if vectorize and vector_items:
            from cortex import vector_engine as ve
            ids = [item["id"] for item in vector_items]
            ph = ",".join("?" * len(ids))
            rowids_query = conn.execute(f"SELECT id, rowid FROM nodes WHERE id IN ({ph})", ids).fetchall()
            id_to_rowid = {r[0]: r[1] for r in rowids_query}
            texts = [b["text"] for b in vector_items]
            embeddings = ve.get_embeddings(texts, use_gpu=use_gpu)
            vec_data = []
            for b, emb in zip(vector_items, embeddings):
                rowid = id_to_rowid.get(b["id"])
                if rowid is not None:
                    vec_data.append((rowid, emb.tobytes()))
            if vec_data:
                conn.executemany("INSERT OR REPLACE INTO vec_nodes (rowid, embedding) VALUES (?, ?)", vec_data)
                conn.commit()
            
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
            # 간단한 Calls 관계 추가
            if result.get("edges"):
                for e in result["edges"]:
                    gdb.execute("MATCH (a {fqn: $src}), (b {fqn: $tgt}) MERGE (a)-[:Calls]->(b)", 
                        {"src": e["source_id"], "tgt": e["target_id"]})
        except Exception as e:
            pass

        conn.commit()

        result = {"status": "updated" if is_update else "created", "nodes": len(nodes_data)}
        if not vectorize:
            # 배치 모드: 호출자가 일괄 처리하도록 vector_items 반환
            result["_vector_items"] = vector_items
        return result
    finally:
        if close_conn:
            conn.close()


def _sync_rules_to_memories(workspace: str, conn):
    """규칙/프로토콜 .md 문서를 memories 테이블에 동기화.
    
    에이전트가 pc_capsule로 규칙을 발견할 수 있도록
    .agents/rules/*.md → category='rule'
    .agents/protocols/*.md → category='protocol'
    형태로 memories 테이블에 저장한다.
    """
    import json
    
    rule_dirs = {
        "rule": os.path.join(workspace, ".agents", "rules"),
        "protocol": os.path.join(workspace, ".agents", "rules", "protocols"),
        "resource": os.path.join(workspace, ".agents", "knowledge", "resources"),
        "example": os.path.join(workspace, ".agents", "knowledge", "examples"),
    }
    
    synced = 0
    from tqdm import tqdm
    for category, dir_path in rule_dirs.items():
        if not os.path.isdir(dir_path):
            continue
        
        md_files = list(Path(dir_path).rglob("*.md"))
        for md_path in tqdm(md_files, desc=f"Syncing {category}", unit="file"):
            full_path = str(md_path)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            
            key = f"{category}::{md_path.stem}"
            content_clean = strip_frontmatter(content).strip()
            if not content_clean:
                continue
            
            # 해시 기반 변경 감지 (불필요한 업데이트 방지)
            content_hash = compute_hash(content_clean)
            existing = conn.execute(
                "SELECT content FROM memories WHERE key = ?", (key,)
            ).fetchone()
            
            if existing and compute_hash(existing[0]) == content_hash:
                continue  # 변경 없음 — 스킵
            
            now = int(time.time())
            tags_json = json.dumps([category, "agent-rule"], ensure_ascii=False)
            rel_json = json.dumps({}, ensure_ascii=False)
            
            # 제목 추출 (첫 번째 # 헤딩 또는 파일명)
            title = md_path.stem
            for line in content_clean.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            
            # memories 테이블에 upsert
            prefixed_content = f"[{category.upper()}] {title}\n{content_clean}"
            conn.execute(
                """INSERT INTO memories (key, project_id, category, content, tags, relationships, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                   content=excluded.content, category=excluded.category,
                   tags=excluded.tags, updated_at=excluded.updated_at""",
                (key, ".", category, prefixed_content, tags_json, rel_json, now, now)
            )
            synced += 1
    
    if synced > 0:
        conn.commit()
        log.info("Synced %d rule/protocol docs to memories table.", synced)


def _cleanup_deleted_files(workspace: str, conn, current_files: list):
    """DB에는 등록되어 있으나 현재 디스크에는 없는 파일을 찾아 제거"""
    cached_files = conn.execute("SELECT file_path FROM file_cache").fetchall()
    db_file_list = [row[0] for row in cached_files]
    current_file_set = set(current_files)
    
    deleted_files = [f for f in db_file_list if f not in current_file_set]
    if not deleted_files:
        return
    
    log.info("Found %d deleted files. Cleaning up DB...", len(deleted_files))
    
    for del_path in deleted_files:
        old_nodes = conn.execute("SELECT id FROM nodes WHERE file_path = ?", (del_path,)).fetchall()
        old_ids = [r[0] for r in old_nodes]
        
        if old_ids:
            chunk_size = 900
            for i in range(0, len(old_ids), chunk_size):
                chunk = old_ids[i:i + chunk_size]
                ph = ",".join("?" * len(chunk))
                conn.execute(f"DELETE FROM edges WHERE source_id IN ({ph})", chunk)
                conn.execute(f"DELETE FROM edges WHERE target_id IN ({ph})", chunk)
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (del_path,))
        
        conn.execute("DELETE FROM file_cache WHERE file_path = ?", (del_path,))
    
    conn.commit()
    log.info("Cleanup complete for %d files.", len(deleted_files))


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
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_indexed_at'").fetchone()
    if not row:
        conn.close()
        return {"status": "skip", "reason": "no previous index"}
    
    last_indexed = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").timestamp()
    
    # 프로젝트 전체 파일을 스캔하여 포함 대상 리스트업
    from cortex.indexer_utils import scan_files
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
    _cleanup_deleted_files(workspace, conn, all_files)
    
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
    _sync_rules_to_memories(workspace, conn)
    
    # CPU 전용 벡터 임베딩 (소량이므로 GPU 불필요)
    if vector_items:
        from cortex.vectorizer import batch_vectorize_nodes
        batch_vectorize_nodes(conn, {"opportunistic": vector_items}, use_gpu=False, workspace=workspace)
    
    from cortex.vectorizer import batch_vectorize_memories
    try:
        batch_vectorize_memories(conn, use_gpu=False, workspace=workspace)
    except Exception:
        pass
    
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_at', ?)",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    conn.close()
    
    log.info("Opportunistic indexing complete: %d files indexed (CPU).", indexed)
    return {"status": "indexed", "changed": len(changed_files), "indexed": indexed}

def index_workspace(workspace: str, force: bool = False) -> dict:
    """전체 워크스페이스 하이브리드 인덱싱.

    최적화:
    - 파싱/DB 저장은 파일별로 수행하되, 벡터 임베딩은 전체 완료 후 1회 배치 처리.
    - 모델 로드 1회 / FAISS 읽기·쓰기 1회 / GPU 해제 1회.
    """
    # 0. 사전에 Skills 폴더 자동 동기화
    from cortex.skill_manager import SkillManager
    log.info("Auto-syncing skills to memories DB...")
    try:
        sm = SkillManager(workspace)
        sm.sync_skills(workspace)
    except Exception as e:
        log.warning("Skill sync failed: %s", e)

    files = scan_files(workspace, SUPPORTED_EXTENSIONS)
    conn = db.get_connection(workspace)
    db.init_schema(conn)

    # 삭제된 파일 정리
    _cleanup_deleted_files(workspace, conn, files)

    stats = {"total_files": len(files), "indexed": 0, "skipped": 0, "errors": 0}
    all_vector_items_by_prefix = {}

    # N+1 최적화: file_cache 일괄 로드
    cache_dict = {}
    if not force:
        cached_rows = conn.execute("SELECT file_path, hash FROM file_cache").fetchall()
        cache_dict = {row[0]: row[1] for row in cached_rows}

    for rel_path in files:
        full_path = os.path.join(workspace, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
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
        if "error" in res:
            stats["errors"] += 1
        else:
            stats["indexed"] += 1
            
            # 소속 폴더 분석 (최상단 폴더 기준)
            parts = Path(rel_path).parts
            prefix = "root"
            if len(parts) > 1 and not parts[0].startswith("."):
                prefix = parts[0]
            
            if prefix not in all_vector_items_by_prefix:
                all_vector_items_by_prefix[prefix] = []
            all_vector_items_by_prefix[prefix].extend(res.get("_vector_items", []))

    # 벡터 임베딩 배치 처리 (vectorizer.py 위임)
    use_gpu = detect_gpu()
    if all_vector_items_by_prefix:
        batch_vectorize_nodes(conn, all_vector_items_by_prefix, use_gpu, workspace=workspace)

    # 규칙/프로토콜 동기화
    _sync_rules_to_memories(workspace, conn)

    # memories 벡터 인덱싱 (vectorizer.py 위임)
    try:
        batch_vectorize_memories(conn, use_gpu, workspace=workspace)
    except Exception as e:
        log.error("Failed to index memories table: %s", e)

    # GPU VRAM 해제 (팬 소음 방지 — 다음 소량 인덱싱은 CPU로 즉시 처리)
    if use_gpu:
        try:
            from cortex.vector_engine import release_gpu
            release_gpu()
            log.info("GPU VRAM released after full indexing.")
        except Exception:
            pass

    # 전체 인덱싱 완료 시각 기록
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_indexed_at', ?)", (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()

    # SQLite nodes/edges → Kuzu 그래프 DB 동기화
    try:
        from cortex.graph_db import GraphDB
        gdb = GraphDB(workspace)
        log.info("Building Kuzu graph from SQLite edges...")
        g_stats = gdb.build_from_sqlite(conn)
        log.info("Kuzu graph built: %d nodes, %d edges, %d errors", g_stats['nodes'], g_stats['edges'], g_stats['errors'])
    except Exception as e:
        log.warning("Kuzu graph build failed: %s", e)

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
        print(json.dumps(result, indent=2))
    else:
        # 전체 워크스페이스 모드
        stats = index_workspace(args.workspace, force=args.force)
        print(json.dumps(stats, indent=2))
