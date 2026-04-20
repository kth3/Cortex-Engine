"""
Cortex 인덱서 유틸리티 (v1.1)
파일 필터링, gitignore 처리, 설정 로드, 중앙 상수 관리.
indexer.py에서 분리됨.
"""
import os
import re
import hashlib
import fnmatch
from pathlib import Path


# ==============================================================================
# 하드웨어 프로필 (Hardware-Aware Dynamic Profiling)
# ==============================================================================

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


# ==============================================================================
# 프리셋 모드 (사용자 의도 기반 — settings.yaml의 tuning.mode로 선택)
# ==============================================================================

PRESETS = {
    "conservative": {"batch_size": 16,  "max_chars": 1000, "cache_clear_freq": 1},
    "balanced":     {"batch_size": 32,  "max_chars": 2000, "cache_clear_freq": 5},
    "turbo":        {"batch_size": 64,  "max_chars": 3000, "cache_clear_freq": 10},
}


# ==============================================================================
# 중앙 튜닝 상수 (하드웨어 비종속)
# ==============================================================================

DEFAULTS = {
    "db_chunk_size": 900,          # SQL IN 절 청크 크기
    "search_snippet_len": 200,     # 검색 결과 content 스니펫 길이
    "search_multiplier": 2,        # FTS/벡터 검색 시 limit 배수
    "rrf_k": 60,                   # RRF(Reciprocal Rank Fusion) K 파라미터
}


def get_tuning_params(workspace: str = None) -> dict:
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
            "cache_clear_freq": preset["cache_clear_freq"],
        }
    elif mode == "custom":
        # 개별 오버라이드 (없으면 하드웨어 감지값 fallback)
        resolved = {
            "batch_size": tuning_cfg.get("batch_size", hw_cap["batch_size"]),
            "max_chars": tuning_cfg.get("max_chars", hw_cap["max_chars"]),
            "cache_clear_freq": tuning_cfg.get("cache_clear_freq", hw_cap["cache_clear_freq"]),
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
    _log_tuning_report(params, hw)
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


# ==============================================================================
# 기본 무시 패턴 (프로젝트 전역)
# ==============================================================================

DEFAULT_IGNORES = [
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", "build", ".gradle", ".idea", ".vscode",
    ".agents", "target", ".next", "*.min.js", "*.min.css",
    "*.pyc", "*.class", "*.o", "*.obj", "*.exe", "*.out",
]


# ==============================================================================
# 텍스트 전처리
# ==============================================================================

def strip_frontmatter(content: str) -> str:
    """YAML Frontmatter (--- ... ---) 제거"""
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)


def compute_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()


# ==============================================================================
# 파일 필터링
# ==============================================================================

def load_gitignore(workspace: str) -> list:
    """프로젝트의 .gitignore 패턴 로드"""
    patterns = list(DEFAULT_IGNORES)
    gitignore_path = os.path.join(workspace, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line.strip("/"))
        except Exception:
            pass
    return patterns


def should_ignore(path: str, ignore_patterns: list, workspace: str) -> bool:
    """파일/디렉토리가 무시 대상인지 확인"""
    rel = os.path.relpath(path, workspace)
    parts = rel.split(os.sep)
    for part in parts:
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def load_settings(workspace: str) -> dict:
    """.agents/settings.yaml 파일 로드"""
    settings_path = os.path.join(workspace, ".agents", "settings.yaml")
    if os.path.exists(settings_path):
        try:
            import yaml
            with open(settings_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


def should_include(path: str, workspace: str, settings: dict) -> bool:
    """파일이 인덱싱 범위에 포함되는지 확인 (Whitelist 우선)"""
    rules = settings.get("indexing_rules", {})
    rel = os.path.relpath(path, workspace)
    
    # 1. 화이트리스트 파일 체크
    whitelist = rules.get("config_whitelist", [])
    for pattern in whitelist:
        if fnmatch.fnmatch(os.path.basename(rel), pattern) or fnmatch.fnmatch(rel, pattern):
            return True
            
    # 2. 포함 경로 체크
    includes = rules.get("include_paths", ["**/src/**", "**/*.py"])
    for pattern in includes:
        if fnmatch.fnmatch(rel, pattern):
            return True
            
    # 3. 모듈별 경로 체크
    modules = rules.get("modules", {})
    if isinstance(modules, dict):
        for mod_name, mod_paths in modules.items():
            for m_path in mod_paths:
                if rel.startswith(m_path) or fnmatch.fnmatch(rel, m_path):
                    return True
                
    return False


def get_module_name(rel_path: str, settings: dict) -> str:
    """경로 기반 모듈명 판단"""
    modules = settings.get("indexing_rules", {}).get("modules", {})
    if isinstance(modules, dict):
        for mod_name, mod_paths in modules.items():
            for m_path in mod_paths:
                if f"{m_path}{os.sep}" in f"{rel_path}{os.sep}" or rel_path.endswith(m_path):
                    return mod_name
    parts = rel_path.split(os.sep)
    return parts[0] if len(parts) > 1 else "root"


# ==============================================================================
# 파일 스캔
# ==============================================================================

def scan_files(workspace: str, supported_extensions: dict) -> list:
    """지능형 필터링을 적용하여 인덱싱할 파일 목록 확보"""
    settings = load_settings(workspace)
    ignore_patterns = load_gitignore(workspace)
    
    # [배포 대응] .agents/settings.yaml의 exclude_paths를 ignore_patterns에 추가
    rules = settings.get("indexing_rules", {})
    extra_excludes = rules.get("exclude_paths", [])
    if extra_excludes:
        ignore_patterns.extend([p.strip("/") for p in extra_excludes if p.strip()])
    
    files = []
    
    # 1. 기본 소스 코드 스캔
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns, workspace)]
        for fname in filenames:
            full_path = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1]
            if ext in supported_extensions:
                if not should_ignore(full_path, ignore_patterns, workspace):
                    if should_include(full_path, workspace, settings):
                        files.append(os.path.relpath(full_path, workspace))
                        
    # 2. .agents 내부 규칙, 프로토콜, 스킬, 설계 문서 강제 포함
    agent_docs = [
        ".agents/rules",
        ".agents/knowledge/resources",
        ".agents/knowledge/examples",
        ".agents/knowledge/skills",
        ".agents/docs",          # ADR 등 설계 문서
    ]
    for doc_dir in agent_docs:
        abs_doc_dir = os.path.join(workspace, doc_dir)
        if os.path.exists(abs_doc_dir):
            for path in Path(abs_doc_dir).rglob("*.md"):
                files.append(os.path.relpath(str(path), workspace))
                        
    return sorted(list(set(files)))
