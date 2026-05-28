[English Version Available](README.en.md)

# Cortex Agent Infrastructure (`.cortex`)

**"The Bridge between Human Intent and Agent Intelligence."**

파편화된 에이전트의 기억을 영속화하고, MCP(Model Context Protocol)를 통해 어떤 프로젝트에서든 즉시 작업 맥락을 형성할 수 있도록 설계된 범용 에이전트 엔지니어링 인프라입니다. 최신 멀티 에이전트 오케스트레이션 패턴과 하이브리드 데이터베이스 기술을 결합하여 로컬 우선 컨텍스트 엔진을 제공합니다.

최근 구조는 `.cortex` 경로 모델을 기본으로 사용하며, MCP, watcher, process control, runtime router를 Rust crate로 분리하는 방향으로 정리되었습니다.

---

## 시스템 아키텍처

기존 Python 단일체 엔진은 Rust MCP dispatcher, Rust engine server, Python embedding worker, Rust watcher, Rust process control 계층으로 분리되었습니다. `cortex-ctl`, `cortex-engine`, `cortex-mcp`, `cortex-watcher`가 운영 표면을 담당하고 Python은 임베딩 모델 실행 계층에만 남습니다.

```mermaid
---
config:
  flowchart:
    curve: stepAfter
    nodeSpacing: 50
    rankSpacing: 80
---
flowchart TB

  subgraph RequestFlow["Request / Retrieval Flow"]
    direction LR

    Agent["Coding Agent / IDE"]

    subgraph MCP["MCP Layer"]
      direction TB
      Entry["MCP Entry Point<br/>요청 수신 · 응답 반환"]
      Router["Request Router<br/>입력 검증 · 기능 라우팅"]
      Handler["Capability Handlers<br/>검색 · 메모리 · 인덱싱 · 편집 · 세션"]
    end

    subgraph Retrieval["Retrieval Layer"]
      direction TB
      Plan["Query Planning<br/>범위 · 필터 · 의도 정리"]
      Search["Hybrid Retrieval<br/>키워드 + 벡터 + 구조 탐색"]
      Format["Rank & Format<br/>후보 병합 · 순위화 · 위치 포함"]
    end

    Agent -->|"tool request"| Entry
    Entry -->|"validated request"| Router
    Router -->|"capability request"| Handler
    Handler -->|"search request"| Plan
    Plan -->|"planned query"| Search
    Search -->|"candidate context"| Format
    Format -->|"ranked context"| Handler
    Handler -->|"tool result"| Agent
  end

  subgraph IndexFlow["Indexing / Write Flow"]
    direction LR

    Local["Local CLI / File Watcher"]

    subgraph Pipeline["Indexing Pipeline"]
      direction TB
      FileSelect["File Selection<br/>대상 파일 탐색 · 변경 파일 선별"]
      Extract["Parse & Extract<br/>심볼 · 참조 · 호출 관계 추출"]
      Chunk["Chunk & Metadata<br/>검색 단위 · 라인 범위 · 컨텍스트 생성"]
      GraphSync["Graph Sync<br/>코드 구조 그래프 반영"]
    end

    subgraph Runtime["Runtime Layer"]
      direction TB
      RuntimeService["Runtime Service<br/>장기 실행 프로세스 · 작업 중계"]
      EmbeddingWorker["Embedding Worker<br/>텍스트 임베딩 생성"]
    end

    Local -->|"manual index / file change"| FileSelect
    Handler -->|"index command / workspace scope"| FileSelect

    FileSelect -->|"selected files"| Extract
    Extract -->|"symbols / references / call relations"| Chunk
    Extract -->|"graph facts"| GraphSync

    Chunk -->|"texts to embed"| RuntimeService
    RuntimeService -->|"embedding job"| EmbeddingWorker
    EmbeddingWorker -->|"vectors"| RuntimeService
    RuntimeService -->|"vector result"| Chunk
  end

  subgraph Storage["Persistent Storage"]
    direction LR
    SQLVector[("Physical Store 1<br/>SQLite + Text Index + Vector<br/>파일 · 청크 · 메모리 · 심볼 · 엣지 · 벡터")]
    GraphDB[("Physical Store 2<br/>Kuzu Graph Store<br/>코드 그래프 노드 · 관계")]
  end

  Search -->|"keyword / vector / metadata lookup"| SQLVector
  SQLVector -->|"candidate rows / matches"| Search

  Search -->|"related structure lookup"| GraphDB
  GraphDB -->|"related nodes / relations"| Search

  Chunk -->|"chunks / metadata / vectors"| SQLVector
  Extract -->|"symbol rows / edge rows"| SQLVector
  GraphSync -->|"graph nodes / graph relations"| GraphDB

  %% Input / Request / Write Flow
  linkStyle 0,1,2,3,4,5,8,9,10,11,12,13,14,17,19,21,22,23 stroke:#2563eb,stroke-width:2px;

  %% Result / Response Flow
  linkStyle 6,7,15,16,18,20 stroke:#16a34a,stroke-width:2px;

  style RequestFlow fill:#f8fafc,stroke:#cbd5e1
  style IndexFlow fill:#f8fafc,stroke:#cbd5e1
  style Storage fill:#f8fafc,stroke:#cbd5e1
```

