#!/usr/bin/env python3
"""
Cortex Local Concurrency Stress Test
=====================================
relay.py의 fcntl 파일 락과 orchestrator.py의 _FileLock이
다중 프로세스 환경에서 Race Condition과 데이터 오염을 방어하는지 검증.

LLM API 호출 없음 — 로컬 디스크 I/O와 락 메커니즘만 테스트합니다.

Usage:
    cd .agents && ./venv/bin/python3 scripts/test_concurrency.py
"""
import json
import os
import sys
import time
import random
import tempfile
import shutil
import traceback
from multiprocessing import Pool

# 프로젝트 scripts/ 경로
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

# ─── 테스트 설정 ───
NUM_WORKERS = 20
CYCLES_PER_WORKER = 50
TOTAL = NUM_WORKERS * CYCLES_PER_WORKER  # 1,000

# ─── 임시 워크스페이스 ───
TMP_DIR = None


def setup_temp():
    global TMP_DIR
    TMP_DIR = tempfile.mkdtemp(prefix="cortex_stress_")
    os.makedirs(os.path.join(TMP_DIR, "state"), exist_ok=True)
    os.makedirs(os.path.join(TMP_DIR, ".agents", "history"), exist_ok=True)
    return TMP_DIR


def cleanup_temp():
    if TMP_DIR and os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)


# ═══════════════════════════════════════════════════════════════
# Scenario A: Relay 관제탑 락 데스매치
# ═══════════════════════════════════════════════════════════════

def _relay_init(state_path):
    """워커 프로세스 초기화: relay.STATE_FILE을 임시 경로로 패치"""
    import relay
    relay.STATE_FILE = state_path


def relay_worker(worker_id):
    """각 워커는 고유 레인에서 acquire→sleep→release를 반복.
    20개 워커가 동일 board.json 파일에 동시 쓰기하여 fcntl 락을 스트레스 테스트.
    """
    import relay

    agent_id = f"agent_{worker_id}"
    lane_id = f"lane_{worker_id}"
    successes = 0
    errors = []

    # relay.py의 print 출력 억제
    devnull = open(os.devnull, "w")

    for i in range(CYCLES_PER_WORKER):
        try:
            old_stdout = sys.stdout
            sys.stdout = devnull

            relay.acquire(agent_id, f"task_{worker_id}_{i}", lane_id)
            sys.stdout = old_stdout

            # 작업 시뮬레이션 (0.005~0.02초)
            time.sleep(random.uniform(0.005, 0.02))

            sys.stdout = devnull
            relay.release(agent_id, lane_id)
            sys.stdout = old_stdout

            successes += 1
        except SystemExit:
            sys.stdout = old_stdout
            errors.append(f"SystemExit@cycle_{i}")
        except Exception as e:
            sys.stdout = old_stdout
            errors.append(f"{type(e).__name__}:{e}")

    devnull.close()
    return {"worker": worker_id, "successes": successes, "errors": errors}


