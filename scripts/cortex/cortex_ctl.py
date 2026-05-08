import os
import sys
import time
import subprocess
import signal
import socket
import struct
import json
import shutil
import threading
import re
from pathlib import Path

# [Safety Net] 가상 환경 실행 여부 검사
def _check_venv():
    """가상 환경(venv) 내부에서 실행 중인지 확인하여 시스템 파이썬 오용 방지"""
    in_venv = hasattr(sys, 'real_prefix') or (sys.base_prefix != sys.prefix)
    if not in_venv:
        print("\n[ERROR] Cortex must be run within the virtual environment.")
        print("💡 Hint: Use 'uv run python scripts/cortex/cortex_ctl.py' or activate .venv first.\n")
        sys.exit(1)

# 실행 시 최우선 검사
_check_venv()

import portalocker  # fcntl 대체 (Windows: msvcrt, Linux: fcntl 자동 선택)
import psutil       # pgrep 대체 (크로스 플랫폼 프로세스 관리)

# 경로 설정
CORTEX_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORTEX_DIR.parent.parent.parent
AGENTS_DIR = PROJECT_ROOT / ".agents"
LOG_DIR = AGENTS_DIR / "history"

# IPC: TCP 소켓 (Unix Domain Socket 대체 — Windows 호환)
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 42384

# uv 실행 경로 탐색
UV_BIN = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")

def _uv_cmd(script: Path) -> list:
    """현재 실행 중인 Python 인터프리터를 사용하여 자식 프로세스 명령어를 생성 (중복 uv 래핑 방지)"""
    return [sys.executable, "-u", str(script)]

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

    # 종료 대상 수집 및 SIGTERM 일괄 발송
    scripts_labels = [(SERVER_SCRIPT, "Engine Server"), (WATCHER_SCRIPT, "Watcher")]
    if LOCAL_DAEMON_SCRIPT:
        scripts_labels.append((LOCAL_DAEMON_SCRIPT, "Local Daemon"))

    all_pids = []
    for script, label in scripts_labels:
        pids = get_pids(str(script))
        if pids:
            for pid in pids:
                logger.info(f"Terminating {label} (PID: {pid})...")
                try:
                    os.kill(pid, signal.SIGTERM)
                    all_pids.append(pid)
                except Exception:
                    pass
        else:
            logger.info(f"{label} is not running.")

    # SIGTERM 발송 후 실제 종료 확인 (최대 10초)
    # 종료 확인 없이 즉시 재시작하면 구 프로세스가 VRAM을 점유한 채로 신 프로세스가 뜨는 중복 점유 발생
    if all_pids:
        # [Root Cause Fix] 공유 예산(10초/N개) → PID별 개별 5초 타임아웃
        # 이전 구조: 6개 PID가 10초 예산을 나눠 써서 후순위 PID가 사실상 0.1초만 받음
        for pid in all_pids:
            try:
                psutil.Process(pid).wait(timeout=5)
                logger.info(f"PID {pid} terminated.")
            except psutil.NoSuchProcess:
                logger.info(f"PID {pid} already gone.")
            except psutil.TimeoutExpired:
                logger.warning(f"PID {pid} did not terminate in time. Force killing...")
                try:
                    psutil.Process(pid).kill()
                    psutil.Process(pid).wait(timeout=3)
                    logger.info(f"PID {pid} force killed.")
                except psutil.NoSuchProcess:
                    logger.info(f"PID {pid} already gone after kill.")
                except psutil.TimeoutExpired:
                    logger.error(f"PID {pid} could not be killed. Port may still be occupied.")
        # CUDA 드라이버 리소스 해제 안정화 대기 (강제 종료 직후 신규 프로세스 CUDA 충돌 방지)
        time.sleep(2)

        # [Root Cause Fix] 포트 해제까지 추가 대기 (TCP TIME_WAIT 대응)
        # kill 후에도 OS 소켓이 TIME_WAIT 상태로 포트를 점유할 수 있어 새 바인딩이 실패함
        TARGET_PORTS = [ENGINE_PORT, 42385]
        deadline = time.time() + 8.0
        while time.time() < deadline:
            occupied = []
            try:
                for conn in psutil.net_connections(kind='tcp'):
                    if conn.laddr.port in TARGET_PORTS and conn.status in ('LISTEN', 'CLOSE_WAIT', 'ESTABLISHED', 'TIME_WAIT'):
                        if conn.pid and conn.pid != os.getpid():
                            occupied.append((conn.laddr.port, conn.pid, conn.status))
            except Exception:
                pass
            if not occupied:
                break
            logger.warning(f"포트 아직 점유 중: {occupied}. 재확인 대기...")
            time.sleep(1.0)

    # [Failsafe A] get_pids()가 놓친 좀비 프로세스를 포트 점유 여부로 이중 확인
    # LISTEN뿐 아니라 CLOSE_WAIT/ESTABLISHED 상태도 포함 (비LISTEN 좀비 탈출 방지)
    try:
        TARGET_PORTS = [ENGINE_PORT, 42385]
        for conn in psutil.net_connections(kind='tcp'):
            if conn.laddr.port in TARGET_PORTS and conn.pid and conn.pid != os.getpid() \
                    and conn.status in ('LISTEN', 'CLOSE_WAIT', 'ESTABLISHED'):
                logger.warning(f"Port {conn.laddr.port} still occupied by PID {conn.pid}. Force killing...")
                try:
                    p = psutil.Process(conn.pid)
                    p.kill()
                    p.wait(timeout=3)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception as e:
        logger.debug(f"Port cleanup exception (non-critical): {e}")
        pass

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

