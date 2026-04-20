"""
스킬 자동 탐색 및 인덱싱 관리자 (SkillManager)
- {workspace}/skills/**/ 내 SKILL.md 파일을 탐색
- memories 테이블에 스킬을 카탈로깅하여 FTS5 기반 검색 지원
- 로컬 Qwen3 모델(1순위)과 외부 API(2순위, 선택사항)를 통한 하이브리드 검색 지원
"""
import gc
import os
import re
import sys
import time
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# .agents/.env 로딩 시도
env_path = Path(__file__).parent.parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from cortex.db import init_schema, to_rel_path
from cortex import vector_engine as ve

logger = logging.getLogger(__name__)

# 임베딩 모드: local (Qwen3, 기본값) 또는 api (GPU/CPU 부족 환경용 API 폴백)
# .env의 CORTEX_EMBEDDING_MODE 변수로 재정의 가능
EMBEDDING_MODE = os.getenv("CORTEX_EMBEDDING_MODE", "local")
EMBEDDING_BATCH_SIZE = 16 if EMBEDDING_MODE == "local" else 50
# API 모드 시 호출할 sentence-transformers 호환 API 모델 (선택사항)
EMBEDDING_API_MODEL = os.getenv("CORTEX_EMBEDDING_API_MODEL", "text-embedding-3-small")

def _parse_skill_md(skill_md_path: str) -> dict:
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {}

    name, description, tags = "", "", []
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        n_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        d_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        t_m = re.search(r"^tags:\s*(?:\[)?([^\]\n]*)(?:\])?", fm, re.MULTILINE)
        name = n_m.group(1).strip().strip('"') if n_m else ""
        description = d_m.group(1).strip().strip('"') if d_m else ""
        if t_m: tags = [t.strip().strip('"').strip("'") for t in t_m.group(1).split(",") if t.strip()]

    if not name:
        h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        name = h1.group(1).strip() if h1 else Path(skill_md_path).stem

    if not description:
        paragraphs = re.findall(r"(?m)^(?!#|---|```|\s*$)(.+)$", content)
        description = " ".join(paragraphs[:3]).strip()[:500]

    if not tags:
        text = (name + " " + description).lower()
        extracted = []
        for k in ["python", "mcp", "agent", "test"]:
            if k in text: extracted.append(k)
        tags = extracted if extracted else ["skill"]
    
    return {
        "name": name, "description": description, "path": skill_md_path,
        "tags": tags,
        "content_preview": content[:2000],
        "full_content": content,  # 전체 본문 (벡터 청킹용)
    }

