# Cortex Agent — 설치 가이드 (V2)

## 🚀 빠른 시작 (Quick Start)

본 인프라 캡슐을 사용하려는 프로젝트(또는 모노레포의 특정 하위 프로젝트)의 최상위 경로에서 다음을 실행하십시오.

```bash
# 1. 에이전트 캡슐 클론 (프로젝트 루트에 .agents 폴더로 배치)
git clone <저장소_URL> .agents

# 2. 가상환경 생성 및 업그레이드
python3 -m venv .agents/venv
.agents/venv/bin/pip install --upgrade pip
```

---

## 1. 의존성 설치 (중요)

사용 중인 컴퓨팅 환경에 맞춰 아래 방식 중 하나를 선택하여 설치하십시오.

### [A] 표준 설치 (CPU 전용 또는 범용)
```bash
.agents/venv/bin/pip install -r .agents/requirements.txt
```

### [B] 고성능 GPU 가속 설치 (NVIDIA Ampere 이상)
NVIDIA GPU를 활용하여 임베딩 및 검색 속도를 높이려면 이 방식을 선택하십시오.
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
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
```

### [B] MCP 서버 등록 (CLI 명령어 추천)
에이전트별 MCP 등록 명령어를 사용하여 간편하게 추가할 수 있습니다. (전체 경로는 `pwd` 명령어로 확인하십시오)

**Gemini CLI (`gemini mcp add` 명령어 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
gemini mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp /절대/경로/참조/.agents/venv/bin/python3 /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

**Claude Code (CLI 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
claude mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp -- /절대/경로/참조/.agents/venv/bin/python3 /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

**OpenAI Codex CLI (`codex mcp add` 명령어 사용):**
터미널에서 다음 명령어를 실행하십시오:
```bash
codex mcp add -s user -e PYTHONPATH=/절대/경로/참조/.agents/scripts cortex-mcp /절대/경로/참조/.agents/venv/bin/python3 /절대/경로/참조/.agents/scripts/cortex_mcp.py
```

---

## ⚖️ 라이선스 (License)
- **Skills**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
