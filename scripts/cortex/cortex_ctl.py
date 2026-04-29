import os
import sys
import time
import subprocess
import signal
import socket
import struct
import json
import shutil
from pathlib import Path

import portalocker  # fcntl 대체 (Windows: msvcrt, Linux: fcntl 자동 선택)
import psutil       # pgrep 대체 (크로스 플랫폼 프로세스 관리)

# 경로 설정
CORTEX_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORTEX_DIR.parent.parent.parent
AGENTS_DIR = PROJECT_ROOT / ".agents"
LOG_DIR = AGENTS_DIR / "history"

# IPC: TCP 소켓 (Unix Domain Socket 대체 — Windows 호환)
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 62384

# uv 실행 경로 탐색
UV_BIN = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")

def _uv_cmd(script: Path) -> list:
    """uv run 기반 실행 명령어를 생성"""
    if not os.path.exists(UV_BIN):
        print("\n[ERROR] uv 패키지 관리자를 찾을 수 없습니다.")
        print("Cortex 엔진은 빠른 속도와 안전한 환경 격리를 위해 uv를 사용합니다.")
        print("터미널에 아래 명령어를 입력하여 uv를 먼저 설치해 주세요:")
        print("    curl -LsSf https://astral.sh/uv/install.sh | sh  # Linux/Mac")
        print("    powershell -c 'irm https://astral.sh/uv/install.ps1 | iex'  # Windows\n")
        sys.exit(1)
    return [UV_BIN, "run", "--project", str(AGENTS_DIR), "python", str(script)]

# 중앙 로거 가져오기
sys.path.append(str(CORTEX_DIR.parent))
from cortex.logger import get_logger
logger = get_logger("ctl")

# 제어 대상 스크립트
SERVER_SCRIPT = CORTEX_DIR / "vector_engine_server.py"
WATCHER_SCRIPT = CORTEX_DIR / "watcher.py"
LOCK_FILE = LOG_DIR / "cortex_ctl.lock"

# 사용자 커스텀 데몬 스크립트 파싱 (.env)
LOCAL_DAEMON_SCRIPT = None
env_path = PROJECT_ROOT / ".agents" / ".env"
if env_path.exists():
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CORTEX_LOCAL_DAEMON="):
                    val = line.split("=", 1)[1].strip("'\" ")
                    if os.path.exists(val):
                        LOCAL_DAEMON_SCRIPT = Path(val)
                    break
    except Exception:
        pass

def _send_minimal_ping() -> bool:
    """엔진 서버에 최소한의 핑을 보내 살아있는지 확인 (TCP)"""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect((ENGINE_HOST, ENGINE_PORT))
        data = json.dumps({"command": "ping"}).encode("utf-8")
        client.sendall(struct.pack("!I", len(data)) + data)
        header = client.recv(4)
        if not header:
            return False
        size = struct.unpack("!I", header)[0]
        resp = client.recv(size).decode("utf-8")
        return json.loads(resp).get("status") == "ok"
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass

def get_pids(script_name: str):
    """psutil로 크로스 플랫폼 프로세스 탐색 (pgrep -f 대체)"""
    result = []
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = " ".join(proc.info['cmdline'] or [])
            if script_name in cmdline:
                result.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result

