import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

CORTEX_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = str(CORTEX_DIR.parent)

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.logger import get_logger
from cortex.paths import resolve_cortex_home, resolve_workspace
from cortex.watch.filters import is_valid_file, normalize_patterns

logger = get_logger("watcher")

WORKSPACE = resolve_workspace(CORTEX_DIR)
CORTEX_HOME = resolve_cortex_home(WORKSPACE)

try:
    from cortex import indexer as pc_indexer
    from cortex.embeddings.hardware import detect_gpu
except ImportError as e:
    logger.error(f"Critical ImportError in Watcher: {e}")
    traceback.print_exc()
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
    for line in banner.strip().split("\n"):
        logger.info(line)


class DebouncedIndexer(FileSystemEventHandler):
    DUPLICATE_DELETE_TTL = 120

    def __init__(self):
        super().__init__()
        self.changed_files = set()
        self.last_event_time = 0
        self._delete_cooldown = {}

        from cortex.indexer_utils import load_settings

        settings = load_settings(str(WORKSPACE))
        rules = settings.get("indexing_rules", {})
        self._exclude_paths = normalize_patterns(rules.get("exclude_paths", []))

    def _is_valid_file(self, path_str):
        return is_valid_file(path_str, self._exclude_paths)

    def on_any_event(self, event):
        """Atomic Save 대응을 위해 모든 이벤트를 수신"""
        if event.is_directory:
            return

        self.handle_event(event.src_path)

        if hasattr(event, "dest_path") and event.dest_path:
            self.handle_event(event.dest_path)

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
        for file_path in actual_changes:
            if file_path.endswith((".meta", ".inputsettings")):
                skipped_count += 1
                continue

            try:
                start_t = time.time()
                result = pc_indexer.index_file(str(WORKSPACE), file_path)
                elapsed = (time.time() - start_t) * 1000

                if isinstance(result, dict) and "error" in result:
                    logger.warning(f"     [FAIL] {file_path}: {result['error']}")
                else:
                    status = result.get("status", "ok").upper()
                    chunks = result.get("chunks", 0)
                    if status == "SKIPPED":
                        skipped_count += 1
                    else:
                        indexed.append((status, file_path, chunks, elapsed))
                        if status == "DELETED":
                            self._delete_cooldown[file_path] = time.time()
            except Exception as exc:
                logger.error(f"     [ERROR] {file_path}: {str(exc)}")

        logger.info(f"Debounce triggered. {len(indexed)} indexed, {skipped_count} skipped.")
        for status, file_path, chunks, elapsed in indexed:
            logger.info(f"     [{status}] {file_path} ({chunks} chunks, {elapsed:.1f}ms)")
        logger.info("✅ [ALL UPDATES SYNCED] Batch complete.")
        logger.info("================================================")


def main():
    env_file = CORTEX_HOME / ".env"
    if env_file.exists():
        load_dotenv(str(env_file))

    event_handler = DebouncedIndexer()
    observer = Observer()
    observer.schedule(event_handler, str(WORKSPACE), recursive=True)

    print_ready_banner()

    observer.start()
    last_heartbeat = 0
    heartbeat_interval = 60

    try:
        while True:
            time.sleep(1)
            event_handler.process_queue()

            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                logger.info(
                    f"[heartbeat] Watcher alive. Queue: {len(event_handler.changed_files)} pending."
                )
                last_heartbeat = now
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
