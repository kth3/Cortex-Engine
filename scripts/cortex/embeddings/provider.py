"""Embedding model provider.

- 서버 우선 경로 vs Local Fallback: 임베딩 요청 시 항상 Engine Server(상주 프로세스)로 먼저 라우팅을 시도한다. 서버가 오프라인이거나 에러가 발생한 경우에만 현재 프로세스(Local Fallback)에 모델을 직접 로드하여 처리한다.
- use_gpu의 의미: False일 경우 강제로 CPU만 사용하여 임베딩을 수행하며, None/True일 경우 서버 가용성 및 하드웨어 상태에 따라 GPU를 최우선으로 시도한다.
"""
import os
import sys
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

from cortex.logger import get_logger
from cortex.embeddings.server_client import _send_to_server

log = get_logger("vector")

def _resolve_env_path() -> Path:
    explicit = os.getenv("CORTEX_ENV_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()

    home = os.getenv("CORTEX_HOME")
    if home:
        return Path(home).expanduser().resolve() / ".env"

    try:
        from cortex.paths import data_home
        global_env = data_home() / ".env"
        if global_env.exists():
            return global_env
    except Exception:
        pass

    capsule_root = Path(__file__).resolve().parents[3]
    candidate = capsule_root / ".env"
    if candidate.exists():
        return candidate

    cwd_candidate = Path.cwd().resolve() / ".env"
    if cwd_candidate.exists():
        return cwd_candidate

    return candidate

ENV_PATH = _resolve_env_path()
load_dotenv(ENV_PATH)

DEFAULT_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_MAX_SEQ_LENGTH = 4096


def _resolve_model_id() -> str:
    raw = (os.environ.get("CORTEX_EMBEDDING_MODEL") or "").strip()
    return raw or DEFAULT_MODEL_ID


def _resolve_max_seq_length() -> int:
    raw = (os.environ.get("CORTEX_EMBEDDING_MAX_SEQ_LENGTH") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_SEQ_LENGTH


MODEL_ID = _resolve_model_id()
MAX_SEQ_LENGTH = _resolve_max_seq_length()

_model = None
_model_device = None

def _clear_model():
    global _model, _model_device
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
            from cortex.embeddings.hardware import release_gpu
            release_gpu()  # VRAM 해제 + _model=None 초기화
        else:
            _model = None
            _model_device = None

    try:
        from sentence_transformers import SentenceTransformer
        from huggingface_hub import snapshot_download
        import torch
        
        hf_token = os.getenv("HF_TOKEN", "").strip() or None

        if sys.platform == "darwin":
            os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

        # 1. 모델 명시적 다운로드 (Snapshot 방식)
        log.info(f"Checking model availability: {MODEL_ID}")
        try:
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
        _model.max_seq_length = MAX_SEQ_LENGTH  # 모델별 컨텍스트 윈도우 (CORTEX_EMBEDDING_MAX_SEQ_LENGTH로 override)
        
        if device in ["cuda", "mps"]:
            _model.to(dtype_choice)

        _model_device = device
        log.info(f"Model successfully loaded on {_model_device}.")
    except Exception as e:
        log.error("Model Load Error: %s", e)
        raise RuntimeError(f"모델 로딩 실패: {e}")

    return _model

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
            log.warning("Server Error: %s. Falling back to local...", resp.get('message'))

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
                    log.warning("Shared Engine Server exists and holding GPU. Forcing Local CPU mode to prevent VRAM conflict/crash.")
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
