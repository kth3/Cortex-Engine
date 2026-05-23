from cortex.config.settings import load_settings

HARDWARE_PROFILES = {
    "cpu": {
        "batch_size": 4,
        "max_chars": 800,
        "cache_clear_freq": 0,      # CPU는 GPU 캐시 해제 불필요
    },
    "mps": {
        "batch_size": 16,
        "max_chars": 1200,
        "cache_clear_freq": 0,      # Mac 통합 메모리 — 강제 해제 불필요
    },
    "cuda_low": {
        "batch_size": 32,
        "max_chars": 2000,
        "cache_clear_freq": 5,      # < 8GB VRAM
    },
    "cuda_high": {
        "batch_size": 64,
        "max_chars": 3000,
        "cache_clear_freq": 10,     # >= 8GB VRAM
    },
}


def detect_hardware_profile() -> dict:
    """현재 하드웨어를 자동 감지하여 최적 프로필을 반환.

    감지 순서: CUDA → MPS → CPU (fallback)
    CUDA의 경우 VRAM 8GB 기준으로 cuda_low / cuda_high 분기.

    Returns:
        {"name": str, "batch_size": int, "max_chars": int, "cache_clear_freq": int}
    """
    # 1. 서버가 활성화되어 있다면, 클라이언트 프로세스에서는 CUDA Context 생성을 방지하기 위해
    # torch.cuda.is_available() 호출을 건너뛰고 서버 응답에 맞춰 프로필을 반환합니다.
    try:
        from cortex.embeddings.server_client import _send_to_server
        status = _send_to_server({"command": "ping"}, retries=1)
        if status.get("status") == "ok":
            vram = 6.0
            profile = dict(HARDWARE_PROFILES["cuda_low"])
            profile["name"] = "cuda_low"
            profile["vram_gb"] = round(vram, 1)
            profile["cache_clear_freq"] = 0  # 서버가 처리하므로 로컬 프로세스는 캐시 해제(및 CUDA 초기화) 불필요
            return profile
    except Exception:
        pass

    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            vram_gb = props.total_memory / (1024 ** 3)
            if vram_gb >= 8.0:
                profile = dict(HARDWARE_PROFILES["cuda_high"])
                profile["name"] = "cuda_high"
                profile["vram_gb"] = round(vram_gb, 1)
            else:
                profile = dict(HARDWARE_PROFILES["cuda_low"])
                profile["name"] = "cuda_low"
                profile["vram_gb"] = round(vram_gb, 1)
            return profile
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            profile = dict(HARDWARE_PROFILES["mps"])
            profile["name"] = "mps"
            return profile
    except ImportError:
        pass

    profile = dict(HARDWARE_PROFILES["cpu"])
    profile["name"] = "cpu"
    return profile


PRESETS = {
    "conservative": {"batch_size": 16,  "max_chars": 1000, "cache_clear_freq": 1},
    "balanced":     {"batch_size": 32,  "max_chars": 2000, "cache_clear_freq": 5},
    "turbo":        {"batch_size": 64,  "max_chars": 3000, "cache_clear_freq": 10},
}


DEFAULTS = {
    "db_chunk_size": 900,          # SQL IN 절 청크 크기
    "search_snippet_len": 200,     # 검색 결과 content 스니펫 길이
    "search_multiplier": 2,        # FTS/벡터 검색 시 limit 배수
    "rrf_k": 60,                   # RRF(Reciprocal Rank Fusion) K 파라미터
}

_TUNING_REPORT_LOGGED = False

def get_tuning_params(workspace: str = None, silent: bool = False) -> dict:
    global _TUNING_REPORT_LOGGED
    """프리셋 모드 + 하드웨어 프로필 + settings.yaml 오버라이드를 병합하여 반환.

    해석 우선순위:
      1. mode: conservative|balanced|turbo → 프리셋 값 적용
         (단, 하드웨어 상한선으로 자동 클램핑하여 OOM 방지)
      2. mode: custom → settings.yaml 개별 값 + 하드웨어 감지 기본값
      3. mode: auto (기본) → 하드웨어 자동 감지 프로필 그대로 사용

    Usage:
        params = get_tuning_params(workspace)
        batch_size = params["batch_size"]
    """
    # 1. 하드웨어 감지 (상한선 역할)
    hw = detect_hardware_profile()
    hw_cap = {
        "batch_size": hw["batch_size"],
        "max_chars": hw["max_chars"],
        "cache_clear_freq": hw["cache_clear_freq"],
    }

    # 2. settings.yaml 읽기
    tuning_cfg = {}
    if workspace:
        settings = load_settings(workspace)
        tuning_cfg = settings.get("tuning", {}) or {}

    mode = tuning_cfg.get("mode", "auto")

    # 3. 모드별 해석
    if mode in PRESETS:
        # 프리셋 적용 + 하드웨어 상한선 클램핑 (OOM 안전장치)
        preset = PRESETS[mode]
        resolved = {
            "batch_size": min(preset["batch_size"], hw_cap["batch_size"]),
            "max_chars": min(preset["max_chars"], hw_cap["max_chars"]),
            "cache_clear_freq": 0 if hw_cap["cache_clear_freq"] == 0 else preset["cache_clear_freq"],
        }
    elif mode == "custom":
        # 개별 오버라이드 (없으면 하드웨어 감지값 fallback)
        resolved = {
            "batch_size": tuning_cfg.get("batch_size", hw_cap["batch_size"]),
            "max_chars": tuning_cfg.get("max_chars", hw_cap["max_chars"]),
            "cache_clear_freq": 0 if hw_cap["cache_clear_freq"] == 0 else tuning_cfg.get("cache_clear_freq", hw_cap["cache_clear_freq"]),
        }
    else:
        # auto: 하드웨어 감지 프로필 그대로
        resolved = dict(hw_cap)

    # 4. 최종 병합
    params = {
        "hw_profile": hw["name"],
        "mode": mode,
    }
    params.update(resolved)
    params.update(DEFAULTS)

    # 5. 튜닝 리포트 출력
    if not silent and not _TUNING_REPORT_LOGGED:
        _log_tuning_report(params, hw)
        _TUNING_REPORT_LOGGED = True
        
    return params

def _log_tuning_report(params: dict, hw: dict):
    """현재 적용된 튜닝 상태를 사용자 친화적으로 출력"""
    from cortex.logger import get_logger
    log = get_logger("tuning")

    vram_info = f" ({hw.get('vram_gb', '?')}GB)" if "vram_gb" in hw else ""
    mode = params["mode"]
    hw_name = params["hw_profile"].upper()

    log.info("=" * 44)
    log.info("  Cortex Tuning Report")
    log.info("  Hardware : %s%s", hw_name, vram_info)
    log.info("  Mode     : %s", mode.upper())
    log.info("  Gear     : batch=%d | chars=%d | flush=%d",
             params["batch_size"], params["max_chars"], params["cache_clear_freq"])

    # 클램핑 발생 시 알림
    if mode in ("conservative", "balanced", "turbo"):
        preset = PRESETS[mode]
        if preset["batch_size"] > params["batch_size"]:
            log.info("  * Clamped: batch %d -> %d (hw limit)",
                     preset["batch_size"], params["batch_size"])
        if preset["max_chars"] > params["max_chars"]:
            log.info("  * Clamped: chars %d -> %d (hw limit)",
                     preset["max_chars"], params["max_chars"])

    log.info("=" * 44)