# 로그 타임스탬프 및 레벨 패턴 (릴레이 시 중복 제거용)
# 예: [2026-05-04 17:21:15] [cortex.server] [INFO]
LOG_CLEAN_PATTERN = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[[^\]]+\] \[[A-Z]+\]\s*")

def _relay_subprocess_output(proc, label):
    """서브프로세스의 stdout/stderr를 부모 로거로 전달 (중복 타임스탬프 제거 및 로테이션 유지)"""
    try:
        for line in iter(proc.stdout.readline, b""):
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                # 이미 타임스탬프와 레벨이 포함된 로그라면 해당 부분 제거
                clean_msg = LOG_CLEAN_PATTERN.sub("", msg)
                # 부모의 로거가 최종적으로 하나의 타임스탬프와 [label]을 붙임
                logger.info(f"[{label}] {clean_msg}")
    except Exception:
        pass

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
        
        # 공통 환경변수
        sub_env = os.environ.copy()
        # [Centralized Logging] 서버 프로세스가 메인 데몬으로서 파일에 직접 기록
        if "CORTEX_NO_FILE_LOG" in sub_env:
            del sub_env["CORTEX_NO_FILE_LOG"]
        sub_env["PYTHONUNBUFFERED"] = "1"

        # 1. Engine Server 가동 (백그라운드 독립)
        logger.info("Launching GPU Engine Server...")
        server_proc = subprocess.Popen(
            _uv_cmd(SERVER_SCRIPT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=sub_env,
            start_new_session=True
        )


        # [Failsafe B] 프로세스 즉시 종료 감지 (포트 충돌·import 오류 등 조용한 실패 방지)
        # Router가 포트 bind 재시도(최대 20초)를 수용하도록 5초 대기
        time.sleep(5)
        if server_proc.poll() is not None:
            logger.error(f"CRITICAL: Engine Server exited immediately (code={server_proc.returncode}). Port conflict or startup error.")
            return

        # 2. 서버 대기 (TCP Ping 확인)
        logger.info("Waiting for Engine Server to initialize GPU...")
        retry = 0
        max_retries = 35
        ready = False
        while retry < max_retries:
            # 재시도 루프 중 서버 프로세스 사망 감지 (Failsafe B가 놓친 지연 크래시 포착)
            if server_proc.poll() is not None:
                logger.error(f"CRITICAL: Engine Server crashed during startup (code={server_proc.returncode}).")
                return
            if _send_minimal_ping():
                ready = True
                break
            if retry > 0 and retry % 5 == 0:
                logger.warning(f"Engine Server not ready yet (retry {retry}/{max_retries})...")
            time.sleep(1)
            retry += 1

        if not ready:
            logger.error("CRITICAL: Engine Server failed to start. Check cortex.log.")
            return

        logger.info("Engine Server is Ready (GPU Shared Mode).")

        logger.info("Cortex services started successfully.")
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
