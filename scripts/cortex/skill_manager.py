"""
스킬 자동 탐색 및 인덱싱 관리자 (SkillManager)
- {workspace}/skills/**/ 내 SKILL.md 파일을 탐색
- memories 테이블에 스킬을 카탈로깅하여 FTS5 기반 검색 지원
- 로컈 BGE-M3 모델(1순위)와 외부 API(2순위, 선택사항)를 통한 하이브리드 검색 지원
"""
import os
import re
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

# 임베딩 모드: local (BAAI/bge-m3, 기본값) 또는 api (GPU/CPU 부족 환경용 API 폴백)
# .env의 CORTEX_EMBEDDING_MODE 변수로 재정의 가능
EMBEDDING_MODE = os.getenv("CORTEX_EMBEDDING_MODE", "local")
EMBEDDING_BATCH_SIZE = 32 if EMBEDDING_MODE == "local" else 50
# API 모드 시 호출할 sentence-transformers 호환 API URL (선택사항)
EMBEDDING_API_MODEL = os.getenv("CORTEX_EMBEDDING_API_MODEL", "text-embedding-3-small")

class EmbeddingEngine:
    """임베딩 엔진 (GPU/CPU 부족 환경용 API 폴백 지원)
    - mode='local': BAAI/bge-m3 로컈 모델 사용 (기본값, VRAM ~1.5GB)
    - mode='api':   CORTEX_EMBEDDING_API_KEY로 식별 가능한 OpenAI-호환 API 사용
    """
    def __init__(self, mode="local"):
        self.mode = mode
        self.api_client = None
        self.local_model = None

        if self.mode == "local":
            try:
                import torch
                from sentence_transformers import SentenceTransformer
                
                # 사용 환경 하드웨어 감지 (GPU/CPU)
                device = "cuda" if torch.cuda.is_available() else "cpu"
                
                # 고성능 로컬 임베딩 엔진 탑재 (VRAM 최적화)
                target_model = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-m3")
                self.local_model = SentenceTransformer(target_model, device=device)
                logger.info(f"로컈 임베딩 모델({target_model})이 [{device.upper()}]에 로드되었습니다.")
            except ImportError:
                logger.error("sentence-transformers 또는 torch 패키지를 찾을 수 없습니다.")
            except Exception as e:
                logger.error(f"로컈 모델 로드 실패: {e}")
        elif self.mode == "api":
            self.api_client = self._setup_api_client()

    def _get_api_key(self) -> str:
        key = os.getenv("CORTEX_EMBEDDING_API_KEY", "")
        return key.strip('"').strip("'")

    def _setup_api_client(self):
        """OpenAI SDK 호환 임베딩 API 클라이언트 초기화.
        OpenAI, Cohere, 또는 openai-compatible 서트잌으로 동작합니다."""
        api_key = self._get_api_key()
        if not api_key:
            logger.warning("CORTEX_EMBEDDING_API_KEY가 설정되지 않았습니다. API 모드를 사용할 수 없습니다.")
            return None
        try:
            from openai import OpenAI
            base_url = os.getenv("CORTEX_EMBEDDING_API_URL")  # None 시 OpenAI 기본값
            return OpenAI(api_key=api_key, base_url=base_url)
        except ImportError:
            logger.warning("`openai` 패키지가 없습니다. `pip install openai`로 설치하세요.")
            return None

    def embed_batch(self, texts: list[str], is_query=False) -> list[bytes | None]:
        if not texts: return []
        import numpy as np
        
        if self.mode == "local" and self.local_model:
            try:
                # normalize_embeddings=True 로 설정해 코사인 유사성 직접 연산 최적화
                embeddings = self.local_model.encode(texts, normalize_embeddings=True)
                return [np.array(e, dtype=np.float32).tobytes() for e in embeddings]
            except Exception as e:
                logger.warning(f"로컈 임베딩 오류: {e}")
                return [None] * len(texts)

        elif self.mode == "api" and self.api_client:
            try:
                response = self.api_client.embeddings.create(
                    input=texts,
                    model=EMBEDDING_API_MODEL,
                )
                return [
                    (np.array(item.embedding, dtype=np.float32).tobytes() if item.embedding else None)
                    for item in response.data
                ]
            except Exception as e:
                logger.warning(f"Embedding API 오류: {e}")
                return [None] * len(texts)
                
        return [None] * len(texts)


