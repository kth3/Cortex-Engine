import json
import os
import sys
from datetime import datetime, timedelta

# 경로 설정
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "state", "board.json")

def load_board():
    if not os.path.exists(STATE_FILE):
        return {
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lanes": {
                "default": {
                    "status": "IDLE",
                    "active_agent_id": None,
                    "current_task": None,
                    "phase": "READY",
                    "handoff_to": None,
                    "handoff_message": None,
                    "contract_id": None
                }
            }
        }
    with open(STATE_FILE, "r") as f:
        board = json.load(f)
        # 하위 호환성 및 스키마 확장 지원
        if "lanes" not in board:
            # 기존 데이터를 default 레인으로 이전
            old_data = {
                "status": board.get("status", "IDLE"),
                "active_agent_id": board.get("active_agent"),
                "current_task": board.get("current_task"),
                "phase": board.get("phase", "READY"),
                "handoff_to": board.get("handoff_to"),
                "handoff_message": board.get("handoff_message"),
                "contract_id": board.get("contract_id")
            }
            board = {"updated_at": board.get("updated_at"), "lanes": {"default": old_data}}
        return board

def save_board(data):
    data["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def status(lane_id=None):
    board = load_board()
    lanes = board["lanes"]
    
    print("\n=== AGENT RELAY BOARD (Multi-Lane) ===")
    target_lanes = [lane_id] if lane_id and lane_id in lanes else lanes.keys()
    
    for lid in target_lanes:
        l = lanes[lid]
        print(f"[{lid.upper()} LANE]")
        print(f"  Status:   {l['status']}")
        print(f"  AgentID:  {l['active_agent_id'] or 'None'}")
        print(f"  Task:     {l['current_task'] or 'None'}")
        print(f"  Phase:    {l['phase']}")
        if l.get("handoff_to"):
            print(f"  Next:     {l['handoff_to']}")
        if l.get("contract_id"):
            print(f"  Contract: {l['contract_id']}")
        if l.get("handoff_message"):
            print(f"  Message:  \"{l['handoff_message']}\"")
        
        # Zombie lock check
        if l["status"] == "BUSY":
            updated_at = datetime.strptime(board["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
            if datetime.utcnow() - updated_at > timedelta(hours=2):
                print(f"  [WARNING] Potential Zombie Lock detected!")
        print("-" * 30)
    print(f"Updated:  {board['updated_at']}\n")

def acquire(agent_id, task, lane_id="default"):
    board = load_board()
    if lane_id not in board["lanes"]:
        board["lanes"][lane_id] = {
            "status": "IDLE", "active_agent_id": None, "current_task": None, 
            "phase": "READY", "handoff_to": None, "handoff_message": None, "contract_id": None
        }
    
    lane = board["lanes"][lane_id]
    if lane["status"] != "IDLE" and lane["active_agent_id"] != agent_id:
        print(f"[CONFLICT] Lane '{lane_id}' is occupied by {lane['active_agent_id']} working on '{lane['current_task']}'.")
        sys.exit(1)
    
    lane["active_agent_id"] = agent_id
    lane["current_task"] = task
    lane["status"] = "BUSY"
    lane["handoff_message"] = None
    save_board(board)
    print(f"[LOCKED] Agent '{agent_id}' acquired lane '{lane_id}' for task '{task}'.")

def release(agent_id, lane_id="default", handoff_to=None, message=None, contract_id=None, phase="DONE"):
    board = load_board()
    if lane_id not in board["lanes"] or board["lanes"][lane_id]["active_agent_id"] != agent_id:
        print(f"[ERROR] Agent '{agent_id}' does not hold the lock for lane '{lane_id}'.")
        sys.exit(1)
    
    lane = board["lanes"][lane_id]
    
    # 메시지 길이 제한
    if message and len(message) > 250:
        message = message[:247] + "..."
    
    lane["status"] = "IDLE" if not handoff_to else "HANDOFF"
    lane["phase"] = phase
    lane["handoff_to"] = handoff_to
    lane["handoff_message"] = message
    lane["contract_id"] = contract_id
    
    if not handoff_to:
        lane["active_agent_id"] = None
        lane["current_task"] = None
    else:
        # Handoff 시에도 active_agent_id는 해제 (다음 에이전트가 acquire 하도록)
        lane["active_agent_id"] = None

    save_board(board)
    print(f"[RELEASED] Agent '{agent_id}' finished task on lane '{lane_id}'. Next: {handoff_to or 'NONE'}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python relay.py [status|acquire|release] ...")
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
