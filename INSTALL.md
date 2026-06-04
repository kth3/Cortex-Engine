# Cortex Agent 설치 가이드

환경별 섹션 하나만 골라 위에서 아래로 실행합니다.

Cortex 본체 설치 위치와 작업 프로젝트 데이터 위치는 다릅니다.

- Cortex 본체: 사용자가 정한 도구 설치 폴더에 clone하고 빌드합니다.
- 작업 프로젝트 데이터: 기본적으로 전역 데이터 루트 `~/.cortex/workspaces/<workspace-key>/` 아래에 생성됩니다.
- 즉, 아래 명령은 Cortex를 도구처럼 설치한 뒤 PATH에 올리는 흐름입니다.

## 1. WSL / Linux에서 설치

Ubuntu/WSL 터미널에서 실행합니다.

```bash
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libsqlite3-dev

curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

mkdir -p "$HOME/.local/share"
git clone https://github.com/kth3/Cortex-agents_infra.git "$HOME/.local/share/cortex"
cd "$HOME/.local/share/cortex"

uv sync

CMAKE_BUILD_PARALLEL_LEVEL=1 cargo build --manifest-path rust/Cargo.toml --release -j 1 \
  -p cortex-ctl \
  -p cortex-runtime \
  -p cortex-watcher \
  -p cortex-mcp

export PATH="$HOME/.local/share/cortex/rust/target/release:$PATH"
cortex status
```

다른 터미널에서도 쓰려면 shell rc 파일에 PATH를 추가합니다.

```bash
echo 'export PATH="$HOME/.local/share/cortex/rust/target/release:$PATH"' >> ~/.bashrc
```

작업 프로젝트에서 사용:

```bash
cd /path/to/your-project
cortex status
```

`Engine Server: STOPPED`, `Watcher Daemon: STOPPED`가 보이면 설치와 실행 확인이 끝난 상태입니다.

## 2. Windows PowerShell에서 설치

PowerShell에서 실행합니다. 먼저 다음 도구가 필요합니다.

- Python 3.12
- Git
- Rust stable toolchain + `x86_64-pc-windows-msvc`
- Visual Studio Build Tools의 C++ 빌드 도구
- CMake
- Ninja
- SQLite 개발 라이브러리

```powershell
iwr -useb https://astral.sh/uv/install.ps1 | iex

$CortexHome = "$env:LOCALAPPDATA\Cortex"
git clone https://github.com/kth3/Cortex-agents_infra.git $CortexHome
cd $CortexHome

uv sync

cargo build --manifest-path rust/Cargo.toml --release `
  -p cortex-ctl `
  -p cortex-runtime `
  -p cortex-watcher `
  -p cortex-mcp

$env:PATH = "$(Resolve-Path ./rust/target/release);$env:PATH"
cortex status
```

다른 터미널에서도 쓰려면 사용자 PATH에 빌드 경로를 추가합니다.

```powershell
$Bin = Join-Path $CortexHome "rust\target\release"
[Environment]::SetEnvironmentVariable("Path", "$([Environment]::GetEnvironmentVariable('Path', 'User'));$Bin", "User")
```

작업 프로젝트에서 사용:

```powershell
cd C:\path\to\your-project
cortex status
```

`Engine Server: STOPPED`, `Watcher Daemon: STOPPED`가 보이면 설치와 실행 확인이 끝난 상태입니다.

현재 `cortex` 명령 표면:

```text
cortex start | stop | restart | status
cortex relay acquire | release | status | force-release
```

인덱싱 명령 표면:

```text
cortex index                 # 현재 프로젝트 인덱싱
cortex index --force         # 강제 재인덱싱
cortex index scan            # 인덱싱 대상 파일만 확인
cortex index roots           # 인덱싱 루트 목록
cortex index add <path>      # 인덱싱 루트 추가
cortex index add <path> --alias <name>
cortex index remove <target>
cortex index file <path>     # 단일 파일 인덱싱
cortex watch                 # 현재 프로젝트 감시
```

---

## 3. 임베딩 모델과 HuggingFace 토큰


임베딩은 Cortex의 기본 기능입니다. 별도 선택 기능이 아닙니다.
첫 실행 시 기본 모델 `Qwen/Qwen3-Embedding-0.6B`가 HuggingFace 캐시에 다운로드됩니다.

공개 모델만 사용할 때는 토큰이 없어도 동작합니다. 게이트 모델, 인증 다운로드, 다운로드 제한 완화가 필요하면 사용하는 환경에서 먼저 설정합니다.

WSL / Linux:

```bash
export HF_TOKEN=<token>
```

Windows PowerShell:

```powershell
$env:HF_TOKEN = "<token>"
```

토큰 저장 방식:

