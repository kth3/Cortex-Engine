"""
Cortex 벡터 임베딩 배치 처리 엔진 (v2.0 — Hardware-Aware)
indexer.py의 벡터 임베딩 로직을 분리하여 독립 모듈로 관리.
하드웨어 프로필(CPU/MPS/CUDA)을 자동 감지하여 최적 파라미터 적용.
"""
import gc
from cortex.logger import get_logger
from cortex.indexer_utils import get_tuning_params

log = get_logger("vectorizer")

# 이 임계치 이하의 아이템은 GPU 시동 비용(모델 로드/VRAM 전송) 대비 CPU가 체감 더 빠름
GPU_THRESHOLD = 20


def _maybe_flush_gpu(use_gpu: bool, counter: int, freq: int):
    """N 배치 주기마다 GPU 캐시를 비워 재할당 오버헤드를 줄인다.
    freq=0이면 해제를 수행하지 않음 (CPU/MPS 환경).
    """
    if freq > 0 and use_gpu and counter % freq == 0:
        import torch
        torch.cuda.empty_cache()
    gc.collect()


def batch_vectorize_nodes(conn, items_by_prefix: dict, use_gpu: bool,
                          workspace: str = None):
    """노드 벡터 임베딩 배치 처리.
    
    Args:
        conn: SQLite 연결 객체
        items_by_prefix: {prefix: [vector_items]} 형태의 딕셔너리
        use_gpu: GPU 사용 여부
        workspace: settings.yaml 오버라이드를 위한 워크스페이스 경로
    """
    from cortex import vector_engine as ve
    from tqdm import tqdm

    params = get_tuning_params(workspace)
    batch_size = params["batch_size"]
    freq = params["cache_clear_freq"]

    total_items = sum(len(v) for v in items_by_prefix.values())

    # [Hybrid Strategy] 전체 아이템 수를 먼저 센 뒤 GPU/CPU 결정
    # → 폴더별 분할 감지로 인한 잘못된 CPU→GPU 전환 방지
    # [Policy Update] 사용자가 명시적으로 GPU(True) 혹은 CPU(False)를 지정했다면 임계값을 무시하고 존중한다.
    if use_gpu is None and total_items <= GPU_THRESHOLD:
        use_gpu = False   # 기회적 CPU: 소량 작업 시 로딩 시간 절약

    log.info("Nodes vectorize | profile: %s, device: %s, batch: %d, freq: %d, items: %d",
             params["hw_profile"], "GPU" if use_gpu else "CPU", batch_size, freq, total_items)

    counter = 0
    for prefix, items in items_by_prefix.items():
        if not items:
            continue
        # 동일 FQN 노드 중복 제거 (마지막 항목 우선)
        deduped = list({item["id"]: item for item in items}.values())
        for i in tqdm(range(0, len(deduped), batch_size), desc=f"Nodes [{prefix}]", unit="batch"):
            batch = deduped[i:i + batch_size]
            texts = [item["text"] for item in batch]
            embeddings = ve.get_embeddings(texts, use_gpu=use_gpu)
            for item, emb in zip(batch, embeddings):
                rowid_cur = conn.execute("SELECT rowid FROM nodes WHERE id = ?", (item["id"],)).fetchone()
                if rowid_cur:
                    conn.execute("DELETE FROM vec_nodes WHERE rowid = ?", (rowid_cur[0],))
                    conn.execute("INSERT INTO vec_nodes(rowid, embedding) VALUES (?, ?)", (rowid_cur[0], emb.tobytes()))
            conn.commit()
            counter += 1
            _maybe_flush_gpu(use_gpu, counter, freq)


def batch_vectorize_memories(conn, use_gpu: bool, workspace: str = None):
    """memories 테이블의 증분 벡터 인덱싱.
    
    Args:
        conn: SQLite 연결 객체
        use_gpu: GPU 사용 여부
        workspace: settings.yaml 오버라이드를 위한 워크스페이스 경로
    
    Returns:
        인덱싱된 메모리 수
    """
    params = get_tuning_params(workspace)
    batch_size = params["batch_size"]
    max_chars = params["max_chars"]
    freq = params["cache_clear_freq"]

    # vec_memories에 아직 없는(LEFT JOIN IS NULL) 메모리만 조회
    memory_rows = conn.execute(
        "SELECT m.rowid, m.key, m.category, m.content FROM memories m "
        "LEFT JOIN vec_memories v ON m.rowid = v.rowid WHERE v.rowid IS NULL"
    ).fetchall()

    if not memory_rows:
        return 0

    from cortex import vector_engine as ve
    from tqdm import tqdm

    log.info("Memories vectorize | profile: %s, device: %s, batch: %d, max_chars: %d, items: %d",
             params["hw_profile"], "GPU" if use_gpu else "CPU", batch_size, max_chars, len(memory_rows))

    memory_vector_items = []
    for row in memory_rows:
        rowid, key, category, content = row
        memory_vector_items.append({
            "id": key,
            "rowid": rowid,
            "text": f"category: {category}\n{content}",
            "meta": {"category": category, "type": "memory", "source": "sqlite"}
        })

    total_indexed = 0
    counter = 0
    for i in tqdm(range(0, len(memory_vector_items), batch_size), desc="Memories", unit="batch"):
        batch = memory_vector_items[i:i + batch_size]
        # 하드웨어 프로필에 따라 동적으로 텍스트 길이 제한
        texts = [item["text"][:max_chars] for item in batch]
        embeddings = ve.get_embeddings(texts, use_gpu=use_gpu)
        for item, emb in zip(batch, embeddings):
            conn.execute("DELETE FROM vec_memories WHERE rowid = ?", (item["rowid"],))
            conn.execute("INSERT INTO vec_memories(rowid, embedding) VALUES (?, ?)", (item["rowid"], emb.tobytes()))
        conn.commit()
        total_indexed += len(batch)
        counter += 1
        _maybe_flush_gpu(use_gpu, counter, freq)

    log.info("Synced %d memories to vec_memories.", total_indexed)
    return total_indexed


def detect_gpu() -> bool:
    """GPU 사용 가능 여부 탐지 (하드웨어 프로필에 맞춰 CUDA 또는 MPS 자동 감지)"""
    try:
        import torch
        if torch.cuda.is_available():
            return True
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return True
        return False
    except ImportError:
        return False
