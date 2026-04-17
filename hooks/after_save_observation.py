#!/usr/bin/env python3
"""
after_save_observation.py - 관찰 저장 직후 실행되는 훅
DB에 저장된 최신 관찰 내용을 inbox.md로 즉시 추출합니다.
(기존 extract_inbox.py의 로직을 훅으로 전환)
"""
import sys
import os
import sqlite3
from pathlib import Path

def run():
    # 경로 설정
    HOOKS_DIR = Path(__file__).resolve().parent
    WORKSPACE = str(HOOKS_DIR.parent.parent)
    DB_PATH = os.path.join(WORKSPACE, ".agents", "memories.db")
    INBOX_PATH = os.path.join(WORKSPACE, ".agents", "history", "inbox.md")

    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    try:
        # 1. 마지막 추출 ID 확인
        cursor = conn.execute("SELECT value FROM meta WHERE key='last_extracted_obs_id'")
        row = cursor.fetchone()
        last_id = int(row[0]) if row else 0
        
        # 2. 신규 데이터 조회
        new_obs = conn.execute(
            "SELECT id, type, content FROM observations WHERE id > ? ORDER BY id ASC", 
            (last_id,)
        ).fetchall()
        
        if not new_obs:
            return

        # 3. 포맷팅 및 파일 쓰기
        with open(INBOX_PATH, 'a', encoding='utf-8') as f:
            for obs in new_obs:
                f.write(f"- [PENDING] **[{obs['type'].upper()}]** {obs['content'].replace(chr(10), ' ')}\n")
        
        # 4. ID 갱신
        max_id = max(o['id'] for o in new_obs)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", ("last_extracted_obs_id", str(max_id)))
        conn.commit()
        print(f"Extracted {len(new_obs)} observations to inbox.md")

    except Exception as e:
        print(f"Error in after_save_observation hook: {str(e)}", file=sys.stderr)
    finally:
        conn.close()

if __name__ == "__main__":
    run()