## 주요 특징

### 1. Hybrid Context Engine & AST Parsing

- **AST Structural Parsing (`Tree-sitter`)**: Python, C#, TypeScript 등의 코드를 AST 수준으로 분석하여 클래스, 함수, 호출 관계를 추출합니다.
- **Vector Search (`sqlite-vec`)**: 로컬 SQLite 기반 벡터 검색으로 외부 서버 없이 시맨틱 검색을 수행합니다.
- **Graph Analysis (`Kuzu DB`)**: 함수 호출, 포함 관계, 외부 참조를 그래프 형태로 추적합니다.
- **FTS5 Text Search**: 키워드 기반 검색과 RRF(Reciprocal Rank Fusion) 결합을 지원합니다.

### 2. Runtime Modularization

런타임 제어 계층은 다음처럼 분리되어 있습니다.

- `rust/crates/ctl`: start/status/stop orchestration과 프로세스 경로 관리
- `rust/crates/runtime`: Rust engine router, worker supervisor, idle monitor, length-prefixed JSON IPC
- `rust/crates/ctl`: start/status/stop orchestration
- `rust/crates/watcher`: file watch, scan, parse, SQLite write path
- `src/cortex/runtime/engine_worker.py`: PyTorch/SentenceTransformers embedding worker

Python은 embedding 모델 생태계에 필요한 worker/provider 영역에만 남고, runtime orchestration과 MCP는 Rust 바이너리 기준으로 동작합니다.

### 3. `.cortex` Path Model

신규 기준 경로는 `.cortex`입니다. 설치/문서/CI 모두 `.cortex` 기준으로 정리됩니다.

- `CORTEX_HOME`: Cortex 인프라 루트 (`pyproject.toml`이 있는 위치)
- `CORTEX_WORKSPACE`: 실제 작업 대상 프로젝트 루트
- `CORTEX_DATA_HOME`: 워크스페이스별 DB·인덱스가 저장되는 글로벌 루트(기본 `~/.cortex`)
- `CORTEX_WORKSPACE_KEY`: 멀티레포 그룹화 — 여러 폴더를 한 워크스페이스로 묶을 때 동일 값을 박는다
- `CORTEX_ENV_PATH`: `.env` 파일 위치를 직접 지정할 때 사용
- `CORTEX_START_TIMEOUT`: `cortex-ctl start`가 엔진 ready를 기다리는 시간(초). 기본 35, WSL/CUDA에선 60~120 권장. timeout 도달 시 엔진이 `loading` 상태라면 INFO 메시지와 함께 백그라운드 로딩을 인정하고 success 반환
- `CORTEX_DIAG_READY_TIMEOUT`: 진단 스크립트(`zombie-check.{sh,ps1}`)의 READY 폴링 시간(초). 기본 90

코드 인덱스(`memories.db`, `graph_db_store/`)와 히스토리는 `<CORTEX_DATA_HOME>/workspaces/<sha1>/` 하위에 격리되며, 사용자 프로젝트 폴더에는 cortex 흔적이 남지 않습니다. 패키지(코드)와 데이터(인덱스·메모리)가 분리된 구조라 cortex 본체 업데이트가 워크스페이스 데이터를 건드리지 않습니다.

### 4. Multi-Lane Parallel Execution

도메인(Lane) 기반 병렬 락 시스템을 통해 여러 터미널 또는 에이전트가 동시에 작업할 때 충돌을 줄입니다. 릴레이 계층은 작업 핸드오프와 동시성 제어를 담당합니다.

