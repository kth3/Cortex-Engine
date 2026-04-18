"""
Cortex 통합 로거 (v1.0)
모든 Cortex 모듈이 공유하는 표준 로거 설정.
sys.stderr.write / print 혼용을 방지하고 포맷을 통일한다.
"""
import logging
import sys

# 공유 로거 이름 (모든 cortex.* 모듈이 이 이름의 자식 로거를 사용)
LOGGER_NAME = "cortex"

_initialized = False


def get_logger(module_name: str = None) -> logging.Logger:
    """표준 포맷의 Cortex 로거를 반환.

    Usage:
        from cortex.logger import get_logger
        log = get_logger("indexer")
        log.info("Indexing started")
        log.warning("Skipped file: %s", path)
        log.error("Failed: %s", err)
    """
    global _initialized

    if not _initialized:
        root_logger = logging.getLogger(LOGGER_NAME)
        root_logger.setLevel(logging.DEBUG)

        # stderr 핸들러 (기존 동작과 호환)
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt="[%(name)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

        # 부모 로거 전파 방지 (중복 출력 차단)
        root_logger.propagate = False
        _initialized = True

    name = f"{LOGGER_NAME}.{module_name}" if module_name else LOGGER_NAME
    return logging.getLogger(name)
