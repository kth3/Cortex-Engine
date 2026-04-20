"""
Cortex 통합 로거 (v2.1)
- 1MB 단위 실시간 자동 로테이션 (런타임 대응)
- 모든 로그를 .agents/history/cortex.log 하나로 강제 수렴.
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

    if not _initialized:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        root_logger = logging.getLogger(LOGGER_NAME)
        root_logger.setLevel(logging.INFO)

        # 1. 파일 핸들러 (실시간 1MB 로테이션)
        file_handler = RotatingFileHandler(
            LOG_FILE, 
            maxBytes=MAX_BYTES, 
            backupCount=BACKUP_COUNT, 
            encoding="utf-8"
        )
        file_formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # 2. 스트림 핸들러 (이게 있어야 cortex.log에 표준 출력이 담김)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_formatter = logging.Formatter(fmt="[%(name)s] %(message)s")
        stream_handler.setFormatter(stream_formatter)
        root_logger.addHandler(stream_handler)

        root_logger.propagate = False
        _initialized = True

    name = f"{LOGGER_NAME}.{module_name}" if module_name else LOGGER_NAME
    return logging.getLogger(name)