| 방식 | 동작 |
|---|---|
| `HF_TOKEN` 환경변수 | 현재 셸 또는 사용자 환경변수로 토큰 제공 |
| `huggingface-cli login` | HuggingFace 표준 캐시 위치에 토큰 저장 |
| 직접 `.env` 관리 | 사용하는 셸/실행 스크립트에서 로드 |

모델 캐시 기본 위치는 Linux/WSL/macOS에서 `~/.cache/huggingface/hub/`, Windows에서 `%USERPROFILE%\.cache\huggingface\hub\`입니다. 다른 위치를 쓰려면 `HF_HOME`을 설정합니다.

기본 모델:

```text
Qwen/Qwen3-Embedding-0.6B
max_seq_length = 4096
```

다른 모델로 바꾸려면 환경변수를 사용합니다.

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

```powershell
$env:CORTEX_EMBEDDING_MODEL = "google/embeddinggemma-300m"
$env:CORTEX_EMBEDDING_MAX_SEQ_LENGTH = "2048"
```

모델의 벡터 차원이 바뀌면 기존 벡터 인덱스와 호환되지 않습니다. 모델 변경 후에는 대상 워크스페이스를 다시 인덱싱해야 합니다.

---

## 4. GPU 가속 선택 사항

GPU 가속은 선택 사항입니다. NVIDIA RTX 3000번대 이상처럼 Ampere 이상 GPU에서 bf16/Flash-Attention 경로를 사용할 때만 추가합니다.

WSL / Linux:

```bash
uv sync --extra gpu-accel
```

이 extra는 Linux에서만 `flash-attn`을 추가합니다. PyTorch CUDA 12.4 wheel은 `pyproject.toml`의 `pytorch-cu124` index 설정을 따릅니다. 수동 wheel 다운로드나 별도 `pip install --index-url` 경로가 필요해지면 먼저 [DEPENDENCIES.md](./DEPENDENCIES.md)를 확인하십시오.

---

## 5. 경로 모델

| 환경변수 | 의미 | 기본 동작 |
|---|---|---|
| `CORTEX_WORKSPACE` | 실제 인덱싱/작업 대상 프로젝트 루트 | 없으면 현재 작업 디렉터리 |
| `CORTEX_DATA_HOME` | DB·그래프 인덱스 루트 | 기본값은 `~/.cortex`; 워크스페이스별 데이터는 `workspaces/<workspace-key>/` 아래에 저장 |
| `CORTEX_WORKSPACE_KEY` | 여러 checkout을 같은 데이터로 묶는 키 | 없으면 워크스페이스 절대경로 sha1 prefix |
| `CORTEX_PYTHON_EXECUTABLE` | embedding worker를 실행할 Python | 없으면 `python` |
| `CORTEX_PYTHON_FALLBACK` | Python fallback 경로 | 없으면 `python` |

같은 프로젝트에서 `cortex`, `cortex-mcp`, `cortex-watcher`를 섞어 실행할 때는 `CORTEX_WORKSPACE`와 필요 시 `CORTEX_WORKSPACE_KEY`를 명시적으로 맞추는 편이 안전합니다.

```powershell
$env:CORTEX_WORKSPACE = (Resolve-Path ..\your-project).Path
$env:CORTEX_DATA_HOME = "$env:USERPROFILE\.cortex"
```

---

## 6. MCP 서버 등록

MCP를 직접 등록할 때는 빌드된 `cortex-mcp` 바이너리와 환경변수를 명시합니다.

예: Gemini CLI, Windows PowerShell

```powershell
$CORTEX_WORKSPACE = (Resolve-Path ..\your-project).Path

gemini mcp add -s user `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  -e CORTEX_DATA_HOME="$env:USERPROFILE\.cortex" `
  cortex-mcp -- "./rust/target/release/cortex-mcp.exe"
```

---

## 7. 로컬 검증 절차

```powershell
# Python 의존성 및 문법 확인
uv sync
uv run python -m compileall -q src

# Python 유지 테스트
uv run --group dev python -m pytest src/cortex/tests -m "not smoke" -q

# Rust 전체 빌드/테스트
cargo test --manifest-path rust/Cargo.toml --workspace

# Rust MCP JSON-RPC smoke
uv run --group dev python -m pytest src/cortex/tests/test_mcp_smoke.py -q

# Windows PowerShell: 프로세스/포트/VRAM 진단
powershell -ExecutionPolicy Bypass -File scripts/diagnostics/zombie-check.ps1
```

`zombie-check.ps1`는 PATH의 `cortex` 또는 소스 체크아웃의
`rust\target\debug|release\cortex.exe`를 사용합니다. 포트 `42384`/`42385`가
이미 점유되어 있으면 기존 Cortex 프로세스를 정리한 뒤 다시 실행하십시오.

---

## 라이선스

- **Code**: [MIT License](LICENSE)
- **Knowledge**: 외부 지식 라이브러리의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
