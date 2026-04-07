"""
Cortex 벡터 검색 엔진 (Vector Engine)
- 모델: Qwen/Qwen3-Embedding-0.6B (최신 SOTA, 다국어 지원)
- 인덱싱: GPU(CUDA) 가속 → 빠른 대량 임베딩
- 검색: CPU 모드 → VRAM 0MB 점유
- 저장소: FAISS (로컬 파일 기반, cortex_data/vectors.index)
"""
import os
import json
from dotenv import load_dotenv

# .env 로드 (스크립트 위치 기준 동적 해석)
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
load_dotenv(ENV_PATH)

# 청킹 설정
CHUNK_SIZE = 1500         # Qwen3의 긴 컨텍스트(32k)를 고려하여 소폭 확대
CHUNK_OVERLAP = 200
DEFAULT_TOP_K = 5

# 모델 식별자
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

# 전역 상태 (지연 초기화)
_model = None
_model_device = None


def _get_data_dir(workspace: str) -> str:
    """cortex_data 폴더 경로 반환 (없으면 생성)"""
    # .agents 폴더 내부로 경로 고정
    if workspace.endswith(".agents"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".agents")
        
    data_dir = os.path.join(base_dir, "cortex_data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def _load_model(device: str = "cpu"):
    """Qwen3 모델 지연 로딩 (device: 'cpu' 또는 'cuda', FP16 최적화)"""
    global _model, _model_device
    if _model is not None and _model_device == device:
        return _model

    try:
        from sentence_transformers import SentenceTransformer
        import torch
        import sys
        
        # 허깅페이스 토큰 로드 확인
        hf_token = os.getenv("HF_TOKEN")
        
        sys.stderr.write(f"[cortex-vector] Loading Qwen3 on {device} (FP16 Mode)...\n")
        
        # 모델 로딩 옵션 (VRAM 최적화: FP16 강제)
        model_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16 if device == "cuda" else torch.float32,
        }
        
        _model = SentenceTransformer(
            MODEL_ID, 
            device=device, 
            model_kwargs=model_kwargs,
            token=hf_token
        )
        
        # 확실하게 FP16으로 변환 (CUDA인 경우)
        if device == "cuda":
            _model.half()

        _model_device = device
        # 실제 장치 확인 로그
        actual_dev = next(_model.parameters()).device
        sys.stderr.write(f"[cortex-vector] Qwen3 successfully loaded on {actual_dev}.\n")
    except Exception as e:
        import sys
        sys.stderr.write(f"[cortex-vector] Model Load Error: {e}\n")
        raise RuntimeError(f"Qwen3 모델 로딩 실패: {e}")

    return _model