class SkillManager:
    def __init__(self, workspace: str):
        from cortex.db import get_connection, init_schema
        self.workspace = workspace
        conn = get_connection(workspace)
        try:
            init_schema(conn)
        finally:
            conn.close()

    def sync_skills(self, project_id: str) -> dict:
        skills_dirs = [
            os.path.join(self.workspace, ".agents", "skills"),
            os.path.join(self.workspace, ".agents", "knowledge", "skills")
        ]

        from cortex.db import get_connection
        conn = get_connection(self.workspace)

        skill_files = []
        for d in skills_dirs:
            if os.path.isdir(d):
                skill_files.extend(list(Path(d).rglob("*.md")))
                
        synced, skipped, errors, pending_embed, embed_done = 0, 0, [], [], 0

        try:
            now = int(time.time())

            # 1단계: FTS5 인덱싱 (N+1 Query 최적화)
            skill_info_map = {}
            for skill_path in skill_files:
                try:
                    info_raw = _parse_skill_md(str(skill_path))
                    if not info_raw:
                        skipped += 1
                        continue

                    # info가 dict인지 tuple인지에 따라 안전하게 데이터 추출
                    if isinstance(info_raw, dict):
                        i_name = info_raw.get("name", skill_path.stem)
                        i_desc = info_raw.get("description", "")
                        i_tags = info_raw.get("tags", ["skill"])
                        i_preview = info_raw.get("content_preview", "")
                    else:
                        i_name = info_raw[0] if len(info_raw) > 0 else skill_path.stem
                        i_desc = info_raw[1] if len(info_raw) > 1 else ""
                        i_tags = info_raw[3] if len(info_raw) > 3 else ["skill"]
                        i_preview = info_raw[4] if len(info_raw) > 4 else ""

                    # skill.md 또는 SKILL.md 등 대소문자 무관하게 처리
                    if skill_path.name.lower() == "skill.md":
                        skill_key = f"skill::{skill_path.parent.name}"
                    else:
                        skill_key = f"skill::{skill_path.stem}"

                    rel_path = to_rel_path(str(skill_path), self.workspace)
                    content = f"[SKILL] {i_name}\n설명: {i_desc}\n경로: {rel_path}\n\n{i_preview}"
                    tags_json = json.dumps(i_tags, ensure_ascii=False)

                    skill_info_map[skill_key] = {
                        "key": skill_key,
                        "name": i_name,
                        "description": i_desc,
                        "content": content,
                        "tags_json": tags_json,
                        "path": skill_path
                    }
                except Exception as e:
                    errors.append(f"{skill_path}: {e}")

            if skill_info_map:
                # 기존 항목 대량 조회 (Batch SELECT) — vec 존재 여부도 함께 확인
                existing_map = {}  # key -> has_vec (bool)
                keys = list(skill_info_map.keys())
                chunk_size = 900
                for i in range(0, len(keys), chunk_size):
                    chunk = keys[i:i + chunk_size]
                    placeholders = ",".join(["?"] * len(chunk))
                    rows = conn.execute(
                        f"SELECT m.key, (v.rowid IS NOT NULL) as has_vec "
                        f"FROM memories m "
                        f"LEFT JOIN vec_memories v ON v.rowid = m.rowid "
                        f"WHERE m.key IN ({placeholders})",
                        chunk
                    ).fetchall()
                    for r in rows:
                        row_dict = dict(r)
                        k = row_dict.get("key")
                        if k:
                            existing_map[k] = bool(row_dict.get("has_vec", 0))

                to_insert = []
                to_update = []
                for si in skill_info_map.values():
                    if isinstance(si, dict):
                        skill_key = si.get("key")
                        content = si.get("content")
                        tags_json = si.get("tags_json")
                        s_name = si.get("name", "")
                        s_desc = si.get("description", "")
                    else:
                        skill_key = si[0] if len(si) > 0 else ""
                        content = si[3] if len(si) > 3 else ""
                        tags_json = si[4] if len(si) > 4 else "[]"
                        s_name = si[1] if len(si) > 1 else ""
                        s_desc = si[2] if len(si) > 2 else ""

                    if not skill_key: continue

                    if skill_key in existing_map:
                        to_update.append((content, tags_json, now, skill_key))
                        # Fix #2: 벡터가 없거나 내용이 변경되었으므로 항상 re-embed
                        pending_embed.append({"id": skill_key, "text": f"{s_name} {s_desc}"})
                    else:
                        to_insert.append((skill_key, project_id, content, tags_json, now, now))
                        pending_embed.append({"id": skill_key, "text": f"{s_name} {s_desc}"})
                    synced += 1

                if to_insert:
                    conn.executemany(
                        "INSERT INTO memories (key, project_id, category, content, tags, created_at, updated_at) VALUES (?, ?, 'skill', ?, ?, ?, ?)",
                        to_insert
                    )
                if to_update:
                    conn.executemany(
                        "UPDATE memories SET content=?, tags=?, updated_at=? WHERE key=?",
                        to_update
                    )
                conn.commit()

            # 2단계: sqlite-vec 벡터 인덱싱 (전체 본문 청킹 방식)
            vector_items = []
            for skill_path in skill_files:
                try:
                    info_raw = _parse_skill_md(str(skill_path))
                    if not info_raw:
                        continue
                    
                    if isinstance(info_raw, dict):
                        i_name = info_raw.get("name", skill_path.stem)
                        i_desc = info_raw.get("description", "")
                        i_tags = info_raw.get("tags", [])
                    else:
                        i_name = info_raw[0] if len(info_raw) > 0 else skill_path.stem
                        i_desc = info_raw[1] if len(info_raw) > 1 else ""
                        i_tags = info_raw[3] if len(info_raw) > 3 else []

                    if skill_path.name.lower() == "skill.md":
                        skill_key = f"skill::{skill_path.parent.name}"
                    else:
                        skill_key = f"skill::{skill_path.stem}"

                    summary_text = f"{i_name} {i_desc}"
                    vector_items.append({
                        "id": skill_key,
                        "text": summary_text,
                        "meta": {"name": i_name, "tags": i_tags},
                    })
                except Exception:
                    pass

            if vector_items:
                # Fix #2: Step 1에서 변경/신규로 수집된 ID만 필터링 (효율성 복구)
                pending_ids = {item["id"] for item in pending_embed}
                vector_items = [item for item in vector_items if item["id"] in pending_ids]

            if vector_items:
                try:
                    import torch
                    use_gpu = torch.cuda.is_available()
                except ImportError:
                    use_gpu = False

                sys.stderr.write(f"[skill_manager] Vectorizing {len(vector_items)} skills (GPU={use_gpu})...\n")
                
                # Batch processing to handle large amounts of items efficiently
                from cortex.db import get_connection as _gc
                vec_conn = _gc(self.workspace)
                from tqdm import tqdm
                try:
                    from cortex.indexer_utils import get_tuning_params
                    tuning = get_tuning_params()
                    batch_size = tuning.get("batch_size", 16)
                    for i in tqdm(range(0, len(vector_items), batch_size), desc="Skills Embedding", unit="batch"):
                        batch = vector_items[i:i + batch_size]
                        texts = [item["text"] for item in batch]
                        embeddings = ve.get_embeddings(texts, use_gpu=use_gpu)
                        
                        for item, emb in zip(batch, embeddings):
                            rowid_cur = vec_conn.execute(
                                "SELECT rowid FROM memories WHERE key = ?", (item["id"],)
                            ).fetchone()
                            if rowid_cur:
                                try:
                                    vec_conn.execute(
                                        "DELETE FROM vec_memories WHERE rowid = ?",
                                        (rowid_cur[0],)
                                    )
                                except Exception:
                                    pass
                                vec_conn.execute(
                                    "INSERT INTO vec_memories(rowid, embedding) VALUES (?, ?)",
                                    (rowid_cur[0], emb.tobytes())
                                )
                        vec_conn.commit()
                        embed_done += len(batch)
                        if use_gpu:
                            torch.cuda.empty_cache()
                        gc.collect()
                    sys.stderr.write(f"[skill_manager] Vector indexing done: {embed_done} skills embedded.\n")
                finally:
                    vec_conn.close()

            else:
                embed_done = 0

        except Exception as e:
            errors.append(f"동기화 중 에러: {e}")
        finally:
            conn.close()

        return {"synced": synced, "skipped": skipped, "errors": errors, "embedded": embed_done}

    def search_skills(self, project_id: str, query: str, limit: int = 5) -> list:
        from cortex.db import get_connection
        conn = get_connection(self.workspace)
        try:
            # 1. FTS5 키워드 검색
            tokens = [f'"{t}"*' for t in re.split(r'[\s\-_.,/]+', query) if len(t) >= 2]
            fts_query = " OR ".join(tokens) if tokens else "*"
            try:
                fts_rows = conn.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid = f.rowid "
                    "WHERE memories_fts MATCH ? AND m.category = 'skill' ORDER BY rank LIMIT ?",
                    (fts_query, limit)
                ).fetchall()
            except Exception:
                fts_rows = []

            fts_scored, fts_data = {}, {}
            for r, row in enumerate(fts_rows):
                d = dict(row)
                fts_scored[d["key"]] = 1.0 / (r + 60)  # RRF score
                fts_data[d["key"]] = d

            # 2. 벡터 검색 (sqlite-vec 기반)
            sem_scored = {}
            try:
                query_vec = ve.get_embeddings([query])[0]
                vec_rows = conn.execute(
                    "SELECT m.key, m.content, m.tags "
                    "FROM vec_memories v "
                    "JOIN memories m ON m.rowid = v.rowid "
                    "WHERE v.embedding MATCH ? AND k = ?",
                    (query_vec.tobytes(), limit * 2)
                ).fetchall()
                
                vec_results = []
                for r in vec_rows:
                    row_dict = dict(r)
                    vec_results.append({
                        "id": row_dict["key"],
                        "text": row_dict["content"],
                        "meta": {"tags": json.loads(row_dict["tags"] or "[]")}
                    })
                missing_keys = []
                for r, vr_raw in enumerate(vec_results):
                    vr = dict(vr_raw) if not isinstance(vr_raw, dict) else vr_raw
                    item_id = vr.get("id")
                    if not item_id: continue
                    
                    sem_scored[item_id] = 1.0 / (r + 60)  # RRF score
                    if item_id not in fts_data:
                        missing_keys.append(item_id)

                if missing_keys:
                    chunk_size = 900
                    for i in range(0, len(missing_keys), chunk_size):
                        chunk = missing_keys[i:i + chunk_size]
                        placeholders = ",".join(["?"] * len(chunk))
                        query_sql = f"SELECT * FROM memories WHERE key IN ({placeholders})"
                        db_rows = conn.execute(query_sql, chunk).fetchall()
                        for db_row in db_rows:
                            d = dict(db_row)
                            fts_data[d["key"]] = d
            except Exception as ve_err:
                sys.stderr.write(f"[skill_manager] Vector search skipped: {ve_err}\n")

            # 3. RRF 점수 합산 후 정렬
            all_keys = sorted(
                set(fts_scored) | set(sem_scored),
                key=lambda k: fts_scored.get(k, 0.0) + sem_scored.get(k, 0.0),
                reverse=True
            )[:limit]

            return [{
                "key": k,
                "name": k.replace("skill::", ""),
                "summary": fts_data[k].get("content", "")[:400] if k in fts_data else "",
                "tags": json.loads(fts_data[k].get("tags") or "[]") if k in fts_data else [],
                "score": round(fts_scored.get(k, 0.0) + sem_scored.get(k, 0.0), 6),
            } for k in all_keys if k in fts_data]
        finally:
            conn.close()
