# Cortex Agent 설치 가이드

이 문서는 실제 사용과 개발에 필요한 설치 경로를 구분해 안내합니다.

- 릴리즈 패키지 사용: 미리 빌드된 Windows 바이너리를 받아 설치합니다. 일반 사용자는 이 경로를 우선 사용합니다.
- 소스에서 직접 빌드: 릴리즈 패키지를 직접 만들거나 Cortex 코드를 수정할 때 사용합니다.
- GPU 가속: 위 두 경로와 별개로, 성능을 충족하는 GPU가 있는 환경에서만 추가합니다.

## 1. 사전 요구사항

릴리즈 패키지 사용 요구사항:

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 패키지 관리자

uv 설치:

```bash
# WSL / Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
iwr -useb https://astral.sh/uv/install.ps1 | iex
```

소스에서 직접 빌드하거나 릴리즈 패키지를 만드는 PC에는 추가로 필요합니다.

- Git
- Rust stable toolchain
- Windows: Rust `x86_64-pc-windows-msvc` toolchain
- Windows: Visual Studio Build Tools의 C++ 빌드 도구
- CMake
- Ninja

확인:

```powershell
rustc --version
cargo --version
cmake --version
ninja --version
```

---

## 2. 릴리즈 패키지 설치

Windows 사용자는 릴리즈 zip을 받아 압축을 풉니다. 패키지는 소스 루트 구조를 유지해야 합니다.

```text
Cortex-agents_infra/
  pyproject.toml
  src/cortex/runtime/engine_worker.py
  src/cortex/embeddings/...
  rust/target/release/cortex-ctl.exe
  rust/target/release/cortex-engine.exe
  rust/target/release/cortex-watcher.exe
  rust/target/release/cortex-mcp.exe
```

설치:

```powershell
cd C:\path\to\Cortex-agents_infra

# Python embedding worker/provider 의존성 설치
uv sync

# 현재 PowerShell 세션에서 Cortex 실행 파일 사용
$env:PATH = "$PWD\rust\target\release;$env:PATH"
```

작업 대상 프로젝트가 Cortex repo가 아니라 별도 프로젝트라면 명시합니다.

```powershell
$env:CORTEX_WORKSPACE = "C:\path\to\your\project"
```

동작 확인:

```powershell
cortex-ctl status
cortex-ctl start
cortex-ctl status
cortex-ctl stop
```

현재 `cortex-ctl` 명령 표면:

```text
cortex-ctl start | stop | restart | status
cortex-ctl relay acquire | release | status | force-release
```

---

## 3. 소스에서 직접 빌드

릴리즈 패키지를 직접 만들거나 Cortex 코드를 수정할 때 사용합니다. 일반 사용 절차와
결과물은 같고, 차이는 로컬에서 Rust 바이너리를 직접 만든다는 점입니다.

```powershell
git clone https://github.com/kth3/Cortex-agents_infra.git
cd Cortex-agents_infra

uv sync

cargo build --manifest-path rust/Cargo.toml --release `
  -p cortex-ctl `
  -p cortex-runtime `
  -p cortex-watcher `
  -p cortex-mcp

$env:PATH = "$PWD\rust\target\release;$env:PATH"
cortex-ctl status
```

릴리즈 zip을 만들 때는 위 빌드 산출물과 Python runtime 소스를 함께 묶습니다.

```powershell
Compress-Archive -Force `
  -Path pyproject.toml, uv.lock, src, rust\target\release\cortex-ctl.exe, rust\target\release\cortex-engine.exe, rust\target\release\cortex-watcher.exe, rust\target\release\cortex-mcp.exe, README.md, INSTALL.md `
  -DestinationPath Cortex-agents_infra-windows-x64.zip
```

---

## 4. 임베딩 및 GPU 선택 경로

기본 설치는 GPU extra 없이 동작합니다. 검색·메모리·MCP 기능은 사용할 수 있고,
임베딩 모델은 첫 사용 시 CPU 또는 사용 가능한 장치에서 다운로드·로드됩니다.

NVIDIA Ampere 이상 GPU가 있고 bf16/Flash-Attention 경로를 쓰려면 Linux 또는 WSL에서
GPU extra를 설치합니다.

```bash
uv sync --extra gpu-accel
```

