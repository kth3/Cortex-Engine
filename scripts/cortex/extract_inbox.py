#!/usr/bin/env python3
"""
Cortex DB에서 수집한 에이전트 관찰(Observations) 내역을 
inbox.md 파일로 포맷팅하여 추출하는 스크립트입니다.
"""
import sys
import os
from pathlib import Path

# Paths
SCRIPTS_DIR = Path(__file__).resolve().parent # .agents/scripts/cortex
WORKSPACE = str(SCRIPTS_DIR.parent.parent.parent) # 프로젝트 루트
INBOX_PATH = os.path.join(WORKSPACE, ".agents", "history", "inbox.md")

# Cortex Modules
sys.path.insert(0, str(SCRIPTS_DIR.parent))
try:
    from cortex.db import get_connection
except ImportError:
    import sqlite3
    def get_connection(workspace):
        db_path = os.path.join(workspace, ".agents", "memories.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

def extract_to_inbox():
    conn = get_connection(WORKSPACE)
    try:
        # 중복 추출 방지를 위한 직전 추출 ID 확인
        last_ext_row = conn.execute("SELECT value FROM meta WHERE key='last_extracted_obs_id'").fetchone()
        last_ext_id = int(last_ext_row[0]) if last_ext_row else 0
        
        # 신규 저장된 관찰 데이터 조회
        new_obs = conn.execute(
            "SELECT id, type, content, session_id FROM observations WHERE id > ? ORDER BY id ASC", 
            (last_ext_id,)
        ).fetchall()
        
        if not new_obs:
            import sys; sys.stderr.write("[INFO] 추출할 새로운 관찰(Observations) 내용이 없습니다.")
            return

        # 추출 대상 포맷팅
        formatted_lines = []
        max_id = last_ext_id
        for obs in new_obs:
            obs_id, obs_type, content, session_id = obs
            # 개행 제거 및 안전한 내용 처리
            safe_content = content.replace('\n', ' ')
            sess_trunc = session_id[:8] if session_id else "unknown"
            formatted_lines.append(f"- [PENDING] **[{obs_type.upper()}]** {safe_content} (Session: {sess_trunc})")
            max_id = max(max_id, obs_id)
            
        if not os.path.exists(INBOX_PATH):
            import sys; sys.stderr.write(f"[ERROR] Inbox 파일을 찾을 수 없습니다: {INBOX_PATH}")
            return
            
        with open(INBOX_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # '## 대기 중 항목 (Pending)' 헤더 다음 줄에 삽입하기 위한 위치 찾기
        insert_idx = -1
        for i, line in enumerate(lines):
            if "## 대기 중 항목 (Pending)" in line:
                insert_idx = i + 1
                break
                
        if insert_idx == -1:
            # 헤더가 없으면 파일 맨 뒤에 헤더와 함께 추가
            lines.append("\n## 대기 중 항목 (Pending)\n")
            insert_idx = len(lines)
            
        # 개행 구조 유지
        if insert_idx < len(lines) and lines[insert_idx].strip() != "":
            lines.insert(insert_idx, "\n")
            insert_idx += 1
            
        # 역순으로 삽입하여 가장 먼저 들어온 관찰 내용이 위쪽에 오도록 조치
        for line in reversed(formatted_lines):
            lines.insert(insert_idx, line + "\n")
            
        with open(INBOX_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
        # 추출 완료 ID 갱신
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", ("last_extracted_obs_id", str(max_id)))
        conn.commit()
        
        import sys; sys.stderr.write(f"[SUCCESS] {len(new_obs)}개의 신규 인사이트를 inbox.md에 성공적으로 정리했습니다.")
        
    except Exception as e:
        import sys; sys.stderr.write(f"[ERROR] 추출 중 예외가 발생했습니다: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    extract_to_inbox()
