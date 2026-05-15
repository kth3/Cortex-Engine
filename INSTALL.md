# Cortex Agent — 설치 가이드 (V3 — uv)

## 빠른 시작 (Quick Start)

본 인프라 캡슐을 사용하려는 프로젝트 또는 모노레포의 특정 하위 프로젝트 최상위 경로에서 다음을 실행하십시오.

### 사전 요구사항
- Python 3.12
- [uv](https://docs.astral.sh/uv/) 패키지 관리자

```bash
# 1. uv 설치 (미설치 시)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 에이전트 캡슐 클론 (프로젝트 루트에 .cortex 폴더로 배치)
git clone <저장소_URL> .cortex

# 3. 의존성 동기화 (가상환경 자동 생성 + 패키지 설치)
uv sync --project .cortex
```

`uv sync`는 `.cortex/pyproject.toml`을 읽어 `.cortex/.venv/`에 가상환경을 자동 생성하고 의존성을 설치합니다. 별도 `python -m venv` 또는 `pip install` 절차는 기본적으로 필요하지 않습니다.

---

## 1. 의존성 설치

### [A] 표준 설치
```bash
uv sync --project .cortex
```

`.cortex` 내부에서 실행하는 경우:
```bash
uv sync --project .
```

### [B] 고성능 GPU 가속 설치 (NVIDIA Ampere 이상)
```bash
uv sync --project .cortex --group gpu-accel
```

상세 의존성 설명은 [DEPENDENCIES.md](./DEPENDENCIES.md)를 참고하십시오.

---

## 2. 프로젝트 통합 설정

AI 에이전트가 `.cortex` 내부의 수천 개 파일을 직접 스캔하여 토큰을 낭비하지 않도록, `.cortex/templates/ignores/` 내의 ignore 설정을 워크스페이스 루트로 복사하십시오.

- `.geminiignore`, `.claudesignore` 등과 `.vscode/` 폴더를 루트로 이동 또는 복사합니다.
- 에이전트 시야에서는 인프라 파일이 숨겨지지만, Cortex MCP/Indexer는 `.cortex` 경로를 직접 읽어 DB를 구축합니다.

---

## 3. 경로 모델

현재 기본 경로 모델은 `.cortex`입니다.

- `CORTEX_HOME`: Cortex 인프라가 위치한 디렉터리입니다. 일반적으로 `<workspace>/.cortex`입니다.
- `CORTEX_WORKSPACE`: 실제 인덱싱 및 편집 대상 프로젝트 루트입니다.
- `CORTEX_ENV_PATH`: `.env` 파일 위치를 명시적으로 지정할 때만 사용합니다.
- 신규 설치 및 CI 검증은 `.cortex` 기준입니다.

`CORTEX_HOME`과 `CORTEX_WORKSPACE`는 분리할 수 있습니다. 예를 들어 홈 디렉터리에 설치된 Cortex로 여러 프로젝트를 인덱싱할 수 있습니다.

---

## 4. 초기 인덱싱 및 실행

### [A] 처음 인덱싱
```bash
uv run --project .cortex python .cortex/scripts/cortex/indexer.py . --force
```

### [B] 런타임 제어

`cortex_ctl.py`는 thin entrypoint입니다. 실제 제어 로직은 `scripts/cortex/runtime/` 하위 모듈에 분리되어 있습니다.

```bash
uv run --project .cortex python .cortex/scripts/cortex_ctl.py status
uv run --project .cortex python .cortex/scripts/cortex_ctl.py start
uv run --project .cortex python .cortex/scripts/cortex_ctl.py stop
```

주요 내부 계층은 다음과 같습니다.

- `runtime/paths.py`: 포트, 스크립트, 로그/락 파일 경로
- `runtime/ipc.py`: 길이 prefix 기반 소켓 메시지 송수신
- `runtime/environment.py`: child process 환경 변수 구성
- `runtime/process.py`: 백그라운드 프로세스 실행 및 PID 관리
- `runtime/lock.py`: ctl 실행 단위 상호 배제
- `runtime/logging.py`: 런타임 로그 설정
- `runtime/control.py`: start/status/stop orchestration
- `runtime/engine_server.py`: engine server entrypoint
- `runtime/engine_router.py`: worker 라우팅 및 idle 모니터 연계
- `runtime/engine_worker.py`: PyTorch/SentenceTransformers embedding worker
- `runtime/worker_manager.py`: worker 기동/종료/상태 확인
- `runtime/watcher_launcher.py`: watchdog watcher 실행
- `runtime/local_daemon.py`: 선택적 local daemon 실행

### [C] 로컬 데몬 옵션

`.env`에 다음 값을 설정하면 `start` 시 engine server 준비 이후 local daemon을 추가 실행합니다.

```env
CORTEX_LOCAL_DAEMON=path/to/daemon.py
```

daemon 경로가 상대 경로이면 `CORTEX_HOME` 기준으로 해석됩니다.

---

## 5. MCP 서버 등록

MCP 등록 시 다음 환경변수를 명시적으로 설정하십시오.

- `PYTHONPATH`: `.cortex/scripts`
- `CORTEX_HOME`: `.cortex` 절대경로
- `CORTEX_WORKSPACE`: 실제 인덱싱/작업 대상 프로젝트 루트
- `CORTEX_ENV_PATH`: 선택 사항

### Gemini CLI (Windows PowerShell 예시)
```powershell
$CORTEX_HOME="C:\path\to\your\workspace\.cortex"
$CORTEX_WORKSPACE="C:\path\to\your\workspace"

gemini mcp add -s user `
  -e PYTHONPATH="$CORTEX_HOME\scripts" `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

### Claude Code (Windows PowerShell 예시)
```powershell
claude mcp add -s user `
  -e PYTHONPATH="$CORTEX_HOME\scripts" `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

### OpenAI Codex CLI (Windows PowerShell 예시)
```powershell
codex mcp add `
  --env PYTHONPATH="$CORTEX_HOME\scripts" `
  --env CORTEX_HOME="$CORTEX_HOME" `
  --env CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

---

## 6. 검증 절차

CI와 동일한 방향으로 로컬 검증하려면 다음 순서로 실행합니다.

```bash
uv sync --project .cortex
PYTHONPATH=.cortex/scripts uv run --project .cortex python - <<'PY'
from pathlib import Path
import py_compile
for path in Path('.cortex/scripts').rglob('*.py'):
    py_compile.compile(str(path), doraise=True)
print('py_compile ok')
PY
```

이후 런타임 제어 검증을 수행합니다.

```bash
uv run --project .cortex python .cortex/scripts/cortex_ctl.py status
uv run --project .cortex python .cortex/scripts/cortex_ctl.py stop
uv run --project .cortex python .cortex/scripts/cortex_ctl.py start
```

임베딩 모델 캐시가 없는 환경에서는 첫 실행 시 모델 다운로드가 발생할 수 있습니다. CI에서는 문법/import/인덱싱/MCP smoke를 중점 검증하고, 장시간 GPU/daemon 실기동 검증은 로컬 검증 대상으로 둡니다.

---

## 라이선스

- **Skills**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
