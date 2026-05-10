# Cortex Agent — 설치 가이드 (V3 — uv)

## 🚀 빠른 시작 (Quick Start)

본 인프라 캡슐을 사용하려는 프로젝트(또는 모노레포의 특정 하위 프로젝트)의 최상위 경로에서 다음을 실행하십시오.

### 사전 요구사항
- Python 3.12
- [uv](https://docs.astral.sh/uv/) (패키지 관리자)

```bash
# 1. uv 설치 (미설치 시)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 에이전트 캡슐 클론 (프로젝트 루트에 .cortex 폴더로 배치)
git clone <저장소_URL> .cortex

# 3. 의존성 동기화 (가상환경 자동 생성 + 패키지 설치)
uv sync --project .cortex
```

> **참고**: `uv sync`는 `.cortex/pyproject.toml`을 읽어 `.cortex/.venv/`에 가상환경을 자동 생성하고 모든 의존성을 설치합니다. `python3 -m venv`나 `pip install` 명령어는 필요하지 않습니다.

---

## 1. 의존성 설치 (중요)

사용 중인 컴퓨팅 환경에 맞춰 아래 방식 중 하나를 선택하여 설치하십시오.

### [A] 표준 설치 (CPU 전용 또는 범용)
```bash
uv sync --project .cortex
```
또는 `.cortex` 내부에서 실행 시:
```bash
uv sync --project .
```

### [B] 고성능 GPU 가속 설치 (NVIDIA Ampere 이상)
NVIDIA GPU를 활용하여 임베딩 및 검색 속도를 높이려면 이 방식을 선택하십시오.
```bash
uv sync --project .cortex --group gpu-accel
```
- **상세 가이드**: [DEPENDENCIES.md](./DEPENDENCIES.md)

---

## 2. 프로젝트 통합 설정 (Lean Context Setup)

AI 에이전트가 `.cortex` 내부의 수천 개 파일을 직접 스캔하여 토큰을 낭비하지 않도록, **`.cortex/templates/ignores/`** 내의 설정들을 워크스페이스 루트로 복사하십시오.

- **방법**: `.geminiignore`, `.claudesignore` 등과 `.vscode/` 폴더를 루트로 이동/복사합니다.
- **효과**: 에이전트의 시야에서는 숨겨지지만, 백그라운드 MCP 엔진은 정상적으로 이를 읽어 DB를 구축합니다.

---

## 3. 초기 인덱싱 및 실행

### [A] 처음 인덱싱
```bash
uv run --project .cortex python .cortex/scripts/cortex/indexer.py . --force
```

### [B] MCP 서버 등록 (CLI 명령어 추천)

MCP 등록 시 다음 환경변수를 명시적으로 설정하여야 합니다.
- **PYTHONPATH**: `.cortex/scripts`
- **CORTEX_HOME**: `.cortex` 절대경로
- **CORTEX_WORKSPACE**: 실제 인덱싱/작업 대상 프로젝트 루트
- **CORTEX_ENV_PATH**: (선택 사항) `.env` 위치를 직접 지정할 때만 사용

> **참고**
> - `.cortex` 자체를 테스트 workspace로 쓰는 경우 `CORTEX_WORKSPACE`는 `.cortex`로 둘 수 있습니다.
> - 나중에 Cortex 엔진을 사용자 홈이나 별도 위치에 두고 다른 프로젝트를 인덱싱하려면 `CORTEX_HOME`과 `CORTEX_WORKSPACE`를 분리해서 지정해야 합니다.
> - `.git`이 파일인 linked worktree 구조는 정상입니다.
> - `.env`, `.venv`, `data`, `history`는 로컬 파일이며 커밋 대상이 아닙니다.
> - 모델 캐시가 없으면 임베딩 테스트가 모델 다운로드를 시도할 수 있으므로, 테스트 시에는 `local_files_only=True`로 먼저 확인해야 합니다.

**Gemini CLI (Windows PowerShell 예시):**
```powershell
$CORTEX_HOME="C:\path\to\your\workspace\.cortex"
$CORTEX_WORKSPACE="C:\path\to\your\workspace\.cortex"

gemini mcp add -s user `
  -e PYTHONPATH="$CORTEX_HOME\scripts" `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

**Claude Code (Windows PowerShell 예시):**
```powershell
claude mcp add -s user `
  -e PYTHONPATH="$CORTEX_HOME\scripts" `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

**OpenAI Codex CLI (Windows PowerShell 예시):**
```powershell
codex mcp add `
  --env PYTHONPATH="$CORTEX_HOME\scripts" `
  --env CORTEX_HOME="$CORTEX_HOME" `
  --env CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- uv run --project "$CORTEX_HOME" python "$CORTEX_HOME\scripts\cortex_mcp.py"
```

---

## ⚖️ 라이선스 (License)
- **Skills**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.