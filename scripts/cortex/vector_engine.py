"""
Cortex 벡터 추출 엔진 (Vector Inference Engine)
- 모델: Qwen/Qwen3-Embedding-0.6B (영한 다국어 특화, 1024차원)
- 목적: FAISS를 제거하고 순수하게 텍스트를 임베딩 벡터(float[1024])로 변환하는 역할만 수행
"""
import os
import sys
import numpy as np
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
load_dotenv(ENV_PATH)

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

_model = None
_model_device = None

def _load_model(device: str = "cpu"):
    global _model, _model_device
    if _model is not None and _model_device == device:
        return _model

    # 디바이스 전환 시 기존 모델 안전 해제 (GPU→CPU 전환 시 VRAM 반환)
    if _model is not None and _model_device != device:
        if _model_device == "cuda":
            release_gpu()  # VRAM 해제 + _model=None 초기화
        else:
            _model = None
            _model_device = None

    try:
        from sentence_transformers import SentenceTransformer
        import torch

        hf_token = os.getenv("HF_TOKEN", "").strip() or None
        sys.stderr.write(f"[cortex-vector] Loading {MODEL_ID} on {device}...\n")

        if sys.platform == "darwin":
            os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

        dtype_choice = torch.float32
        if device == "cuda":
            # [Optimization] Use bfloat16 if supported (Ampere+), else fallback to float16
            if torch.cuda.is_bf16_supported():
                dtype_choice = torch.bfloat16
                sys.stderr.write("[cortex-vector] Using bfloat16 (bf16) for CUDA acceleration.\n")
            else:
                dtype_choice = torch.float16
                sys.stderr.write("[cortex-vector] Using float16 (fp16) for CUDA acceleration.\n")
        elif device == "mps":
            dtype_choice = torch.float16

        model_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": dtype_choice,
        }

        _model = SentenceTransformer(MODEL_ID, device=device, model_kwargs=model_kwargs, token=hf_token)
        _model.max_seq_length = 4096  # Qwen3 컨텍스트 윈도우 — 모델 토크나이저가 안전하게 truncate
        if device in ["cuda", "mps"]:
            _model.to(dtype_choice)

        _model_device = device
        sys.stderr.write(f"[cortex-vector] Model successfully loaded on {_model_device}.\n")
    except Exception as e:
        sys.stderr.write(f"[cortex-vector] Model Load Error: {e}\n")
        raise RuntimeError(f"모델 로딩 실패: {e}")

    return _model

def release_gpu():
    global _model, _model_device
    if _model_device == "cuda" and _model is not None:
        try:
            import torch
            _model = None
            _model_device = None
            torch.cuda.empty_cache()
            sys.stderr.write("[cortex-vector] GPU memory released.\n")
        except Exception:
            pass

def get_embeddings(texts: list[str], use_gpu: bool = None) -> np.ndarray:
    if not texts:
        return np.array([])

    # [NOTE] 텍스트 길이 제한 해제 — max_seq_length=4096 에서 모델 토크나이저가 자동 truncate 처리

    global _model_device
    device = "cpu"
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            if use_gpu is True:
                device = "cuda"  # 명시적 GPU 요청 (대량 인덱싱)
            elif use_gpu is False:
                device = "cpu"   # 명시적 CPU 강제 (소량 증분/기회적 인덱싱)
            else:
                # None: 이미 로드된 디바이스 유지, 미로드 시 CPU 기본
                device = _model_device if _model_device else "cpu"
    except ImportError:
        pass

    model = _load_model(device)
    batch_size = 16 if device == "cuda" else 8

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype(np.float32)

    return embeddings
