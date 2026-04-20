import os
import sys
import time
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging
from logging.handlers import RotatingFileHandler

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent

log_file = WORKSPACE / ".agents" / "history" / "watcher.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cortex_watcher")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

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
            
            indexer_path = Path(__file__).resolve().parent / "indexer.py"
            scripts_dir = str(indexer_path.parent.parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            
            import traceback
            from cortex import indexer as pc_indexer
            
            logger.info(f"Debounce triggered. Indexing {len(files_to_index)} files directly in-process...")
            for f in files_to_index:
                try:
                    logger.info(f"  -> Indexing: {f}")
                    # 콜드 스타트 제거: subprocess 대신 현재 메모리(System RAM)에 상주하는 엔진 직접 호출
                    result = pc_indexer.index_file(str(WORKSPACE), f)
                    if isinstance(result, dict) and "error" in result:
                        logger.warning(f"     [FAIL] {f}: {result['error']}")
                    else:
                        logger.info(f"     [OK] {f}")
                except Exception as e:
                    err_trace = traceback.format_exc()
                    logger.error(f"     [ERROR] {f}: {str(e)}\n{err_trace}")

def main():
    event_handler = DebouncedIndexer()
    observer = Observer()
    observer.schedule(event_handler, str(WORKSPACE), recursive=True)
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
