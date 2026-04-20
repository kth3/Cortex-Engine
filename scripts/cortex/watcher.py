import os
import sys
import time
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 프로젝트 루트 및 스크립트 경로 설정
CORTEX_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = str(CORTEX_DIR.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.logger import get_logger

WORKSPACE = CORTEX_DIR.parent.parent.parent
logger = get_logger("watcher")

# ---------------------------------------------------------
# Resident Engine Initialization
# ---------------------------------------------------------
# indexer를 모듈 레벨에서 로드하여 cold start 방지 및 부팅 로그 확인
indexer_path = Path(__file__).resolve().parent / "indexer.py"
scripts_dir = str(indexer_path.parent.parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import traceback
try:
    from cortex import indexer as pc_indexer
    from cortex.vectorizer import detect_gpu
    
    # [Resident Strategy] 이제 모델을 직접 로드하지 않고, 통합 엔진 서버를 활용합니다.
    # 데몬은 가벼운 감찰형 프로세스로 상주합니다.
except ImportError:
    # 런타임에 path를 다시 잡아야 할 수도 있음 (venv 환경 등)
    pc_indexer = None
    detect_gpu = lambda: "unknown"

def print_ready_banner():
    is_gpu = detect_gpu()
    hw_str = "GPU (Accelerated)" if is_gpu else "CPU (Standard)"
    banner = f"""
================================================
🚀 [SYSTEM READY] Cortex Unified Daemon Active
------------------------------------------------
- Mode: Shared Engine Client (Low Overhead)
- Hardware: GPU (Shared via Engine Server)
- Workspace: {WORKSPACE}
- Monitoring: Active (5s debounce)
================================================
"""
    for line in banner.strip().split('\n'):
        logger.info(line)

class DebouncedIndexer(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.changed_files = set()
        self.last_event_time = 0

    def _is_valid_file(self, path_str):
        # 1. 절대 무시 (무한 루프 및 불필요 파일 방지)
        blacklist = ['.git', 'node_modules', '__pycache__', 'venv', '.venv', '.agents/data/', '.agents/history/', '.agents/artifacts/']
        if any(x in path_str for x in blacklist):
            return False

        # 2. 에이전트 내부 선별 감시 - 지식/규칙 폴더 혹은 .agents 루트의 핵심 파일들
        if '.agents/' in path_str:
            return any(x in path_str for x in ['/rules/', '/knowledge/', '/skills/', '/docs/'])

        # 3. 그 외 프로젝트 폴더 및 예제 소스 (자동 감시) - 확장자 기반
        allowed_exts = ['.py', '.md', '.txt', '.js', '.ts', '.json', '.pdf']
        return any(path_str.endswith(ext) for ext in allowed_exts)

    def on_modified(self, event):
        if event.is_directory:
            return
        self.handle_event(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self.handle_event(event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        self.handle_event(event.src_path)

    def handle_event(self, src_path):
        rel_path = os.path.relpath(src_path, str(WORKSPACE))
        if self._is_valid_file(rel_path):
            self.changed_files.add(rel_path)
            self.last_event_time = time.time()

    def process_queue(self):
        # 5초 디바운싱
        now = time.time()
        if self.changed_files and (now - self.last_event_time) >= 5.0:
            files_to_index = list(self.changed_files)
            self.changed_files.clear()
            
            logger.info(f"Debounce triggered. Indexing {len(files_to_index)} files directly in-process...")
            for f in files_to_index:
                try:
                    logger.info(f"  -> Indexing: {f}")
                    # [Unified Policy] 이제 데몬이 직접 CPU를 쓰지 않고, 
                    # 이미 GPU에 상주 중인 통합 엔진 서버에게 요청을 보냅니다.
                    # 서버가 없을 경우에만 자동 fallback 처리됩니다.
                    result = pc_indexer.index_file(str(WORKSPACE), f)
                    if isinstance(result, dict) and "error" in result:
                        logger.warning(f"     [FAIL] {f}: {result['error']}")
                    elif isinstance(result, dict) and result.get("status") == "deleted":
                        logger.info(f"     [DELETED] {f}")
                    elif isinstance(result, dict) and result.get("status") == "created":
                        logger.info(f"     [CREATED] {f}")
                    elif isinstance(result, dict) and result.get("status") == "updated":
                        logger.info(f"     [UPDATED] {f}")
                    else:
                        logger.info(f"     [OK] {f}")
                except Exception as e:
                    err_trace = traceback.format_exc()
                    logger.error(f"     [ERROR] {f}: {str(e)}\n{err_trace}")

def main():
    event_handler = DebouncedIndexer()
    observer = Observer()
    observer.schedule(event_handler, str(WORKSPACE), recursive=True)
    # 부팅 배너 출력 (모델 로딩 유도)
    print_ready_banner()
    
    observer.start()
    try:
        while True:
            time.sleep(1)
            event_handler.process_queue()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