### 5. Hardware-Aware Embedding Strategy

SentenceTransformers/PyTorch 기반 embedding worker를 별도 프로세스로 격리합니다. GPU/MPS/CUDA 사용은 Python worker에 남겨 두고, control/server/router 계층은 모델 의존성을 낮춘 구조로 유지합니다.

---

## 디렉토리 구조

```text
.cortex/                                  # cortex 본체
├── docs/                                 # 인프라 관련 문서
├── hooks/                                # 런타임 라이프사이클 훅
├── rules/                                # 에이전트 행동 규칙 및 정밀 편집 지침
├── scripts/                              # Cortex 코어 모듈, MCP 서버, runtime 제어 계층
├── tasks/                                # 능동적 추적을 위한 작업 문서
├── templates/                            # 시스템 템플릿 및 ignore 번들
├── knowledge/
│   └── knowledge.zip                     # 외부 지식 시드 (선택 전개)
├── pyproject.toml                        # uv 기반 의존성 선언
├── .venv/                                # [비공유] uv 가상 환경
├── uv.lock                               # 패키지 잠금 파일
└── settings.yaml                         # 인프라 전역 설정

~/.cortex/                                # 글로벌 데이터 루트 (CORTEX_DATA_HOME)
└── workspaces/
    └── <sha1-of-workspace-path>/         # 워크스페이스별 격리
        ├── memories.db                   # 메모리·관찰·세션 (sqlite + vec)
        ├── graph_db_store/               # 코드 그래프 (kuzu)
        └── history/                      # 세션 로그·관찰 이력
```

---

## Cortex Modular Layout

최근 구조 개편에 따라 Cortex 백엔드는 Rust crate 중심으로 분리되었습니다. SQLite, 검색, 메모리, 편집, watcher, parser 구현은 Rust에 위치하고 Python은 임베딩 worker/provider만 유지합니다:

- `rust/crates/mcp`: MCP JSON-RPC 도구 catalog, 검색, 메모리, 편집, 세션 도구
- `rust/crates/storage`: SQLite 스키마, resolver, 공용 저장소 접근
- `rust/crates/scanner`: `.gitignore` 기반 파일 탐색과 필터
- `rust/crates/parsers`: Tree-sitter 중심 파서 레지스트리
- `rust/crates/watcher`: file watch, scan, parse, SQLite write path
- `rust/crates/runtime`: 데몬 라우터, worker supervisor, IPC 등 실행 환경 인프라
- `src/cortex/embeddings`, `src/cortex/runtime/engine_worker.py`: PyTorch/SentenceTransformers 모델 로드와 추론

> 외부 Workspace 경로 대응은 Rust `cortex-ctl`/`cortex-engine` 경로 정책을 따르며, 모델 다운로드나 GPU 토큰 의존성 검증은 기본 CI에서 제외되고 로컬 구성 이후의 별도 검증 대상으로 분류됩니다.

---

## 설치 및 사용

상세 가이드는 [INSTALL.md](./INSTALL.md)를 참고하십시오.

### 릴리즈 패키지 설치

uv가 없으면 먼저 설치합니다:

```bash
# WSL / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
iwr -useb https://astral.sh/uv/install.ps1 | iex
```

Windows 사용자는 릴리즈 zip을 받아 압축을 풀고 Python 의존성을 설치합니다. GPU 가속은 이 기본 설치 위에 선택으로 추가합니다.

```powershell
.\install.ps1
cortex-ctl status
```

소스에서 직접 빌드하거나 릴리즈 패키지를 만들 때는 같은 구조에서 Rust 바이너리를 생성합니다:

```powershell
git clone https://github.com/kth3/Cortex-agents_infra.git
cd Cortex-agents_infra
uv sync

cargo build --manifest-path rust/Cargo.toml --release `
  -p cortex-ctl `
  -p cortex-runtime `
  -p cortex-watcher `
  -p cortex-mcp
```

### cortex-ctl 명령 표면

```text
cortex-ctl start | stop | restart | status        # MCP 엔진 라이프사이클
cortex-ctl relay acquire | release | status | force-release
```

토큰과 임베딩 모델 설정은 환경변수 또는 실행 스크립트에서 관리합니다.

### HuggingFace 토큰

cortex는 토큰을 세 가지 소스에서 자동으로 찾습니다. 다음 중 **하나만** 해두면 됩니다:

| 방식 | 동작 |
|---|---|
| `huggingface-cli login` (1회) | `~/.cache/huggingface/token` 표준 위치에 저장 |
| 셸 환경변수 `HF_TOKEN=<T>` | rc 파일에 `export HF_TOKEN=...` 추가 |
| 직접 `.env` 관리 | 사용하는 셸/실행 스크립트에서 로드 |

우선순위는 huggingface_hub 라이브러리 표준: **명시 인자 > `HF_TOKEN` env > `~/.cache/huggingface/token` 파일**. 공개 모델만 쓸 때는 토큰이 없어도 동작하며, 게이트 모델·속도 가속이 필요할 때만 설정합니다.

모델 캐시 기본 위치는 `~/.cache/huggingface/hub/` (Linux/WSL/Mac), Windows에서는 `%USERPROFILE%\.cache\huggingface\hub\`입니다. 다른 위치를 쓰려면 `HF_HOME` 환경변수로 루트를 변경합니다.

### 임베딩 모델 변경

기본 모델은 `Qwen/Qwen3-Embedding-0.6B` (컨텍스트 4096)입니다. 다른 모델로 옮기려면 환경변수를 사용합니다:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

> **주의**: 임베딩 모델의 벡터 차원이 기존과 다르면 `memories.db`·`graph_db_store/`의 기존 벡터와 호환되지 않습니다. 모델 변경 후에는 대상 워크스페이스를 다시 인덱싱해야 합니다.

### Hook 통합 (양쪽 어댑터)

Codex/Claude hook은 사용하는 클라이언트의 hook 설정 방식에 맞춰 등록합니다. MCP 서버는 아래 `bin\cortex-mcp.exe` 바이너리를 기준으로 등록합니다.

### MCP 등록

MCP 등록은 빌드된 Rust `cortex-mcp` 바이너리를 기준으로 합니다. `CORTEX_WORKSPACE`, `CORTEX_DATA_HOME`, 필요 시 `CORTEX_WORKSPACE_KEY`를 명시해 플랫폼별 데이터 경로가 갈라지지 않게 합니다.

```powershell
$CORTEX_WORKSPACE = (Resolve-Path ..\your-project).Path

gemini mcp add -s user `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  -e CORTEX_DATA_HOME="$env:USERPROFILE\.cortex" `
  cortex-mcp -- ".\bin\cortex-mcp.exe"
```

---

## CI 검증 범위

GitHub Actions는 Windows와 Ubuntu에서 다음을 검증합니다.

- `uv sync --group dev` 기반 의존성 설치
- Rust workspace build/test
- embedding worker/provider import smoke
- 유지 Python 테스트
- `.cortex` 기준 테스트 워크스페이스 인덱싱
- Rust MCP JSON-RPC smoke test

장시간 daemon 실기동, 실제 GPU/CUDA 메모리 동작, 로컬 모델 캐시 상태는 환경 의존성이 높아 로컬 검증 대상으로 둡니다. 실측 절차는 [OS Validation Runbook](./docs/runbook-os-validation.md)에 정리합니다.

---

## 릴리즈 패키지 구성

Windows 릴리즈 zip은 설치자가 바로 실행하기 쉽도록 `bin\` 아래에 실행 파일을 묶습니다. 패키지 루트에는 Python worker 소스와 설치 스크립트를 같이 둡니다.

```text
Cortex-agents_infra/
  install.ps1
  pyproject.toml
  uv.lock
  src/cortex/runtime/engine_worker.py
  src/cortex/embeddings/...
  bin/cortex-ctl.exe
  bin/cortex-engine.exe
  bin/cortex-watcher.exe
  bin/cortex-mcp.exe
```

릴리즈 패키지를 만들 때는 위 파일과 README/INSTALL 문서를 함께 압축합니다.

---

## 영감 및 참고

- **Vexp**: 범용 워크플로 프레임워크 구조와 DB 스키마 형식 참고
- **oh-my-agent**: 역할 기반 에이전트 전문화 및 포터블 에이전트 정의 개념
- **oh-my-claudecode**: 심층 인터뷰와 아티팩트 기반 핸드오프 패턴
- **oh-my-openagent**: 해시 기반 정밀 편집과 검증 루프 패턴

---

## 라이선스

- **Code**: [MIT License](LICENSE)
- **Knowledge**: 외부 지식 라이브러리의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.