이 extra는 Linux에서만 `flash-attn`을 추가합니다. PyTorch CUDA 12.4 wheel은
`pyproject.toml`의 `pytorch-cu124` index 설정을 따릅니다. 수동 wheel 다운로드나
별도 `pip install --index-url` 경로가 필요해지면 먼저 [DEPENDENCIES.md](./DEPENDENCIES.md)를 확인하십시오.

임베딩 모델을 바꾸려면 환경변수를 사용합니다.

```powershell
$env:CORTEX_EMBEDDING_MODEL = "google/embeddinggemma-300m"
$env:CORTEX_EMBEDDING_MAX_SEQ_LENGTH = "2048"
```

Linux/WSL:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

모델의 벡터 차원이 바뀌면 기존 벡터 인덱스와 호환되지 않습니다. 모델 변경 후에는 대상 워크스페이스를 다시 인덱싱해야 합니다.

---

## 5. HuggingFace 토큰

공개 모델만 사용할 때는 토큰이 없어도 동작합니다. 게이트 모델이나 인증된 다운로드가 필요하면 다음 중 하나를 사용합니다.

| 방식 | 동작 |
|---|---|
| `HF_TOKEN` 환경변수 | 현재 셸 또는 사용자 환경변수로 토큰 제공 |
| `huggingface-cli login` | HuggingFace 표준 캐시 위치에 토큰 저장 |
| 직접 `.env` 관리 | 사용하는 셸/실행 스크립트에서 로드 |

모델 캐시 기본 위치는 Linux/WSL/macOS에서 `~/.cache/huggingface/hub/`, Windows에서
`%USERPROFILE%\.cache\huggingface\hub\`입니다. 다른 위치를 쓰려면 `HF_HOME`을
설정합니다.

---

## 6. 경로 모델

| 환경변수 | 의미 | 기본 동작 |
|---|---|---|
| `CORTEX_WORKSPACE` | 실제 인덱싱/작업 대상 프로젝트 루트 | 없으면 현재 작업 디렉터리 |
| `CORTEX_DATA_HOME` | DB·그래프 인덱스 루트 | standalone MCP/watcher는 `~/.cortex`; `cortex-ctl start`는 `<workspace>/.cortex`를 하위 프로세스에 전달 |
| `CORTEX_WORKSPACE_KEY` | 여러 checkout을 같은 데이터로 묶는 키 | 없으면 워크스페이스 절대경로 sha1 prefix |
| `CORTEX_PYTHON_EXECUTABLE` | embedding worker를 실행할 Python | 없으면 `python` |
| `CORTEX_PYTHON_FALLBACK` | Python fallback 경로 | 없으면 `python` |

같은 프로젝트에서 `cortex-ctl`, `cortex-mcp`, `cortex-watcher`를 섞어 실행할 때는
`CORTEX_WORKSPACE`와 `CORTEX_DATA_HOME`을 명시적으로 맞추는 편이 안전합니다.

```powershell
$env:CORTEX_WORKSPACE = "C:\path\to\your\project"
$env:CORTEX_DATA_HOME = "$env:USERPROFILE\.cortex"
```

---

## 7. MCP 서버 등록

MCP를 직접 등록할 때는 빌드된 `cortex-mcp` 바이너리와 환경변수를 명시합니다.

예: Gemini CLI, Windows PowerShell

```powershell
$CORTEX_REPO = "C:\path\to\Cortex-agents_infra"
$CORTEX_WORKSPACE = "C:\path\to\your\project"
$CORTEX_MCP = "$CORTEX_REPO\rust\target\release\cortex-mcp.exe"

gemini mcp add -s user `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  -e CORTEX_DATA_HOME="$env:USERPROFILE\.cortex" `
  cortex-mcp -- "$CORTEX_MCP"
```

---

## 8. 로컬 검증 절차

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

`zombie-check.ps1`는 PATH의 `cortex-ctl` 또는 소스 체크아웃의
`rust\target\debug|release\cortex-ctl.exe`를 사용합니다. 포트 `42384`/`42385`가
이미 점유되어 있으면 기존 Cortex 프로세스를 정리한 뒤 다시 실행하십시오.

---

## 라이선스

- **Code**: [MIT License](LICENSE)
- **Knowledge**: 외부 지식 라이브러리의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
