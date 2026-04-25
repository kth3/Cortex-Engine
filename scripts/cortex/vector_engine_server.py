import os
import sys
import json
import socket
import struct
import numpy as np
from typing import List

# 프로젝트 루트 및 스크립트 경로 설정 (모듈 인식을 위해 최상단에서 수행)
CORTEX_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(CORTEX_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from vector_engine import _load_model
from cortex.logger import get_logger

logger = get_logger("server")
SOCKET_PATH = "/tmp/cortex.sock"

def start_server():
    from vector_engine import release_gpu
    import time

    IDLE_TIMEOUT = 300  # 운영 환경: 5분 유휴 시 VRAM 반환
    last_activity = time.time()
    model_loaded = False
    current_device = "cpu"

    # 기존 소켓 파일 정리
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    # 디바이스 감지 로직
    try:
        import torch
        if torch.cuda.is_available():
            current_device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            current_device = "mps"
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            current_device = "xpu"
            
        # 상시 대기를 위해 시작 시 모델 로드
        model = _load_model(device=current_device)
        model_loaded = True
        last_activity = time.time()
        logger.info(f"Engine Ready on {current_device}.")
    except Exception as e:
        logger.error(f"Critical Error during startup: {e}")
        sys.exit(1)

    # 소켓 서버 생성
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(5)
    
    # 누구나 접근 가능하도록 권한 설정 (사용자 편의성)
    os.chmod(SOCKET_PATH, 0o666)

    sys.stderr.write(f"[cortex-server] [SERVER] Listening on {SOCKET_PATH}\n")

    try:
        while True:
            try:
                server.settimeout(1.0) # 1초마다 루프를 돌아 타임아웃 체크
                conn, _ = server.accept()
                last_activity = time.time() # 요청이 왔으므로 시간 갱신
            except socket.timeout:
                # 유휴 시간 체크
                if model_loaded and (time.time() - last_activity > IDLE_TIMEOUT):
                    logger.info(f"IDLE Timeout ({IDLE_TIMEOUT}s). Releasing VRAM...")
                    release_gpu()
                    model = None
                    model_loaded = False
                    logger.info("VRAM Released. Standing by (Lazy loading enabled).")
                continue

            try:
                # 데이터 수신 (길이 헤더 + 바디)
                header = conn.recv(4)
                if not header:
                    continue
                size = struct.unpack("!I", header)[0]
                
                data = b""
                while len(data) < size:
                    chunk = conn.recv(min(size - len(data), 4096))
                    if not chunk:
                        break
                    data += chunk
                
                request = json.loads(data.decode("utf-8"))
                cmd = request.get("command", "embed")
                
                if cmd == "ping":
                    status_str = "alive" if model_loaded else "standby"
                    response = {"status": "ok", "message": f"Cortex Engine is {status_str}"}
                elif cmd == "embed":
                    texts = request.get("texts", [])
                    if not texts:
                        response = {"status": "ok", "embeddings": []}
                    else:
                        # [Lazy Loading] 모델이 내려가 있다면 다시 로드
                        if not model_loaded:
                            logger.info(f"Request received. Re-loading model on {current_device}...")
                            model = _load_model(device=current_device)
                            model_loaded = True

                        # GPU 연산 수행
                        embeddings = model.encode(
                            texts,
                            batch_size=16,
                            normalize_embeddings=True,
                            show_progress_bar=False,
                        ).tolist()
                        response = {"status": "ok", "embeddings": embeddings}
                else:
                    response = {"status": "error", "message": f"Unknown command: {cmd}"}

                # 결과 전송 (길이 헤더 + 바디)
                resp_data = json.dumps(response).encode("utf-8")
                conn.sendall(struct.pack("!I", len(resp_data)) + resp_data)
                
            except Exception as e:
                err_resp = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                try:
                    conn.sendall(struct.pack("!I", len(err_resp)) + err_resp)
                except:
                    pass
            finally:
                conn.close()
    except KeyboardInterrupt:
        sys.stderr.write("[cortex-server] [SERVER] Shutting down...\n")
    finally:
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

if __name__ == "__main__":
    start_server()
