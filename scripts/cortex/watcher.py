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
    DUPLICATE_DELETE_TTL = 120  # seconds — 이 시간 내 동일 경로 DELETE 중복 무시

    def __init__(self):
        super().__init__()
        self.changed_files = set()
        self.last_event_time = 0
        self._delete_cooldown = {}  # {rel_path: processed_timestamp}

    def _is_valid_file(self, path_str):
        # 1. 절대 무시 (무한 루프 및 불필요 파일 방지)
        blacklist = ['.git', 'node_modules', '__pycache__', 'venv', '.venv', '.agents/data/', '.agents/history/', '.agents/artifacts/']
        if any(x in path_str for x in blacklist):
            return False

        # 2. 에이전트 내부 선별 감시 - 지식/규칙 폴더 혹은 .agents 루트의 핵심 파일들
        if '.agents/' in path_str:
            return any(x in path_str for x in ['/rules/', '/knowledge/', '/skills/', '/docs/'])

        # 3. 그 외 프로젝트 폴더 및 예제 소스 (자동 감시) - 확장자 기반
        allowed_exts = ['.py', '.md', '.txt', '.js', '.ts', '.json', '.pdf', '.cs', '.asset', '.prefab']
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
        if not self._is_valid_file(rel_path):
            return

        abs_path = os.path.join(str(WORKSPACE), rel_path)
        if not os.path.exists(abs_path):
            # DELETE 이벤트 — cooldown 내 중복이면 억제
            last_ts = self._delete_cooldown.get(rel_path)
            if last_ts and (time.time() - last_ts) < self.DUPLICATE_DELETE_TTL:
                return
        else:
            # CREATE/MODIFY 이벤트 — 파일이 다시 생겼으면 cooldown 해제
            self._delete_cooldown.pop(rel_path, None)

        self.changed_files.add(rel_path)
        self.last_event_time = time.time()

    def process_queue(self):
        # 5초 디바운싱
        now = time.time()
        if self.changed_files and (now - self.last_event_time) >= 5.0:
            # 만료된 DELETE cooldown 정리
            self._delete_cooldown = {
                p: ts for p, ts in self._delete_cooldown.items()
                if now - ts < self.DUPLICATE_DELETE_TTL
            }
            files_to_index = list(self.changed_files)
            self.changed_files.clear()
            
            logger.info(f"Debounce triggered. Indexing {len(files_to_index)} files directly in-process...")
            for f in files_to_index:
                try:
                    start_t = time.time()
                    # [Unified Policy] 이제 데몬이 직접 CPU를 쓰지 않고, 
                    # 이미 GPU에 상주 중인 통합 엔진 서버에게 요청을 보냅니다.
                    result = pc_indexer.index_file(str(WORKSPACE), f)
                    elapsed = (time.time() - start_t) * 1000 # ms
                    
                    if isinstance(result, dict) and "error" in result:
                        logger.warning(f"     [FAIL] {f}: {result['error']}")
                    else:
                        status = result.get("status", "ok").upper()
                        chunks = result.get("chunks", 0)
                        logger.info(f"     [{status}] {f} ({chunks} chunks, {elapsed:.1f}ms)")
                        if status == "DELETED":
                            self._delete_cooldown[f] = time.time()
                except Exception as e:
                    err_trace = traceback.format_exc()
                    logger.error(f"     [ERROR] {f}: {str(e)}\n{err_trace}")
            
            logger.info("------------------------------------------------")
            logger.info("✅ [ALL UPDATES SYNCED] Indexing batch complete.")
            logger.info("================================================")

from dotenv import load_dotenv

def main():
    # 절대 경로 대신 WORKSPACE 기준 상대 경로 사용 (Zero Path 원칙 준수)
    env_file = WORKSPACE / ".agents" / ".env"
    if env_file.exists():
        load_dotenv(str(env_file))
        
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
