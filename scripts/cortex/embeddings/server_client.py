import socket
import json
import struct

# IPC 설정 (Windows/Linux 공용 TCP)
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 42384

def _send_to_server(request: dict, retries: int = 15) -> dict:
    """엔진 서버에 요청을 보내고 응답을 받는다 (TCP)."""
    import time
    for i in range(retries):
        client = None
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(10.0)
            client.connect((ENGINE_HOST, ENGINE_PORT))
            
            data = json.dumps(request).encode("utf-8")
            client.sendall(struct.pack("!I", len(data)) + data)
            
            header = client.recv(4)
            if not header:
                return {"status": "error", "message": "Empty response"}
            size = struct.unpack("!I", header)[0]
            
            resp_data = b""
            while len(resp_data) < size:
                chunk = client.recv(min(size - len(resp_data), 4096))
                if not chunk: break
                resp_data += chunk
            
            return json.loads(resp_data.decode("utf-8"))
        except (ConnectionRefusedError, socket.timeout):
            if i < retries - 1:
                time.sleep(1.0)
                continue
            return {"status": "offline"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            if client is not None:
                client.close()
    return {"status": "error", "message": "Max retries exceeded"}
