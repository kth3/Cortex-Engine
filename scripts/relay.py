import json
import os
import sys
import portalocker
from datetime import datetime, timedelta

# 경로 설정
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "state", "board.json")

# 좀비 락 자동 탈취 기준 시간 (초)
ZOMBIE_LOCK_THRESHOLD_SECONDS = 2 * 60 * 60  # 2시간


_DEFAULT_LANE = {
    "status": "IDLE",
    "active_agent_id": None,
    "current_task": None,
    "phase": "READY",
    "handoff_to": None,
    "handoff_message": None,
    "contract_id": None,
    "locked_at": None  # 락 획득 시각 (좀비 감지용)
}

def _default_board():
    """빈 보드 기본 스키마"""
    return {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lanes": {
            "default": dict(_DEFAULT_LANE)
        }
    }

def _ensure_dir():
    """STATE_FILE 상위 디렉토리 보장 (FileNotFoundError 방지)"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

def _locked_transaction(fn):
    """portalocker 배타적 락으로 board.json 읽기→수정→쓰기를 원자적으로 수행하는 래퍼.
    
    fn(board) -> board 를 받아 수정된 보드를 반환하면, 락 안에서 안전하게 저장한다.
    fn이 None을 반환하면 쓰기를 생략한다 (읽기 전용).
    """
    _ensure_dir()
    
    # 원자적 파일 생성: O_CREAT | O_RDWR (O_TRUNC 없음 → 기존 내용 보존)
    # 단일 syscall이므로 TOCTOU Race Condition 없음
    fd = os.open(STATE_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    os.close(fd)
    
    with open(STATE_FILE, "r+", encoding="utf-8") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
        try:
            # 락 획득 후 파일이 비어있는지 확인 (원자적 초기화)
            f.seek(0, 2)
            if f.tell() == 0:
                f.seek(0)
                json.dump(_default_board(), f, indent=2)
                f.flush()
            
            f.seek(0)
            board = json.load(f)
            # 하위 호환성: lanes 키 없는 구 버전 마이그레이션
            if "lanes" not in board:
                old_data = {
                    "status": board.get("status", "IDLE"),
                    "active_agent_id": board.get("active_agent"),
                    "current_task": board.get("current_task"),
                    "phase": board.get("phase", "READY"),
                    "handoff_to": board.get("handoff_to"),
                    "handoff_message": board.get("handoff_message"),
                    "contract_id": board.get("contract_id"),
                    "locked_at": None
                }
                board = {"updated_at": board.get("updated_at"), "lanes": {"default": old_data}}
            
            result = fn(board)
            
            if result is not None:
                result["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                f.seek(0)
                json.dump(result, f, indent=2)
                f.truncate()
                return result
            return board
        finally:
            portalocker.unlock(f)

def _is_zombie(lane, updated_at_str):
    """레인이 좀비 상태인지 판별 (locked_at 또는 updated_at 기준)"""
    if lane.get("status") != "BUSY":
        return False
    
    # locked_at이 있으면 그것을 기준으로, 없으면 updated_at 사용
    ts_str = lane.get("locked_at") or updated_at_str
    if not ts_str:
        return False
    
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        return (datetime.utcnow() - ts).total_seconds() > ZOMBIE_LOCK_THRESHOLD_SECONDS
    except (ValueError, TypeError):
        return False

def _auto_evict_zombie(board, lane_id, lane):
    """좀비 락 자동 탈취: BUSY → IDLE 전환 및 경고 출력"""
    old_agent = lane.get("active_agent_id", "unknown")
    old_task = lane.get("current_task", "unknown")
    lane["status"] = "IDLE"
    lane["active_agent_id"] = None
    lane["current_task"] = None
    lane["phase"] = "ZOMBIE_EVICTED"
    lane["handoff_to"] = None
    lane["handoff_message"] = f"Auto-evicted zombie lock (was: {old_agent} on '{old_task}')"
    lane["locked_at"] = None
    print(f"[ZOMBIE-EVICT] Lane '{lane_id}' auto-released: agent '{old_agent}' exceeded {ZOMBIE_LOCK_THRESHOLD_SECONDS // 3600}h timeout.")

def status(lane_id=None):
    def _read(board):
        lanes = board["lanes"]
        
        print("\n=== AGENT RELAY BOARD (Multi-Lane) ===")
        target_lanes = [lane_id] if lane_id and lane_id in lanes else lanes.keys()
        
        for lid in target_lanes:
            l = lanes[lid]
            print(f"[{lid.upper()} LANE]")
            print(f"  Status:   {l['status']}")
            print(f"  AgentID:  {l.get('active_agent_id') or 'None'}")
            print(f"  Task:     {l.get('current_task') or 'None'}")
            print(f"  Phase:    {l.get('phase', 'READY')}")
            if l.get("handoff_to"):
                print(f"  Next:     {l['handoff_to']}")
            if l.get("contract_id"):
                print(f"  Contract: {l['contract_id']}")
            if l.get("handoff_message"):
                print(f"  Message:  \"{l['handoff_message']}\"")
            if l.get("locked_at"):
                print(f"  Locked:   {l['locked_at']}")
            
            # Zombie lock 경고
            if _is_zombie(l, board.get("updated_at")):
                print(f"  ⚠️ [WARNING] Potential Zombie Lock detected! (>{ZOMBIE_LOCK_THRESHOLD_SECONDS // 3600}h)")
            print("-" * 30)
        print(f"Updated:  {board.get('updated_at', 'N/A')}\n")
        return None  # 읽기 전용 — 쓰기 생략
    
    _locked_transaction(_read)

def acquire(agent_id, task, lane_id="default"):
    def _acquire(board):
        if lane_id not in board["lanes"]:
            board["lanes"][lane_id] = dict(_DEFAULT_LANE)
        
        lane = board["lanes"][lane_id]
        
        # Fix #3: HANDOFF 상태는 별도 처리 — 다음 에이전트가 점유할 수 있도록 허용
        if lane["status"] == "HANDOFF":
            # handoff_to가 지정된 경우 해당 에이전트만 허용, 미지정이면 누구나 허용
            expected = lane.get("handoff_to")
            if expected and expected != agent_id:
                print(f"[CONFLICT] Lane '{lane_id}' is in HANDOFF state waiting for '{expected}', but '{agent_id}' tried to acquire.")
                sys.exit(1)
            # HANDOFF → 신규 acquire 허용 (IDLE로 초기화하여 아래 로직으로 통과)
            lane["status"] = "IDLE"
            lane["handoff_to"] = None
            print(f"[HANDOFF-ACCEPT] Lane '{lane_id}' handoff accepted by '{agent_id}'.")
        elif lane["status"] != "IDLE" and lane.get("active_agent_id") != agent_id:
            # 좀비 락 자동 탈취: 해당 레인이 좀비 상태면 강제 해제 후 진행
            if _is_zombie(lane, board.get("updated_at")):
                _auto_evict_zombie(board, lane_id, lane)
            else:
                print(f"[CONFLICT] Lane '{lane_id}' is occupied by {lane['active_agent_id']} working on '{lane['current_task']}'.")
                sys.exit(1)
        
        lane["active_agent_id"] = agent_id
        lane["current_task"] = task
        lane["status"] = "BUSY"
        lane["handoff_message"] = None
        lane["locked_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[LOCKED] Agent '{agent_id}' acquired lane '{lane_id}' for task '{task}'.")
        if lane.get("contract_id"):
            print(f"[CONTRACT] Previous contract on file: {lane['contract_id']}")
            print(f"           Read it before starting: .agents/artifacts/{lane['contract_id']}")
        return board
    
    _locked_transaction(_acquire)

def release(agent_id, lane_id="default", handoff_to=None, message=None, contract_id=None, phase="DONE"):
    def _release(board):
        if lane_id not in board["lanes"] or board["lanes"][lane_id].get("active_agent_id") != agent_id:
            print(f"[ERROR] Agent '{agent_id}' does not hold the lock for lane '{lane_id}'.")
            sys.exit(1)
        
        lane = board["lanes"][lane_id]
        
        # 메시지 길이 제한
        if message and len(message) > 250:
            msg = message[:247] + "..."
        else:
            msg = message
        
        lane["status"] = "IDLE" if not handoff_to else "HANDOFF"
        lane["phase"] = phase
        lane["handoff_to"] = handoff_to
        lane["handoff_message"] = msg
        lane["contract_id"] = contract_id
        lane["locked_at"] = None
        
        if not handoff_to:
            lane["active_agent_id"] = None
            lane["current_task"] = None
        else:
            # Handoff 시에도 active_agent_id는 해제 (다음 에이전트가 acquire 하도록)
            lane["active_agent_id"] = None
        
        print(f"[RELEASED] Agent '{agent_id}' finished task on lane '{lane_id}'. Next: {handoff_to or 'NONE'}")
        return board
    
    _locked_transaction(_release)

def force_release(lane_id="default"):
    """CLI --force 옵션: 레인의 락을 강제로 해제"""
    def _force(board):
        if lane_id not in board["lanes"]:
            print(f"[ERROR] Lane '{lane_id}' does not exist.")
            return None
        
        lane = board["lanes"][lane_id]
        old_agent = lane.get("active_agent_id", "unknown")
        lane["status"] = "IDLE"
        lane["active_agent_id"] = None
        lane["current_task"] = None
        lane["phase"] = "FORCE_RELEASED"
        lane["handoff_to"] = None
        lane["handoff_message"] = f"Force-released by operator (was: {old_agent})"
        lane["locked_at"] = None
        print(f"[FORCE-RELEASED] Lane '{lane_id}' has been forcefully released. (was held by: {old_agent})")
        return board
    
    _locked_transaction(_force)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python relay.py [status|acquire|release|force-release] ...")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        lane = sys.argv[2] if len(sys.argv) > 2 else None
        status(lane)
    elif cmd == "acquire":
        if len(sys.argv) < 4:
            print("Usage: python relay.py acquire [agent_id] [task_name] [lane_id_opt]")
            sys.exit(1)
        lane = sys.argv[4] if len(sys.argv) > 4 else "default"
        acquire(sys.argv[2], sys.argv[3], lane)
    elif cmd == "release":
        if len(sys.argv) < 3:
            print("Usage: python relay.py release [agent_id] [lane_id_opt] [handoff_to_opt] [message_opt] [contract_id_opt]")
            sys.exit(1)
        aid = sys.argv[2]
        lid = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "" else "default"
        hto = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "" else None
        msg = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "" else None
        cid = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "" else None
        release(aid, lid, hto, msg, cid)
    elif cmd == "force-release":
        lane = sys.argv[2] if len(sys.argv) > 2 else "default"
        force_release(lane)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
