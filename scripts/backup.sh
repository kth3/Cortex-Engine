#!/bin/bash

# 스크립트 실행 디렉토리 설정
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/../.." || exit 1

PROJECT_ROOT="$(pwd)"
PROJECT_NAME=$(basename "$PROJECT_ROOT")
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# 백업 대상 및 경로 설정
AGENT_DIR=".agents"
SKILLS_DIR="skills"
GDRIVE_ARCHIVE="gdrive:AgentBackups/${PROJECT_NAME}/archives"
GDRIVE_LATEST="gdrive:AgentBackups/${PROJECT_NAME}/latest/${PROJECT_NAME}_backup_latest.tar.gz"
TEMP_BACKUP_FILE="${PROJECT_NAME}_backup_${TIMESTAMP}.tar.gz"

echo "=========================================="
echo "[INFO] 고속 15MB 타겟 극한 압축 백업을 시작합니다. (v7.1)"
echo "[INFO] 프로젝트: ${PROJECT_NAME}"
echo "[INFO] 대상: ${AGENT_DIR}, ${SKILLS_DIR}"
echo "=========================================="

# 1. 압축 실행 (15MB 지향 - v7.1)
echo "[PROGRESS] 극한의 다이어트 중 (Goal: 15~25MB)..."
# venv뿐만 아니라 불필요한 캐시, 히스토리 중 유실 가능한 것들을 제외
tar --exclude=".agents/venv" \
    --exclude=".agents/memories.db-wal" \
    --exclude=".agents/memories.db-shm" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    -c "${AGENT_DIR}" "${SKILLS_DIR}" | gzip -9 > "${TEMP_BACKUP_FILE}"

if [ $? -eq 0 ]; then
    FILE_SIZE=$(ls -lh "${TEMP_BACKUP_FILE}" | awk '{print $5}')
    echo "[SUCCESS] 압축 완료: ${TEMP_BACKUP_FILE} (최종 용량: ${FILE_SIZE})"
else
    echo "[ERROR] 압축 실패!"
    exit 1
fi

# 2. 구글 드라이브 업로드 (아카이브 - 1회 전송)
echo "[PROGRESS] Google Drive 업로드 중 (Archive Transfer)..."
rclone copy "${TEMP_BACKUP_FILE}" "${GDRIVE_ARCHIVE}" --progress

# 3. 서버측 복제 (Latest 포인터 - 업로드 대역폭 0 사용)
echo "[PROGRESS] 서버측 포인터 갱신 중 (Instant Sync)..."
rclone copyto "${GDRIVE_ARCHIVE}/${TEMP_BACKUP_FILE}" "${GDRIVE_LATEST}" --drive-server-side-across-configs

# 4. 임시 파일 삭제
rm "${TEMP_BACKUP_FILE}"

echo "------------------------------------------"
echo "[DONE] 최종 백업 수복 완료 (15MB 지향)"
echo "------------------------------------------"

# read -p 제거됨 (백그라운드 실행 안정화)