def run_scenario_a():
    print("\n" + "=" * 60)
    print("  SCENARIO A: Relay Lock Deathmatch")
    print(f"  {NUM_WORKERS} workers × {CYCLES_PER_WORKER} cycles = {TOTAL} transactions")
    print("  Target: board.json (fcntl.LOCK_EX)")
    print("=" * 60)

    state_path = os.path.join(TMP_DIR, "state", "board.json")

    start = time.time()
    with Pool(NUM_WORKERS, initializer=_relay_init, initargs=(state_path,)) as pool:
        results = pool.map(relay_worker, range(NUM_WORKERS))
    elapsed = time.time() - start

    total_success = sum(r["successes"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)

    print(f"\n  Time:      {elapsed:.2f}s ({TOTAL / elapsed:.0f} tx/s)")
    print(f"  Successes: {total_success}/{TOTAL}")
    print(f"  Errors:    {total_errors}")

    if total_errors > 0:
        for r in results:
            if r["errors"]:
                print(f"    worker_{r['worker']}: {r['errors'][:3]}")

    # ─── Assertion 1: JSON 문법 무결성 ───
    with open(state_path, "r", encoding="utf-8") as f:
        board = json.load(f)
    print("  ✅ [A1] board.json: valid JSON (no corruption)")

    # ─── Assertion 2: Double Booking 검사 ───
    active_agents = []
    for lane_id, lane in board["lanes"].items():
        if lane.get("active_agent_id"):
            active_agents.append(lane["active_agent_id"])

    assert len(active_agents) == len(set(active_agents)), \
        f"❌ Double booking! Active agents: {active_agents}"
    print("  ✅ [A2] No double-booking detected")

    # ─── Assertion 3: 모든 레인 IDLE (모든 워커가 release 완료) ───
    busy_lanes = [(lid, l["active_agent_id"])
                  for lid, l in board["lanes"].items() if l["status"] != "IDLE"]
    assert len(busy_lanes) == 0, \
        f"❌ Stale locks! Busy lanes: {busy_lanes}"
    print("  ✅ [A3] All lanes IDLE after completion")

    # ─── Assertion 4: 모든 트랜잭션 성공 ───
    assert total_success == TOTAL, \
        f"❌ Expected {TOTAL} successes, got {total_success}"
    print(f"  ✅ [A4] All {TOTAL} transactions completed")

    return True


# ═══════════════════════════════════════════════════════════════
# Scenario B: Todo 오케스트레이터 덮어쓰기 방어
# ═══════════════════════════════════════════════════════════════

def todo_worker(args):
    """워커가 manage_todo("add")를 반복 호출하여 동시 쓰기 테스트"""
    worker_id, workspace = args
    sys.path.insert(0, SCRIPTS_DIR)
    from cortex.orchestrator import manage_todo

    successes = 0
    errors = []

    for i in range(CYCLES_PER_WORKER):
        try:
            result = manage_todo(workspace, "add", task=f"w{worker_id}_i{i}")
            if result and result.get("success"):
                successes += 1
            else:
                errors.append(f"bad_result@{i}: {result}")
        except Exception as e:
            errors.append(f"{type(e).__name__}:{e}")

    return {"worker": worker_id, "successes": successes, "errors": errors}


def run_scenario_b():
    print("\n" + "=" * 60)
    print("  SCENARIO B: Todo Orchestrator Overwrite Defense")
    print(f"  {NUM_WORKERS} workers × {CYCLES_PER_WORKER} adds = {TOTAL} items")
    print("  Target: todo.json (_FileLock: O_CREAT|O_EXCL)")
    print("=" * 60)

    workspace = TMP_DIR
    todo_path = os.path.join(workspace, ".agents", "history", "todo.json")

    start = time.time()
    with Pool(NUM_WORKERS) as pool:
        args = [(i, workspace) for i in range(NUM_WORKERS)]
        results = pool.map(todo_worker, args)
    elapsed = time.time() - start

    total_success = sum(r["successes"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)

    print(f"\n  Time:      {elapsed:.2f}s ({TOTAL / elapsed:.0f} tx/s)")
    print(f"  Successes: {total_success}/{TOTAL}")
    print(f"  Errors:    {total_errors}")

    if total_errors > 0:
        for r in results:
            if r["errors"]:
                print(f"    worker_{r['worker']}: {r['errors'][:3]}")

    # ─── Assertion 1: JSON 문법 무결성 ───
    with open(todo_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("  ✅ [B1] todo.json: valid JSON (no corruption)")

    # ─── Assertion 2: 데이터 유실 방지 (정확히 1,000개) ───
    actual_count = len(data["todos"])
    assert actual_count == TOTAL, \
        f"❌ Expected {TOTAL} todos, got {actual_count} (lost {TOTAL - actual_count})"
    print(f"  ✅ [B2] All {TOTAL} items preserved (zero data loss)")

    # ─── Assertion 3: 모든 워커 항목 존재 확인 ───
    tasks = {t["task"] for t in data["todos"]}
    missing = []
    for w in range(NUM_WORKERS):
        for i in range(CYCLES_PER_WORKER):
            expected = f"w{w}_i{i}"
            if expected not in tasks:
                missing.append(expected)

    assert len(missing) == 0, \
        f"❌ Missing {len(missing)} items: {missing[:5]}..."
    print(f"  ✅ [B3] All worker items verified (complete coverage)")

    return True


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║  Cortex Local Concurrency Stress Test                   ║")
    print("║  fcntl (relay.py) + _FileLock (orchestrator.py)         ║")
    print("║  LLM API: NONE — pure disk I/O only                    ║")
    print("╚══════════════════════════════════════════════════════════╝")

    setup_temp()
    print(f"\n  Temp workspace: {TMP_DIR}")
    print(f"  Config: {NUM_WORKERS} workers × {CYCLES_PER_WORKER} cycles = {TOTAL} total")

    passed = True
    try:
        if not run_scenario_a():
            passed = False
        if not run_scenario_b():
            passed = False
    except AssertionError as e:
        print(f"\n  ❌ ASSERTION FAILED: {e}")
        passed = False
    except Exception as e:
        print(f"\n  ❌ UNEXPECTED ERROR: {e}")
        traceback.print_exc()
        passed = False
    finally:
        cleanup_temp()

    print("\n" + "=" * 60)
    if passed:
        print("  🎉 ALL TESTS PASSED — Concurrency Safety VERIFIED")
    else:
        print("  ❌ SOME TESTS FAILED — Review output above")
    print("=" * 60 + "\n")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
