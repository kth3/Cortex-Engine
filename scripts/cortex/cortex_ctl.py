import os
import sys
import time
import subprocess
import signal
import socket
import struct
import json
from pathlib import Path

# 경로 설정
CORTEX_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORTEX_DIR.parent.parent.parent
VENV_PYTHON = PROJECT_ROOT / ".agents" / "venv" / "bin" / "python3"
LOG_DIR = PROJECT_ROOT / ".agents" / "history"
SOCKET_PATH = "/tmp/cortex.sock"

# 중앙 로거 가져오기
sys.path.append(str(CORTEX_DIR))
from logger import get_logger
logger = get_logger("ctl")

# 제어 대상 스크립트
SERVER_SCRIPT = CORTEX_DIR / "vector_engine_server.py"
WATCHER_SCRIPT = CORTEX_DIR / "watcher.py"

def _send_minimal_ping() -> bool:
    """엔진 서버에 최소한의 핑을 보내 살아있는지 확인"""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(SOCKET_PATH)
        
        # 'ping' 명령 전송
        data = json.dumps({"command": "ping"}).encode("utf-8")
        client.sendall(struct.pack("!I", len(data)) + data)
        
        # 헤더 수신 (4바이트)
        header = client.recv(4)
        if not header: return False
        
        # 응답 바디 수신
        size = struct.unpack("!I", header)[0]
        resp = client.recv(size).decode("utf-8")
        return json.loads(resp).get("status") == "ok"
    except:
        return False
    finally:
        try: client.close()
        except: pass

def get_pids(script_name: str):
    """실행 중인 스크립트의 PID 목록을 반환"""
    try:
        output = subprocess.check_output(["pgrep", "-f", script_name]).decode().strip()
        return [int(pid) for pid in output.split()]
    except subprocess.CalledProcessError:
        return []

def stop():
    logger.info("Stopping all Cortex services...")
    
    # 1. Watcher 종료
    pids = get_pids(str(WATCHER_SCRIPT))
    if pids:
        for pid in pids:
            logger.info(f"Terminating Watcher (PID: {pid})...")
            try: os.kill(pid, signal.SIGTERM)
            except: pass
    else:
        logger.info("Watcher is not running.")
    
    # 2. Server 종료
    pids = get_pids(str(SERVER_SCRIPT))
    if pids:
        for pid in pids:
            logger.info(f"Terminating Engine Server (PID: {pid})...")
            try: os.kill(pid, signal.SIGTERM)
            except: pass
    else:
        logger.info("Engine Server is not running.")
    
    # 3. 인프라 파일 정리 (소켓 및 유령 로그)
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
        logger.info(f"Cleaned IPC Socket: {SOCKET_PATH}")

    # [CLEANUP] 유령 로그 파일 삭제 (사용자 요청 반영)
    phantom_logs = ["watcher.log", "watcher_output.log", "engine_server.log"]
    for vlog in phantom_logs:
        target = LOG_DIR / vlog
        if target.exists():
            target.unlink()
            logger.info(f"Infrastructure Cleaned: Removed {vlog}")
        else:
            # 존재하지 않아도 기록하여 투명성 보장
            logger.info(f"Infrastructure Sync: {vlog} is already clean.")

    logger.info("All services stop/cleanup sequence complete.")

def start():
    # 로그 디렉토리 준비
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 먼저 종료 및 청소 (중복 방지 및 유령 로그 제거)
    stop()
    
    logger.info("Starting Unified Cortex Services...")
    
    # 1. Engine Server 가동 (이제 파일 리다이렉션 없이 실행, 자체가 logger.py 사용)
    logger.info("Launching GPU Engine Server...")
    subprocess.Popen(
        [str(VENV_PYTHON), str(SERVER_SCRIPT)],
        preexec_fn=os.setpgrp
    )
    
    # 2. 서버 대기 (소켓 생성 확인)
    logger.info("Waiting for Engine Server to initialize GPU...")
    retry = 0
    max_retries = 35 # 모델 로딩 시간을 고려하여 넉넉히 설정
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
        [str(VENV_PYTHON), str(WATCHER_SCRIPT)],
        preexec_fn=os.setpgrp
    )
    
    logger.info("Cortex services started successfully. All logs unified in cortex.log (1MB rotate).")

def status():
    server_pids = get_pids(str(SERVER_SCRIPT))
    watcher_pids = get_pids(str(WATCHER_SCRIPT))
    
    print("\n--- Cortex Status Report (Resident Mode) ---")
    print(f"Engine Server : {'RUNNING' if server_pids else 'STOPPED'} (PIDs: {server_pids})")
    print(f"Watcher Daemon: {'RUNNING' if watcher_pids else 'STOPPED'} (PIDs: {watcher_pids})")
    
    if os.path.exists(SOCKET_PATH):
        print(f"IPC Socket    : [OK] {SOCKET_PATH}")
    else:
        print(f"IPC Socket    : [MISSING]")
        
    print(f"Log Path      : {LOG_DIR}/cortex.log (1MB Auto-Rotate)")
    print("--------------------------------------------\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 cortex_ctl.py [start|stop|status]")
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