def acquire_lock():
    """하나의 ctl 프로세스만 서버/워처를 제어하도록 파일 락 획득 (portalocker)"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOCK_FILE, "w")
        portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
        return f
    except portalocker.LockException:
        return None
    except (IOError, OSError):
        return None

def release_lock(f):
    if f:
        try:
            portalocker.unlock(f)
            f.close()
        except Exception:
            pass

def _perform_stop():
    """실제 종료 로직 (락 획득 여부와 상관없이 실행 가능)"""
    logger.info("Stopping all Cortex services...")

    # 1. Server 종료
    pids = get_pids(str(SERVER_SCRIPT))
    if pids:
        for pid in pids:
            logger.info(f"Terminating Engine Server (PID: {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    else:
        logger.info("Engine Server is not running.")

    # 2. Watcher 종료
    pids = get_pids(str(WATCHER_SCRIPT))
    if pids:
        for pid in pids:
            logger.info(f"Terminating Watcher (PID: {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    else:
        logger.info("Watcher is not running.")

    # 3. Local Daemon 종료
    if LOCAL_DAEMON_SCRIPT:
        pids = get_pids(str(LOCAL_DAEMON_SCRIPT))
        if pids:
            for pid in pids:
                logger.info(f"Terminating Local Daemon (PID: {pid})...")
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        else:
            logger.info("Local Daemon is not running.")

    # 4. TCP 소켓은 파일이 아니므로 별도 정리 불필요 (SO_REUSEADDR로 즉시 재사용 가능)
    logger.info(f"IPC Endpoint: {ENGINE_HOST}:{ENGINE_PORT} (TCP — no file cleanup needed)")

    # [CLEANUP] 유령 로그 파일 삭제
    phantom_logs = ["watcher_output.log", "engine_server.log"]
    for vlog in phantom_logs:
        target = LOG_DIR / vlog
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
            logger.info(f"Infrastructure Cleaned: Removed {vlog}")

    logger.info("All services stop/cleanup sequence complete.")

def stop():
    lock_f = acquire_lock()
    if not lock_f:
        logger.info("Another control process is running. Skipping stop.")
        return
    try:
        _perform_stop()
    finally:
        release_lock(lock_f)

def start():
    # 로그 디렉토리 준비
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # [Atomic Lock] 중복 기동 방지를 위한 파일 락 획득
    lock_f = acquire_lock()
    if not lock_f:
        # 이미 다른 ctl(예: MCP 자동기동)이 작업 중이면 조용히 종료
        return

    try:
        # 먼저 이미 완벽히 실행 중인지 체크 (중복 실행 방지)
        current_watchers = get_pids(str(WATCHER_SCRIPT))
        current_servers = get_pids(str(SERVER_SCRIPT))

        all_running = bool(current_watchers) and bool(current_servers) and _send_minimal_ping()
        if all_running and LOCAL_DAEMON_SCRIPT:
            all_running = all_running and bool(get_pids(str(LOCAL_DAEMON_SCRIPT)))

        if all_running:
            # 이미 모든 서비스가 정상 가동 중이면 종료
            return

        # 기동 전 청소 (기존 프로세스 및 파일 정리)
        _perform_stop()

        logger.info("Starting Unified Cortex Services...")

        # 1. Engine Server 가동
        logger.info("Launching GPU Engine Server...")
        subprocess.Popen(
            _uv_cmd(SERVER_SCRIPT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # 2. 서버 대기 (TCP Ping 확인)
        logger.info("Waiting for Engine Server to initialize GPU...")
        retry = 0
        max_retries = 35
        ready = False
        while retry < max_retries:
            if _send_minimal_ping():
                ready = True
                break
            time.sleep(1)
            retry += 1

        if not ready:
            logger.error("CRITICAL: Engine Server failed to start. Check cortex.log.")
            return

        logger.info("Engine Server is Ready (GPU Shared Mode).")

        # 3. Watcher 가동
        logger.info("Launching Watcher Daemon...")
        subprocess.Popen(
            _uv_cmd(WATCHER_SCRIPT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # 4. Local Daemon 가동
        if LOCAL_DAEMON_SCRIPT:
            logger.info(f"Launching Local Daemon: {LOCAL_DAEMON_SCRIPT.name}...")
            subprocess.Popen(
                _uv_cmd(LOCAL_DAEMON_SCRIPT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

        logger.info("Cortex services started successfully.")
        (LOG_DIR / "last_start.txt").write_text(str(time.time()))
    finally:
        release_lock(lock_f)

def status():
    server_pids = get_pids(str(SERVER_SCRIPT))
    watcher_pids = get_pids(str(WATCHER_SCRIPT))
    ping_ok = _send_minimal_ping()

    print("\n--- Cortex Status Report (Resident Mode) ---")
    print(f"Engine Server : {'RUNNING' if server_pids else 'STOPPED'} (PIDs: {server_pids}) {'[READY]' if ping_ok else '[LOADING/ERROR]'}")
    print(f"Watcher Daemon: {'RUNNING' if watcher_pids else 'STOPPED'} (PIDs: {watcher_pids})")

    if LOCAL_DAEMON_SCRIPT:
        local_pids = get_pids(str(LOCAL_DAEMON_SCRIPT))
        print(f"Local Daemon  : {'RUNNING' if local_pids else 'STOPPED'} (PIDs: {local_pids}) [{LOCAL_DAEMON_SCRIPT.name}]")

    print(f"IPC Endpoint  : {'[OK]' if ping_ok else '[UNREACHABLE]'} {ENGINE_HOST}:{ENGINE_PORT} (TCP)")
    print(f"Log Path      : {LOG_DIR}/cortex.log")
    print("--------------------------------------------\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cortex_ctl.py [start|stop|status]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    else:
        print(f"Unknown command: {cmd}")
