# Cortex Agent — 설치 가이드 (V3 — uv)

## 🚀 빠른 시작 (Quick Start)

본 인프라 캡슐을 사용하려는 프로젝트(또는 모노레포의 특정 하위 프로젝트)의 최상위 경로에서 다음을 실행하십시오.

### 사전 요구사항
- Python 3.12
- [uv](https://docs.astral.sh/uv/) (패키지 관리자)

```bash
# 1. uv 설치 (미설치 시)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 에이전트 캡슐 클론 (프로젝트 루트에 .agents 폴더로 배치)
git clone <저장소_URL> .agents

# 3. 의존성 동기화 (가상환경 자동 생성 + 패키지 설치)
uv sync --project .agents
```

> **참고**: `uv sync`는 `.agents/pyproject.toml`을 읽어 `.agents/.venv/`에 가상환경을 자동 생성하고 모든 의존성을 설치합니다. `python3 -m venv`나 `pip install` 명령어는 필요하지 않습니다.

---

## 1. 의존성 설치 (중요)

사용 중인 컴퓨팅 환경에 맞춰 아래 방식 중 하나를 선택하여 설치하십시오.

### [A] 표준 설치 (CPU 전용 또는 범용)
```bash
uv sync --project .agents
```

### [B] 고성능 GPU 가속 설치 (NVIDIA Ampere 이상)
NVIDIA GPU를 활용하여 임베딩 및 검색 속도를 높이려면 이 방식을 선택하십시오.
```bash
uv sync --project .agents --group gpu-accel
```
- **상세 가이드**: [DEPENDENCIES.md](./DEPENDENCIES.md)

---

## 2. 프로젝트 통합 설정 (Lean Context Setup)

AI 에이전트가 `.agents` 내부의 수천 개 파일을 직접 스캔하여 토큰을 낭비하지 않도록, **`.agents/templates/ignores/`** 내의 설정들을 워크스페이스 루트로 복사하십시오.

- **방법**: `.geminiignore`, `.claudesignore` 등과 `.vscode/` 폴더를 루트로 이동/복사합니다.
- **효과**: 에이전트의 시야에서는 숨겨지지만, 백그라운드 MCP 엔진은 정상적으로 이를 읽어 DB를 구축합니다.

---

## 3. 초기 인덱싱 및 실행

### [A] 처음 인덱싱
```bash
uv run --project .agents python .agents/scripts/cortex/indexer.py . --force
```

### [B] MCP 서버 등록 (CLI 명령어 추천)
에이전트별 MCP 등록 명령어를 사용하여 간편하게 추가할 수 있습니다. (전체 경로는 `pwd` 명령어로 확인하십시오)

**Gemini CLI (`gemini mcp add` 명령어 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
gemini mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp -- uv run --project /절대/경로/참조/.agents python /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

**Claude Code (CLI 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
claude mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp -- uv run --project /절대/경로/참조/.agents python /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

**OpenAI Codex CLI (`codex mcp add` 명령어 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
codex mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp -- uv run --project /절대/경로/참조/.agents python /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

---

## ⚖️ 라이선스 (License)
- **Skills**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
