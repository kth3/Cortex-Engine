import gc
import sys

def _maybe_flush_gpu(use_gpu: bool, counter: int, freq: int):
    """N 배치 주기마다 GPU 캐시를 비워 재할당 오버헤드를 줄인다.
    freq=0이면 해제를 수행하지 않음 (CPU/MPS 환경).
    """
    if freq > 0 and use_gpu and counter % freq == 0:
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
    gc.collect()

def detect_gpu() -> bool:
    """GPU 사용 가능 여부 탐지 (하드웨어 프로필에 맞춰 CUDA 또는 MPS 자동 감지)"""
    # 1. 서버가 활성화되어 있다면, 클라이언트 프로세스에서는 CUDA Context 생성을 방지하기 위해
    # torch.cuda.is_available() 호출을 건너뜁니다.
    try:
        from cortex.embeddings.server_client import _send_to_server
        status = _send_to_server({"command": "ping"}, retries=1)
        if status.get("status") == "ok":
            return True
    except Exception:
        pass

    try:
        import torch
        if torch.cuda.is_available():
            return True
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return True
        return False
    except ImportError:
        return False

def release_gpu():
    """GPU VRAM 해제 + 모델 캐시 초기화"""
    from cortex.embeddings.provider import _model_device, _clear_model
    if _model_device == "cuda":
        try:
            import torch
            _clear_model()
            torch.cuda.empty_cache()
            sys.stderr.write("[cortex-vector] GPU memory released.\n")
        except Exception:
            pass
