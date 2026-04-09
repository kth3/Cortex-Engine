import json
import os
import sys
from datetime import datetime, timedelta

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "state", "board.json")

def load_board():
    if not os.path.exists(STATE_FILE):
        return {"current_task": None, "active_agent": None, "status": "IDLE", "phase": "READY", "handoff_message": None}
    with open(STATE_FILE, "r") as f:
        board = json.load(f)
        # Ensure handoff_message exists in older versions
        if "handoff_message" not in board:
            board["handoff_message"] = None
        return board

def save_board(data):
    data["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def status():
    board = load_board()
    print("\n=== AGENT RELAY BOARD ===")
    print(f"Status:   {board['status']}")
    print(f"Agent:    {board['active_agent'] or 'None'}")
    print(f"Task:     {board['current_task'] or 'None'}")
    print(f"Phase:    {board['phase']}")
    if board.get("handoff_to"):
        print(f"Next:     {board['handoff_to']}")
    if board.get("handoff_message"):
        print(f"Message:  \"{board['handoff_message']}\"")
    print(f"Updated:  {board['updated_at']}")
    print("==========================\n")

    # Zombie lock check (e.g., 2 hours)
    if board["status"] == "BUSY":
        updated_at = datetime.strptime(board["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
        if datetime.utcnow() - updated_at > timedelta(hours=2):
            print(f"[WARNING] Potential Zombie Lock detected! {board['active_agent']} has been busy since {board['updated_at']}.")

def acquire(agent, task):
    board = load_board()
    if board["status"] != "IDLE" and board["active_agent"] != agent:
        print(f"[CONFLICT] {board['active_agent']} is already working on '{board['current_task']}'.")
        sys.exit(1)
    
    board["active_agent"] = agent
    board["current_task"] = task
    board["status"] = "BUSY"
    # Clear previous handoff message when starting new task
    board["handoff_message"] = None
    save_board(board)
    print(f"[LOCKED] {agent} started working on '{task}'.")

def release(agent, handoff=None, message=None, phase="DONE"):
    board = load_board()
    if board["active_agent"] != agent:
        print(f"[ERROR] {agent} does not hold the lock.")
        sys.exit(1)
    
    board["status"] = "IDLE" if not handoff else "HANDOFF"
    board["phase"] = phase
    board["handoff_to"] = handoff
    board["handoff_message"] = message
    
    if not handoff:
        board["active_agent"] = None
        board["current_task"] = None
    else:
        board["active_agent"] = None

    save_board(board)
    print(f"[RELEASED] {agent} finished task. Next expected: {handoff or 'NONE'}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python relay.py [status|acquire|release] ...")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        status()
    elif cmd == "acquire":
        if len(sys.argv) < 4:
            print("Usage: python relay.py acquire [agent_name] [task_name]")
            sys.exit(1)
        acquire(sys.argv[2], sys.argv[3])
    elif cmd == "release":
        if len(sys.argv) < 3:
            print("Usage: python relay.py release [agent_name] [next_agent_optional] [message_optional]")
            sys.exit(1)
        agent = sys.argv[2]
        next_agent = sys.argv[3] if len(sys.argv) > 3 else None
        msg = sys.argv[4] if len(sys.argv) > 4 else None
        release(agent, next_agent, msg)
