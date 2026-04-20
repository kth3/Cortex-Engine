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
from logger import get_logger

logger = get_logger("server")
SOCKET_PATH = "/tmp/cortex.sock"

def start_server():
    # 기존 소켓 파일 정리
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    # 모델 강제 GPU 로드 (서비스의 핵심)
    try:
        logger.info("Initializing GPU Engine...")
        model = _load_model(device="cuda")
        logger.info("GPU Engine Ready.")
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
            conn, _ = server.accept()
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
                    response = {"status": "ok", "message": "Cortex Engine is alive (GPU)"}
                elif cmd == "embed":
                    texts = request.get("texts", [])
                    if not texts:
                        response = {"status": "ok", "embeddings": []}
                    else:
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