def _cosine_similarity(blob_a: bytes, blob_b: bytes) -> float:
    try:
        import numpy as np
        a = np.frombuffer(blob_a, dtype=np.float32)
        b = np.frombuffer(blob_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0: return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
    except Exception:
        return 0.0

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
        name = h1.group(1).strip() if h1 else Path(skill_md_path).parent.name

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
        self.workspace = workspace
        # DB 연결 시 SQLite Lock 방지를 위해 타임아웃 지정
        import sqlite3
        db_path = os.path.join(workspace, ".agents/cortex_data/index.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            init_schema(conn)
        finally:
            conn.close()

    def sync_skills(self, project_id: str) -> dict:
        skills_root = os.path.join(self.workspace, "skills")
        if not os.path.isdir(skills_root):
            return {"error": f"Skills root not found: {skills_root}"}

        import sqlite3
        db_path = os.path.join(self.workspace, ".agents/cortex_data/index.db")
        conn = sqlite3.connect(db_path, timeout=60.0)
        
        skill_files = list(Path(skills_root).rglob("SKILL.md"))
        synced, skipped, errors, pending_embed = 0, 0, [], []

        try:
            now = int(time.time())

            # 1단계: FTS5 인덱싱
            for skill_path in skill_files:
                try:
                    info = _parse_skill_md(str(skill_path))
                    if not info:
                        skipped += 1
                        continue

                    skill_key = f"skill::{skill_path.parent.name}"
                    rel_path = to_rel_path(str(skill_path), self.workspace)
                    content = f"[SKILL] {info['name']}\n설명: {info['description']}\n경로: {rel_path}\n\n{info['content_preview']}"
                    tags_json = json.dumps(info.get("tags", ["skill"]), ensure_ascii=False)

                    existing = conn.execute("SELECT embedding FROM memories WHERE key = ?", (skill_key,)).fetchone()
                    if existing:
                        conn.execute("UPDATE memories SET content=?, tags=?, updated_at=? WHERE key=?", (content, tags_json, now, skill_key))
                        if not existing[0]:
                            pending_embed.append((skill_key, f"{info['name']} {info['description']}"))
                    else:
                        conn.execute("INSERT INTO memories (key, project_id, category, content, tags, created_at, updated_at) VALUES (?, ?, 'skill', ?, ?, ?, ?)",
                                     (skill_key, project_id, content, tags_json, now, now))
                        pending_embed.append((skill_key, f"{info['name']} {info['description']}"))
                    synced += 1
                except Exception as e:
                    errors.append(f"{skill_path}: {e}")
            conn.commit()

            # 2단계: FAISS 벡터 인덱싱 (전체 본문 청킹 방식)
            import sys
            vector_items = []
            for skill_path in skill_files:
                try:
                    info = _parse_skill_md(str(skill_path))
                    if not info:
                        continue
                    skill_key = f"skill::{skill_path.parent.name}"
                    # 제목 + 태그 + 전체 본문을 합쳐 청킹
                    full_text = (
                        f"[SKILL] {info['name']}\n"
                        f"Tags: {', '.join(info.get('tags', []))}\n\n"
                        f"{info.get('full_content', info['content_preview'])}"
                    )
                    vector_items.append({
                        "id": skill_key,
                        "text": full_text,
                        "meta": {"name": info["name"], "tags": info.get("tags", [])},
                    })
                except Exception:
                    pass

            if vector_items:
                try:
                    import torch
                    use_gpu = torch.cuda.is_available()
                except ImportError:
                    use_gpu = False

                sys.stderr.write(f"[skill_manager] Vectorizing {len(vector_items)} skills (GPU={use_gpu})...\n")
                v_result = ve.index_texts(self.workspace, vector_items, use_gpu=use_gpu)
                sys.stderr.write(f"[skill_manager] Vector indexing done: {v_result}\n")
                embed_done = v_result.get("indexed", 0)
            else:
                embed_done = 0

        except Exception as e:
            errors.append(f"동기화 중 에러: {e}")
        finally:
            conn.close()

        return {"synced": synced, "skipped": skipped, "errors": errors, "embedded": embed_done}

    def search_skills(self, project_id: str, query: str, limit: int = 5) -> list:
        import sqlite3
        db_path = os.path.join(self.workspace, ".agents/cortex_data/index.db")
        conn = sqlite3.connect(db_path, timeout=60.0)
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

            col_names = [d[0] for d in conn.execute("SELECT * FROM memories LIMIT 1").description]
            fts_scored, fts_data = {}, {}
            for r, row in enumerate(fts_rows):
                d = dict(zip(col_names, row))
                fts_scored[d["key"]] = 1.0 / (r + 60)  # RRF 점수
                fts_data[d["key"]] = d

            # 2. FAISS 벡터 검색 (CPU, VRAM 0MB)
            sem_scored = {}
            try:
                vec_results = ve.search_similar(self.workspace, query, top_k=limit, use_gpu=False)
                for r, vr in enumerate(vec_results):
                    sem_scored[vr["id"]] = 1.0 / (r + 60)  # RRF 점수
                    # FTS에 없는 항목은 DB에서 보완
                    if vr["id"] not in fts_data:
                        db_row = conn.execute(
                            "SELECT * FROM memories WHERE key = ?", (vr["id"],)
                        ).fetchone()
                        if db_row:
                            fts_data[vr["id"]] = dict(zip(col_names, db_row))
            except Exception as ve_err:
                import sys
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
