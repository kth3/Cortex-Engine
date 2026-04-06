"""
Cortex 벡터 검색 엔진 (Vector Engine)
- 모델: BAAI/bge-m3 (다국어, 100개+ 언어 지원)
- 인덱싱: GPU(CUDA) 가속 → 빠른 대량 임베딩
- 검색: CPU 모드 → VRAM 0MB 점유
- 저장소: FAISS (로컬 파일 기반, cortex_data/vectors.index)
"""
import os
import json

# 청킹 설정
CHUNK_SIZE = 1200         # 청크당 최대 문자 수 (BGE-M3 최적화)
CHUNK_OVERLAP = 150       # 청크 간 오버랩
DEFAULT_TOP_K = 5         # 기본 검색 결과 수

# 모델 식별자
MODEL_ID = "BAAI/bge-m3"

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
    """BGE-M3 모델 지연 로딩 (device: 'cpu' 또는 'cuda')"""
    global _model, _model_device
    if _model is not None and _model_device == device:
        return _model

    try:
        from sentence_transformers import SentenceTransformer
        import sys
        sys.stderr.write(f"[cortex-vector] Loading BGE-M3 on {device}...\n")
        _model = SentenceTransformer(MODEL_ID, device=device)
        _model_device = device
        sys.stderr.write(f"[cortex-vector] BGE-M3 loaded on {device}.\n")
    except Exception as e:
        raise RuntimeError(f"BGE-M3 모델 로딩 실패: {e}")

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

def _index_path(workspace: str) -> str:
    return os.path.join(_get_data_dir(workspace), "vectors.index")


def _meta_path(workspace: str) -> str:
    return os.path.join(_get_data_dir(workspace), "vectors_meta.json")


def _load_faiss_index(workspace: str):
    """FAISS 인덱스 및 메타 로드 (없으면 None 반환)"""
    try:
        import faiss
        import sys
        idx_path = _index_path(workspace)
        meta_path = _meta_path(workspace)

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


def _save_faiss_index(workspace: str, index, meta: list):
    """FAISS 인덱스 및 메타 저장 (JSON 포맷)"""
    import faiss
    faiss.write_index(index, _index_path(workspace))
    with open(_meta_path(workspace), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _create_new_index(dim: int):
    """새 FAISS Flat L2 인덱스 생성"""
    import faiss
    return faiss.IndexFlatIP(dim)  # Inner Product (코사인 유사도 근사)


# ============================================================
# 공개 API
# ============================================================

def index_texts(workspace: str, items: list[dict], use_gpu: bool = False) -> dict:
    """
    텍스트 리스트를 임베딩하여 FAISS 인덱스에 저장.

    Args:
        workspace: 프로젝트 루트 경로
        items: [{"id": str, "text": str, "meta": dict}, ...] 형태 리스트
        use_gpu: True면 CUDA 사용 (대량 인덱싱용), False면 CPU

    Returns:
        {"indexed": int, "skipped": int}
    """
    if not items:
        return {"indexed": 0, "skipped": 0}

    import numpy as np  # lazy import

    device = "cuda" if use_gpu else "cpu"
    try:
        import torch
        if use_gpu and not torch.cuda.is_available():
            device = "cpu"
    except ImportError:
        device = "cpu"

    model = _load_model(device)

    # 청킹 및 전처리
    all_texts = []
    all_metas = []
    for item_raw in items:
        # item이 튜플인 경우를 대비해 dict로 변환 (안전 장치)
        item = dict(item_raw) if not isinstance(item_raw, dict) else item_raw

        text = item.get("text", "")
        item_id = item.get("id", "")
        if not text or not item_id:
            continue

        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            prefixed = f"passage: {chunk}"  # BGE-M3 권장 prefix
            all_texts.append(prefixed)
            all_metas.append({
                "id": item_id,
                "chunk_idx": i,
                "text": chunk[:300],  # 미리보기 저장
                **(item.get("meta") or {}),
            })

    if not all_texts:
        return {"indexed": 0, "skipped": len(items)}

    # 임베딩 생성 (배치 처리)
    embeddings = model.encode(
        all_texts,
        batch_size=64,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype(np.float32)

    # FAISS 인덱스에 추가
    existing_index, existing_meta = _load_faiss_index(workspace)
    if existing_index is None:
        index = _create_new_index(embeddings.shape[1])
        meta = []
    else:
        index = existing_index
        # meta 리스트의 각 항목이 dict임을 보장
        meta = [dict(m) if not isinstance(m, dict) else m for m in (existing_meta or [])]

    # 기존 ID 중복 제거 후 추가
    new_ids = {item.get("id") for item in items if isinstance(item, dict) or hasattr(item, "get")}
    filtered_meta = [m for m in meta if m.get("id") not in new_ids]
    filtered_count = len(meta) - len(filtered_meta)

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
    _save_faiss_index(workspace, index, meta)

    return {"indexed": len(all_texts), "skipped": 0}


def search_similar(workspace: str, query: str, top_k: int = DEFAULT_TOP_K, use_gpu: bool = False) -> list[dict]:
    """
    쿼리와 가장 유사한 문서 청크를 반환.

    Args:
        workspace: 프로젝트 루트 경로
        query: 검색 질의 (한국어/영어/스페인어 등 다국어 지원)
        top_k: 반환할 최대 결과 수
        use_gpu: True면 CUDA 사용 (False 권장 for 실시간 검색)

    Returns:
        [{"id": str, "score": float, "text": str, "meta": dict}, ...]
    """
    index, meta_raw = _load_faiss_index(workspace)
    if index is None or index.ntotal == 0:
        return []

    # meta 리스트의 각 항목이 dict임을 보장
    meta = [dict(m) if not isinstance(m, dict) else m for m in (meta_raw or [])]

    import numpy as np  # lazy import
    device = "cpu"  # 검색은 CPU 기본값 (VRAM 0MB)
    if use_gpu:
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            pass

    model = _load_model(device)

    # BGE-M3 쿼리 prefix
    query_with_prefix = f"query: {query}"
    query_vec = model.encode(
        [query_with_prefix],
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype(np.float32)

    # FAISS 검색
    search_k = min(top_k * 3, index.ntotal)  # 중복 ID 제거를 위해 더 많이 검색
    scores, indices = index.search(query_vec, search_k)

    # 결과 조합 (중복 ID는 최고 점수만 유지)
    seen_ids = {}
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

    # 점수 기준 정렬 후 상위 K개 반환
    results = sorted(seen_ids.values(), key=lambda x: x.get("score", 0.0), reverse=True)
    return results[:top_k]


def get_index_stats(workspace: str) -> dict:
    """벡터 인덱스 현황 반환"""
    index, meta = _load_faiss_index(workspace)
    if index is None:
        return {"status": "empty", "total_vectors": 0, "unique_docs": 0}
    unique_ids = len(set(m.get("id", "") for m in meta))
    return {
        "status": "ready",
        "total_vectors": index.ntotal,
        "unique_docs": unique_ids,
        "model": MODEL_ID,
    }
