import os
import sys
import time
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
indexer_path = Path(__file__).resolve().parent / "indexer.py"
scripts_dir = str(indexer_path.parent.parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import traceback
try:
    from cortex import indexer as pc_indexer
    from cortex.vectorizer import detect_gpu
except ImportError:
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
- Hardware: {hw_str}
- Workspace: {WORKSPACE}
- Monitoring: Active (5s debounce)
================================================
"""
    for line in banner.strip().split('\n'):
        logger.info(line)

class DebouncedIndexer(FileSystemEventHandler):
    DUPLICATE_DELETE_TTL = 120

    def __init__(self):
        super().__init__()
        self.changed_files = set()
        self.last_event_time = 0
        self._delete_cooldown = {}

    def _is_valid_file(self, path_str):
        blacklist = [
            '.git', 'node_modules', '__pycache__', 'venv', '.venv',
            '.agents/data/', '.agents/history/', '.agents/artifacts/',
            # Unity 내부 캐시 — 인덱싱 불필요
            'Unity/Library/',
            'Unity/Temp/',
            'Unity/Logs/',
            'Unity/obj/',
        ]
        if any(x in path_str for x in blacklist):
            return False

        if '.agents/' in path_str:
            return any(x in path_str for x in ['/rules/', '/knowledge/', '/skills/', '/docs/'])

        allowed_exts = ['.py', '.md', '.txt', '.js', '.ts', '.json', '.pdf', '.cs', '.asset', '.prefab', '.meta', '.inputsettings']
        return any(path_str.endswith(ext) for ext in allowed_exts)

    def on_any_event(self, event):
        """Atomic Save 대응을 위해 모든 이벤트를 수신"""
        if event.is_directory:
            return
        self.handle_event(event.src_path)

    def handle_event(self, src_path):
        rel_path = os.path.relpath(src_path, str(WORKSPACE))
        if not self._is_valid_file(rel_path):
            return

        abs_path = os.path.join(str(WORKSPACE), rel_path)
        if not os.path.exists(abs_path):
            last_ts = self._delete_cooldown.get(rel_path)
            if last_ts and (time.time() - last_ts) < self.DUPLICATE_DELETE_TTL:
                return
        else:
            self._delete_cooldown.pop(rel_path, None)

        self.changed_files.add(rel_path)
        self.last_event_time = time.time()

    def process_queue(self):
        now = time.time()
        if not self.changed_files or (now - self.last_event_time) < 5.0:
            return

        actual_changes = list(self.changed_files)
        self.changed_files.clear()

        skipped_count = 0
        indexed = []
        for f in actual_changes:
            # .meta나 .inputsettings는 인덱싱에서 제외
            if f.endswith(('.meta', '.inputsettings')):
                skipped_count += 1
                continue
            try:
                start_t = time.time()
                result = pc_indexer.index_file(str(WORKSPACE), f)
                elapsed = (time.time() - start_t) * 1000

                if isinstance(result, dict) and "error" in result:
                    logger.warning(f"     [FAIL] {f}: {result['error']}")
                else:
                    status = result.get("status", "ok").upper()
                    chunks = result.get("chunks", 0)
                    if status == "SKIPPED":
                        skipped_count += 1
                    else:
                        indexed.append((status, f, chunks, elapsed))
                        if status == "DELETED":
                            self._delete_cooldown[f] = time.time()
            except Exception as e:
                logger.error(f"     [ERROR] {f}: {str(e)}")

        if indexed:
            logger.info(f"Debounce triggered. {len(indexed)} indexed, {skipped_count} skipped.")
            for status, f, chunks, elapsed in indexed:
                logger.info(f"     [{status}] {f} ({chunks} chunks, {elapsed:.1f}ms)")
            logger.info("✅ [ALL UPDATES SYNCED] Batch complete.")
            logger.info("================================================")

from dotenv import load_dotenv

def main():
    env_file = WORKSPACE / ".agents" / ".env"
    if env_file.exists():
        load_dotenv(str(env_file))

    event_handler = DebouncedIndexer()
    observer = Observer()
    observer.schedule(event_handler, str(WORKSPACE), recursive=True)

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