def _release_gpu():
    """인덱싱 완료 후 GPU 메모리 해제"""
    global _model, _model_device
    if _model_device == "cuda" and _model is not None:
        try:
            import torch
            _model = None
            _model_device = None
            torch.cuda.empty_cache()
            import sys
            sys.stderr.write("[cortex-vector] GPU memory released.\n")
        except Exception:
            pass


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    텍스트를 의미 단위로 분할 (청킹)
    - 단락(\\n\\n) 경계를 우선, 불가 시 문자 수 기준 분할
    """
    if not text or len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    chunks = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # 단락 자체가 너무 길면 강제 분할
            while len(para) > chunk_size:
                chunks.append(para[:chunk_size])
                para = para[chunk_size - overlap:]
            current = para

    if current:
        chunks.append(current)

    return [c for c in chunks if c.strip()]


# ============================================================
# FAISS 인덱스 관리
# ============================================================

def _index_path(workspace: str, prefix: str = "vectors") -> str:
    return os.path.join(_get_data_dir(workspace), f"{prefix}.index")


def _meta_path(workspace: str, prefix: str = "vectors") -> str:
    return os.path.join(_get_data_dir(workspace), f"{prefix}_meta.json")


def _load_faiss_index(workspace: str, prefix: str = "vectors"):
    """FAISS 인덱스 및 메타 로드 (없으면 None 반환)"""
    try:
        import faiss
        import sys
        idx_path = _index_path(workspace, prefix)
        meta_path = _meta_path(workspace, prefix)

        # pkl → json 자동 마이그레이션
        old_meta_path = os.path.join(_get_data_dir(workspace), "vectors_meta.pkl")
        if os.path.exists(old_meta_path) and not os.path.exists(meta_path):
            sys.stderr.write(f"[cortex-vector] Migrating {old_meta_path} → {meta_path}...\n")
            try:
                import pickle
                with open(old_meta_path, "rb") as f:
                    legacy_meta = pickle.load(f)
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(legacy_meta, f, ensure_ascii=False, indent=2)
                os.rename(old_meta_path, old_meta_path + ".migrated")
                sys.stderr.write("[cortex-vector] Migration complete.\n")
            except Exception as me:
                sys.stderr.write(f"[cortex-vector] Migration failed: {me}. Re-index required.\n")

        if not os.path.exists(idx_path) or not os.path.exists(meta_path):
            return None, []

        index = faiss.read_index(idx_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return index, meta
    except Exception as e:
        import sys
        sys.stderr.write(f"[cortex-vector] FAISS load failed: {e}\n")
        return None, []


def _save_faiss_index(workspace: str, index, meta: list, prefix: str = "vectors"):
    """FAISS 인덱스 및 메타 저장 (JSON 포맷)"""
    import faiss
    faiss.write_index(index, _index_path(workspace, prefix))
    with open(_meta_path(workspace, prefix), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _create_new_index(dim: int):
    """새 FAISS Flat L2 인덱스 생성"""
    import faiss
    return faiss.IndexFlatIP(dim)  # Inner Product (코사인 유사도 근사)


# ============================================================
# 공개 API
# ============================================================

def index_texts(workspace: str, items: list[dict], use_gpu: bool = None, prefix: str = "vectors") -> dict:
    """
    텍스트 리스트를 임베딩하여 정의된 prefix 구역의 FAISS 인덱스에 저장 (증분 업데이트 지원)
    """
    if not items:
        return {"indexed": 0, "skipped": 0}

    import numpy as np
    import hashlib

    # 1. 기존 인덱스 및 메타 로드 (먼저 수행하여 중복 체크)
    existing_index, existing_meta = _load_faiss_index(workspace, prefix)
    existing_meta = [dict(m) if not isinstance(m, dict) else m for m in (existing_meta or [])]
    existing_ids = {m.get("id") for m in existing_meta if m.get("id")}

    # 2. 임베딩이 필요한 항목만 선별
    to_embed = []
    skipped_count = 0
    
    for item_raw in items:
        item = dict(item_raw) if not isinstance(item_raw, dict) else item_raw
        item_id = item.get("id", "")
        text = item.get("text", "")
        
        if not text or not item_id:
            skipped_count += 1
            continue

        # [중요] 이미 인덱스에 있는 ID면 건너뜀 (강제 업데이트 로직이 필요하다면 여기에 추가)
        if item_id in existing_ids:
            skipped_count += 1
            continue
            
        to_embed.append(item)

    if not to_embed:
        return {"indexed": 0, "skipped": skipped_count}

    # 3. 청킹 및 전처리
    all_texts = []
    all_metas = []
    for item in to_embed:
        text = item.get("text", "")
        item_id = item.get("id", "")
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            prefixed = f"passage: {chunk}"
            all_texts.append(prefixed)
            all_metas.append({
                "id": item_id,
                "chunk_idx": i,
                "text": chunk[:300],
                **(item.get("meta") or {}),
            })

    # 4. [Smart Device Selection]
    if use_gpu is None:
        use_gpu = len(all_texts) >= 128

    device = "cpu"
    if use_gpu:
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except ImportError:
            pass

    model = _load_model(device)

    # 5. 임베딩 생성
    embeddings = model.encode(
        all_texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype(np.float32)

    # 6. FAISS 인덱스 업데이트
    if existing_index is None:
        index = _create_new_index(embeddings.shape[1])
        meta = []
    else:
        index = existing_index
        meta = existing_meta

    index.add(embeddings)
    meta.extend(all_metas)

    _save_faiss_index(workspace, index, meta, prefix)
    return {"indexed": len(to_embed), "skipped": skipped_count}

    # 기존 항목 제거가 FAISS에서 복잡하므로, 새로 재구성
    if filtered_count > 0 and filtered_meta:
        # 기존 벡터 재구성 (필요 시)
        kept_indices = [i for i, m in enumerate(meta) if m.get("id") not in new_ids]
        if kept_indices:
            # IndexFlatIP는 reconstruct를 지원하지 않을 수 있음
            try:
                kept_vecs = index.reconstruct_batch(kept_indices)
                new_index = _create_new_index(embeddings.shape[1])
                new_index.add(kept_vecs)
                index = new_index
            except Exception:
                # reconstruct 불가 시 기존 인덱스 유지 (중복은 상위에서 처리됨)
                pass
        meta = filtered_meta

    index.add(embeddings)
    meta.extend(all_metas)
    _save_faiss_index(workspace, index, meta, prefix)

    return {"indexed": len(all_texts), "skipped": 0}


def search_similar(workspace: str, query: str, top_k: int = DEFAULT_TOP_K, use_gpu: bool = False) -> list[dict]:
    """
    쿼리와 가장 유사한 문서 청크를 반환 (모든 *.index 파일 일괄 병합 검색).
    """
    import glob
    import numpy as np
    
    data_dir = _get_data_dir(workspace)
    index_files = glob.glob(os.path.join(data_dir, "*.index"))
    if not index_files:
        return []

    device = "cpu"
    if use_gpu:
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            pass

    model = _load_model(device)

    instruction = "Given a search query, retrieve relevant code snippets and documents that help in software engineering tasks."
    query_with_instruction = f"{instruction}\nQuery: {query}"
    
    query_vec = model.encode(
        [query_with_instruction],
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype(np.float32)

    seen_ids = {}

    # 모든 파편화된 인덱스를 순회하며 검색 및 스코어 갱신
    for idx_path in index_files:
        basename = os.path.basename(idx_path)
        scan_prefix = basename[:-6]  # '.index' 제거
        
        index, meta_raw = _load_faiss_index(workspace, scan_prefix)
        if index is None or index.ntotal == 0:
            continue
            
        meta = [dict(m) if not isinstance(m, dict) else m for m in (meta_raw or [])]
        search_k = min(top_k * 3, index.ntotal)
        scores, indices = index.search(query_vec, search_k)

        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(meta):
                continue
            item_meta = meta[idx]
            item_id = item_meta.get("id", "")
            if not item_id:
                continue
                
            if item_id not in seen_ids or score > seen_ids[item_id]["score"]:
                seen_ids[item_id] = {
                    "id": item_id,
                    "score": float(score),
                    "text": item_meta.get("text", ""),
                    "meta": {k: v for k, v in item_meta.items() if k not in ("id", "text")},
                }

    results = sorted(seen_ids.values(), key=lambda x: x.get("score", 0.0), reverse=True)
    return results[:top_k]


def get_index_stats(workspace: str) -> dict:
    """벡터 인덱스 현황 반환 (모든 *.index 파일 병합 집계)"""
    import glob
    import os
    data_dir = _get_data_dir(workspace)
    index_files = glob.glob(os.path.join(data_dir, "*.index"))
    
    if not index_files:
        return {"status": "empty", "total_vectors": 0, "unique_docs": 0}
        
    total_vectors = 0
    unique_ids = set()
    
    for idx_path in index_files:
        basename = os.path.basename(idx_path)
        scan_prefix = basename[:-6]
        index, meta = _load_faiss_index(workspace, scan_prefix)
        if index is not None:
            total_vectors += index.ntotal
            unique_ids.update(m.get("id", "") for m in meta)

    return {
        "status": "ready",
        "total_vectors": total_vectors,
        "unique_docs": len(unique_ids),
        "model": MODEL_ID,
    }
