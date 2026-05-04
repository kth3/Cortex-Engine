"""
Cortex 벡터 추출 엔진 (Vector Inference Engine)
- 모델: Qwen/Qwen3-Embedding-0.6B (영한 다국어 특화, 1024차원)
- 목적: FAISS를 제거하고 순수하게 텍스트를 임베딩 벡터(float[1024])로 변환하는 역할만 수행
"""
import os
import sys
import json
import socket
import struct
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트 설정
CORTEX_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(CORTEX_DIR)))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(ENV_PATH)

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

# IPC 설정 (Windows/Linux 공용 TCP)
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 62384

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
        from huggingface_hub import snapshot_download
        import torch
        from cortex.logger import get_logger
        
        log = get_logger("cortex.vector")
        hf_token = os.getenv("HF_TOKEN", "").strip() or None

        if sys.platform == "darwin":
            os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

        # 1. 모델 명시적 다운로드 (Snapshot 방식)
        # SentenceTransformer 생성자 내부의 자동 다운로드 로직이 Windows에서 불안정하므로
        # 명시적으로 먼저 로컬로 받아낸 뒤 경로를 전달한다.
        log.info(f"Checking model availability: {MODEL_ID}")
        try:
            # 타임아웃 넉넉히 설정하여 세션 종료 방지
            model_path = snapshot_download(
                repo_id=MODEL_ID,
                token=hf_token,
                local_files_only=False,
                resume_download=True,
                max_workers=4
            )
            log.info(f"Model path verified: {model_path}")
        except Exception as e:
            log.warning(f"Snapshot download failed or interrupted: {e}. Trying direct load...")
            model_path = MODEL_ID

        # 2. 디바이스 데이터 타입 결정
        dtype_choice = torch.float32
        if device == "cuda":
            # [Optimization] Use bfloat16 if supported (Ampere+), else fallback to float16
            if torch.cuda.is_bf16_supported():
                dtype_choice = torch.bfloat16
                log.info("Using bfloat16 (bf16) for CUDA acceleration.")
            else:
                dtype_choice = torch.float16
                log.info("Using float16 (fp16) for CUDA acceleration.")
        elif device == "mps":
            dtype_choice = torch.float16

        model_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": dtype_choice,
        }

        # 3. 모델 로드 (로컬 경로 우선)
        log.info(f"Initializing SentenceTransformer on {device}...")
        _model = SentenceTransformer(
            model_path, 
            device=device, 
            model_kwargs=model_kwargs, 
            token=hf_token
        )
        _model.max_seq_length = 4096  # Qwen3 컨텍스트 윈도우 — 모델 토크나이저가 안전하게 truncate
        
        if device in ["cuda", "mps"]:
            _model.to(dtype_choice)

        _model_device = device
        log.info(f"Model successfully loaded on {_model_device}.")
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

def _send_to_server(request: dict, retries: int = 15) -> dict:
    """엔진 서버에 요청을 보내고 응답을 받는다 (TCP)."""
    import time
    for i in range(retries):
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(10.0)
            client.connect((ENGINE_HOST, ENGINE_PORT))
            
            data = json.dumps(request).encode("utf-8")
            client.sendall(struct.pack("!I", len(data)) + data)
            
            header = client.recv(4)
            if not header:
                return {"status": "error", "message": "Empty response"}
            size = struct.unpack("!I", header)[0]
            
            resp_data = b""
            while len(resp_data) < size:
                chunk = client.recv(min(size - len(resp_data), 4096))
                if not chunk: break
                resp_data += chunk
            
            return json.loads(resp_data.decode("utf-8"))
        except (ConnectionRefusedError, socket.timeout):
            if i < retries - 1:
                time.sleep(1.0)
                continue
            return {"status": "offline"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            client.close()
    return {"status": "error", "message": "Max retries exceeded"}

def get_embeddings(texts: list[str], use_gpu: bool = None) -> np.ndarray:
    if not texts:
        return np.array([])

    # [BUGFIX] Surrogate 문자열 제거 (JSON 직렬화 및 HuggingFace Tokenizer 에러 방지)
    try:
        texts = [t.encode('utf-8', 'replace').decode('utf-8') for t in texts]
    except Exception:
        pass

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
            # [Shared Engine Policy] 서버 소켓이 존재한다면,
            # 로컬에서는 GPU를 점유하지 않고 안전하게 CPU로 폴백하여 중복 방지.
            status = _send_to_server({"command": "ping"}, retries=1)
            if status.get("status") == "ok":
                # [Strict Fix] 사용자가 명시적으로 use_gpu=True를 요구했더라도,
                # 이미 서버가 GPU를 점유 중이면 충돌 방지를 위해 강제로 CPU 모드 전환
                if use_gpu is True:
                    from cortex.logger import get_logger
                    get_logger("vector").warning("Shared Engine Server exists and holding GPU. Forcing Local CPU mode to prevent VRAM conflict/crash.")
                device = "cpu"
            elif use_gpu is True:
                device = "cuda"
            else:
                device = "cpu"
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
