#!/bin/bash

# ==============================================================================
# Cortex Agent Bootstrap Setup Script
# ==============================================================================
# 사용법: bash <folder_name>/setup.sh (프로젝트 루트에서 실행 권장)

set -e

# 1. 색상 정의
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}>>> Cortex Agent 초기 설정을 시작합니다...${NC}"

# 2. 현재 스크립트의 위치 파악
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
CURRENT_DIR_NAME=$(basename "$SCRIPT_DIR")
PARENT_DIR=$(dirname "$SCRIPT_DIR")

# 3. 폴더명 정규화 (.agents로 변경)
if [ "$CURRENT_DIR_NAME" != ".agents" ]; then
    echo -e "${YELLOW}현재 폴더명($CURRENT_DIR_NAME)을 .agents 로 변경합니다...${NC}"
    if [ -d "$PARENT_DIR/.agents" ]; then
        echo -e "${RED}오류: 이미 $PARENT_DIR/.agents 폴더가 존재합니다. 기존 폴더를 확인하세요.${NC}"
        exit 1
    fi
    mv "$SCRIPT_DIR" "$PARENT_DIR/.agents"
    SCRIPT_DIR="$PARENT_DIR/.agents"
fi

cd "$SCRIPT_DIR"

# 4. 가상환경(venv) 구축
echo -e "${BLUE}Python 가상환경을 생성합니다...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}오류: python3가 설치되어 있지 않습니다.${NC}"
    exit 1
fi

python3 -m venv venv
echo -e "${GREEN}가상환경 생성 완료.${NC}"

# 5. 의존성 패키지 설치
echo -e "${BLUE}필수 라이브러리를 설치합니다 (requirements.txt)...${NC}"
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
echo -e "${GREEN}라이브러리 설치 완료.${NC}"

# 6. .env 설정 (기존 .env가 없을 경우 .env.example 복사)
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}.env 파일이 없어 .env.example을 복사합니다. 직접 수정이 필요할 수 있습니다.${NC}"
    cp .env.example .env
fi

# 7. 완료 및 다음 안내
echo -e "\n${GREEN}==================================================================${NC}"
echo -e "${GREEN}  Cortex Agent 설치가 성공적으로 완료되었습니다!${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "\n이제 다음 명령어로 초기 인덱싱을 수행하세요:"
echo -e "${BLUE}  PYTHONPATH=.agents/scripts venv/bin/python3 .agents/scripts/cortex/indexer.py . --force${NC}\n"
