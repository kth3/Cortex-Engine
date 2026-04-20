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

def preload_model(device: str = "cpu"):
    """원격 데몬 상주화 시 하드웨어 부하를 조절하기 위해 모델을 수동으로 선행 로드한다."""
    return _load_model(device=device)

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

import json
import socket
import struct

SOCKET_PATH = "/tmp/cortex.sock"

def _send_to_server(request: dict) -> dict:
    """엔진 서버에 요청을 보내고 응답을 받는다."""
    if not os.path.exists(SOCKET_PATH):
        return {"status": "offline"}

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10.0) # 넉넉한 타임아웃
        client.connect(SOCKET_PATH)
        
        # 데이터 전송 (길이 헤더 + 바디)
        data = json.dumps(request).encode("utf-8")
        client.sendall(struct.pack("!I", len(data)) + data)
        
        # 응답 수신
        header = client.recv(4)
        if not header:
            return {"status": "error", "message": "No response from server"}
        size = struct.unpack("!I", header)[0]
        
        resp_data = b""
        while len(resp_data) < size:
            chunk = client.recv(min(size - len(resp_data), 4096))
            if not chunk:
                break
            resp_data += chunk
            
        return json.loads(resp_data.decode("utf-8"))
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        client.close()

def get_embeddings(texts: list[str], use_gpu: bool = None) -> np.ndarray:
    if not texts:
        return np.array([])

    # 1. 서버 모드 시도 (상주 중인 GPU 엔진 활용)
    # use_gpu가 False가 아닐 때만 서버(GPU) 시도
    if use_gpu is not False:
        resp = _send_to_server({"command": "embed", "texts": texts})
        if resp.get("status") == "ok":
            return np.array(resp["embeddings"], dtype=np.float32)
        
        if resp.get("status") == "error":
            sys.stderr.write(f"[cortex-vector] Server Error: {resp.get('message')}. Falling back to local...\n")

    # 2. 서버 오프라인 또는 강제 CPU 모드 시 로컬 처리
    global _model_device
    device = "cpu"
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            if use_gpu is True:
                device = "cuda"
            else:
                device = "cpu" # 기본적으로 서버가 없으면 CPU 상주 모드로 fallback
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
