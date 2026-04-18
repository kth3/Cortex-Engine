"""
Cortex 벡터 추출 엔진 (Vector Inference Engine)
- 모델: Qwen/Qwen3-Embedding-0.6B (최신 SOTA, 다국어 지원)
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

    # [Safety] VRAM OOM 방지를 위해 모든 입력 텍스트를 최대 1,200자로 제한
    # (6GB 이하 저사양 GPU 환경에서도 긴 문서로 인한 폭주를 방지함)
    texts = [t[:1200] for t in texts]

    global _model_device
    device = "cpu"
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            # 이미 VRAM(GPU)에 모델이 로드되어 있다면, 텍스트 개수(단위)에 상관없이 무조건 GPU 사용
            if _model_device == "cuda":
                device = "cuda"
            else:
                # 명시적으로 CPU를 요청한 게 아니라면, GPU 환경에서는 기본적으로 GPU 사용을 우선함
                # (VRAM이 약 2GB만 필요하므로, 사양이 되는 사용자는 항상 GPU 상주가 유리함)
                if use_gpu is False:
                    device = "cpu"
                else:
                    device = "cuda"
    except ImportError:
        pass

    model = _load_model(device)
    batch_size = 32 if device == "cuda" else (4 if device == "mps" else 8)

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype(np.float32)

    return embeddings
