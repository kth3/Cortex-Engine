---
trigger: model_decision
description: 계층형 디버깅 프로토콜 (Layered Diagnostics Protocol)
---

# 계층형 디버깅 프로토콜 (Layered Diagnostics Protocol)

에이전트는 코드 수정이 반복적으로 실패하거나, 변경 사항이 무시된다고 판단할 때 이 프로토콜을 활성화하여 시스템의 물리적 레이어부터 점검합니다.

## 1. 진단 철학
"동일한 입력(코드 수정)에 대해 동일한 실패(오류 지속)가 반복된다면, 변경 지점(Code)이 아닌 반영 경로(Runtime)에 문제가 있는 것이다."
- **원인 탐색 실패 시, 원인 재현을 통한 분석을 고려한다.** (최소 재현 코드/POC 구축)


## 2. 계층별 점검 및 조치 (Priority Order)

### Phase 0: Proactive Environment Check (선제적 환경 점검)
테스트 코드 실행 또는 시스템 명령어 수행 전 반드시 다음을 확인하여 가상환경 오염 및 의존성 충돌을 방지합니다.
- **활성 환경 검증**: 명령어 실행 전 `which python` 또는 `uv run python -c "import sys; print(sys.prefix)"`를 실행하여 현재 환경이 에이전트 전용(`.agents/.venv`)이 아닌, 대상 프로젝트의 환경인지 선제적으로 점검합니다.
- **명시적 격리 실행**: 모노레포 구조에서 명령어 실행 시 `uv run --project <모듈경로> <명령어>` 형태로 실행하여, 프로젝트별 의존성이 섞이지 않도록 차단합니다.

### Phase 1: Artifact & Cache (물리적 레이어)
가장 먼저 '오래된 바이트코드'나 '빌드 아티팩트'를 제거합니다.
- **Python**: `find . -name "*.pyc" -delete`, `find . -type d -name "__pycache__" -exec rm -rf {} +`
- **Java/Spring**: `./gradlew clean`, `rm -rf build/`, `target/`
- **Frontend/Node.js**: `rm -rf .next/`, `rm -rf dist/`, `npm cache clean --force`

### Phase 2: Dependency & Environment (의존성 레이어)
패키지 무결성 및 환경 변수를 점검합니다.
- 패키지 잠금 파일(`package-lock.json`, `poetry.lock`)과 실제 설치된 패키지 정합성 확인
- `.env` 및 설정 파일의 런타임 로딩 여부 검증

### Phase 3: Runtime Process (프로세스 레이어)
메모리에 상주한 프로세스가 이전 코드를 캐싱하고 있는지 점검합니다.
- 개발 서버(Dev Server) 재시작
- MCP 서버의 경우 핫리로드 로직 강제 실행 또는 프로세스 킬 후 재시도

### Phase 4: MCP Infrastructure & Protocol
MCP 도구가 에러를 반환할 때 수동 도구로 회피하기 전 반드시 수행합니다.
- **TypeError (Argument Error)**: 호출하는 MCP 스크립트와 호출받는 모듈 간의 함수 시그니처(`def`)가 일치하는지 확인하고 즉시 수정.
- **ImportError / ModuleNotFound**: `sys.path` 설정과 `.agents/scripts/` 내 파일 위치가 유효한지 검증.
- **Runtime Conflict**: 코드 수정 후 동일 에러 발생 시, 현재 에디터(IDE)의 MCP 서버 프로세스를 재시작(Restart Server)하도록 사용자에게 요청.

---

> [!TIP]
> 디버깅은 코드를 고치는 과정이 아니라, 코드가 실행되는 **'환경의 오염'**을 걷어내는 과정입니다.