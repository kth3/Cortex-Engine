#!/usr/bin/env bash
# Cortex 좀비 프로세스·VRAM 누수 진단 (WSL/Linux bash).
#
# cortex-ctl 라이프사이클을 정상 종료/강제 종료 두 시나리오로 돌려
# 자식 프로세스(워커, 워처) 잔존 여부와 VRAM 해제 여부를 보고합니다.
#
# 사용: bash zombie-check.sh
# 요구: PATH에 cortex-ctl. CUDA 환경에서만 nvidia-smi 동작.

set -u

failures=()
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
target_regex="$(printf '%s\n' \
    "$repo_root/scripts/cortex/vector_engine_server.py" \
    "$repo_root/scripts/cortex/watch/daemon.py" \
    "$repo_root/scripts/cortex/runtime/engine_worker.py" |
    sed 's/[.[\*^$()+?{}|]/\\&/g' |
    paste -sd '|' -)"

cortex_ctl() {
    if command -v cortex-ctl >/dev/null 2>&1; then
        cortex-ctl "$@"
    else
        uv run cortex-ctl "$@"
    fi
}

cortex_pids() {
    pgrep -af "$target_regex" || true
}

cortex_pid_count() {
    cortex_pids | wc -l | tr -d ' '
}

vram_used_mib() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d ' '
    else
        echo ""
    fi
}

stage() {
    printf "\n=== %s ===\n" "$1"
}

cortex_ready() {
    status="$(cortex_ctl status 2>&1)"
    printf '%s\n' "$status"
    printf '%s\n' "$status" | grep -Eq 'Engine Server[[:space:]]*:[[:space:]]*RUNNING .*\[READY\]' &&
        printf '%s\n' "$status" | grep -Eq 'IPC Endpoint[[:space:]]*:[[:space:]]*\[OK\]'
}

# 0. 기준선
stage "0. 기준선 (cortex 미기동)"
baseline_count=$(cortex_pid_count)
baseline_vram=$(vram_used_mib)
echo "잔존 cortex 프로세스: $baseline_count"
echo "VRAM 사용량(MiB): ${baseline_vram:-n/a}"
if [ "$baseline_count" -gt 0 ]; then
    echo "[경고] 기준선에 이미 잔존 프로세스. 결과 신뢰도 저하."
fi

# 1. 정상 start → stop
stage "1. cortex-ctl start"
cortex_ctl start || true
sleep 4
if ! cortex_ready; then
    failures+=("start 후 Engine Server READY 상태 확인 실패")
fi
echo "기동된 프로세스:"
cortex_pids
vram_after_start=$(vram_used_mib)
echo "VRAM 사용량(MiB): ${vram_after_start:-n/a}"

stage "2. cortex-ctl stop"
cortex_ctl stop || true
sleep 4
after_stop_count=$(cortex_pid_count)
echo "stop 직후 잔존 프로세스 수: $after_stop_count"
cortex_pids
if [ "$after_stop_count" -gt 0 ]; then
    failures+=("정상 stop 후 잔존 프로세스 ${after_stop_count}개")
fi
vram_after_stop=$(vram_used_mib)
if [ -n "$baseline_vram" ] && [ -n "$vram_after_stop" ]; then
    diff=$((vram_after_stop - baseline_vram))
    echo "VRAM 차이(MiB, 기준선 대비): $diff"
    if [ "$diff" -gt 100 ]; then
        failures+=("정상 stop 후 VRAM ${diff} MiB 누수")
    fi
fi

# 2. 강제 종료 시나리오
stage "3. cortex-ctl start (재기동)"
cortex_ctl start || true
sleep 4
if ! cortex_ready; then
    failures+=("재기동 후 Engine Server READY 상태 확인 실패")
fi
echo "재기동된 프로세스:"
cortex_pids

stage "4. SIGKILL — cortex 자식 프로세스 강제 종료"
worker_pids=$(pgrep -f "$repo_root/scripts/cortex/vector_engine_server.py|$repo_root/scripts/cortex/watch/daemon.py" || true)
for pid in $worker_pids; do
    echo "kill -9 $pid"
    kill -9 "$pid" 2>/dev/null || true
done
sleep 3
after_kill_count=$(cortex_pid_count)
echo "강제 종료 후 잔존: $after_kill_count"
cortex_pids
if [ "$after_kill_count" -gt 0 ]; then
    failures+=("강제 종료 후 자식 잔존 ${after_kill_count}개")
fi
vram_after_kill=$(vram_used_mib)
if [ -n "$baseline_vram" ] && [ -n "$vram_after_kill" ]; then
    diff=$((vram_after_kill - baseline_vram))
    echo "VRAM 차이(MiB): $diff"
    if [ "$diff" -gt 100 ]; then
        failures+=("강제 종료 후 VRAM ${diff} MiB 누수")
    fi
fi

# 결과
stage "결과"
if [ ${#failures[@]} -eq 0 ]; then
    echo "OK — 잔존 프로세스·VRAM 누수 감지 없음."
    exit 0
else
    echo "FAIL:"
    for f in "${failures[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
