"""
Cortex 통합 로거 (v2.2)
- 기본 로그는 .agents/history/cortex.log로 수렴.
- Windows 다중 프로세스 환경에서는 롤오버 충돌(WinError 32)을 피하기 위해 회전 비활성화.
"""
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "cortex"
WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
LOG_FILE = WORKSPACE / ".agents" / "history" / "cortex.log"
MAX_BYTES = 1 * 1024 * 1024  # 1MB 상한선
BACKUP_COUNT = 3             # 1.gz, 2.gz... 최대 3개 보관

_initialized = False


def get_logger(module_name: str = None) -> logging.Logger:
    global _initialized

    root_logger = logging.getLogger(LOGGER_NAME)

    # [Singleton Guard] 기존에 등록된 핸들러가 있다면 중복 방지를 위해 모두 제거
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            try:
                handler.close()
            except Exception:
                pass
            root_logger.removeHandler(handler)

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    root_logger.setLevel(logging.INFO)

    # 1. 파일 핸들러 (CORTEX_NO_FILE_LOG가 설정되지 않은 경우에만 등록)
    if os.environ.get("CORTEX_NO_FILE_LOG") != "1":
        if os.name == "nt":
            # Windows에서 다중 프로세스가 동일 로그 파일을 잡고 있을 때
            # RotatingFileHandler의 rename 단계가 WinError 32로 자주 실패한다.
            file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        else:
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )

        file_formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # 2. 스트림 핸들러 (MCP JSON-RPC 통신 규격 보호를 위해 반드시 sys.stderr 사용)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream_handler.setFormatter(stream_formatter)
    root_logger.addHandler(stream_handler)

    root_logger.propagate = False

    name = f"{LOGGER_NAME}.{module_name}" if module_name else LOGGER_NAME
    return logging.getLogger(name)
