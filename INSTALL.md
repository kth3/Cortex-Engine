# Cortex Agent — 설치 가이드

## 사전 요구사항

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 패키지 관리자

uv가 없으면 먼저 설치합니다:

```bash
# WSL / Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
iwr -useb https://astral.sh/uv/install.ps1 | iex
```

---

## 1. 빠른 시작 (글로벌 설치 — 권장)

cortex 본체는 한 번 글로벌로 깔고, 워크스페이스별 데이터는 `~/.cortex/workspaces/<key>/`에 자동 격리됩니다. 사용자 프로젝트 폴더에 cortex 흔적이 남지 않습니다.

```bash
# 1) cortex 본체 글로벌 설치
#    PATH에 cortex-ctl, cortex-codex-hook, cortex-claude-hook, cortex-mcp, cortex-index 등록
uv tool install "git+https://github.com/kth3/Cortex-agents_infra.git"

# 2) Codex + Claude Code hook 등록 + 워크스페이스 데이터 디렉토리 초기화
cortex-ctl bootstrap --include-all

# 3) HF 토큰 저장(선택) + 임베딩 모델 준비
cortex-ctl bootstrap --include-all \
    --hf-token <YOUR_HF_TOKEN> \
    --warm-models

# 4) (선택) 외부 지식 시드 전개
cortex-ctl bootstrap --include-all --enable-knowledge
```

### 업데이트

```bash
uv tool upgrade cortex-agent
```

uv가 동일 source(git URL)에서 default branch HEAD를 다시 받아 재설치합니다. `~/.cortex/workspaces/<key>/`의 데이터는 보존됩니다.

### 제거

```bash
uv tool uninstall cortex-agent
# 데이터까지 지우려면
rm -rf ~/.cortex
```

---

## 2. 임베딩·가속 선택

그래픽카드가 없거나 저사양이면 추가 GPU 설치를 하지 않는 기본 경로를 사용합니다. 이 경우에도 검색·메모리·MCP 기능은 동작하고, 임베딩 모델은 첫 사용 시 CPU 또는 사용 가능한 장치에서 자동 다운로드·로드됩니다. 즉, HF 토큰과 `--warm-models`는 선택이지만 임베딩 모델 준비 자체는 관련 기능에 필요합니다.

저사양 환경에서 기본 모델이 부담되면 더 작은 임베딩 모델로 바꿉니다:

```bash
cortex-ctl bootstrap \
    --embedding-model google/embeddinggemma-300m \
    --embedding-max-seq-length 2048
```

NVIDIA Ampere 이상 GPU가 있고 bf16/Flash-Attention까지 쓰려면 Linux 또는 WSL의 소스 체크아웃에서 GPU extra를 설치합니다:

```bash
uv sync --extra gpu-accel
```

이 extra는 `flash-attn`을 추가하고, PyTorch CUDA 12.4 wheel은 `pyproject.toml`의 `pytorch-cu124` index 설정을 따릅니다. 수동 wheel 다운로드나 별도 `pip install --index-url` 경로가 필요해지면 먼저 [DEPENDENCIES.md](./DEPENDENCIES.md)를 확인하십시오.

---

## 3. 개발 모드 (소스 트리에서 직접 작업)

cortex 코드 자체를 수정·기여할 때 사용합니다.

```bash
# 1) 저장소 클론
git clone https://github.com/kth3/Cortex-agents_infra.git
cd Cortex-agents_infra

# 2) 기본 의존성 설치
uv sync

# 3) 로컬 entry point로 호출
uv run cortex-ctl bootstrap --include-all
uv run cortex-index --force
```

GPU extra, 저사양 모델 변경, bf16/Flash-Attention 경로는 바로 앞의 `2. 임베딩·가속 선택`과 [DEPENDENCIES.md](./DEPENDENCIES.md)를 따릅니다.

---

## 4. 경로 모델

| 환경변수 | 의미 | 기본값 |
|---|---|---|
| `CORTEX_HOME` | cortex 본체(pyproject.toml 위치) | 자동 탐색 (uv tool 설치 venv 또는 cwd의 `.cortex`) |
| `CORTEX_WORKSPACE` | 실제 인덱싱/작업 대상 프로젝트 루트 | cwd에서 `.git` 위로 탐색 |
| `CORTEX_DATA_HOME` | 워크스페이스별 DB·인덱스가 저장되는 글로벌 루트 | `~/.cortex` |
| `CORTEX_WORKSPACE_KEY` | 멀티레포 그룹화 — 여러 폴더를 한 워크스페이스로 묶을 때 동일 값 박기 | (없음, 워크스페이스 절대경로 sha1 자동) |
| `CORTEX_ENV_PATH` | `.env` 파일 위치 명시 | (없음) |
| `CORTEX_START_TIMEOUT` | `cortex-ctl start`가 엔진을 기다리는 시간(초). WSL/CUDA 환경에선 60~120 권장 | 35 |
| `CORTEX_DIAG_READY_TIMEOUT` | 진단 스크립트(`zombie-check.{sh,ps1}`)가 READY 도달까지 폴링하는 시간(초) | 90 |

코드 인덱스(`memories.db`, `graph_db_store/`)와 히스토리는 `<CORTEX_DATA_HOME>/workspaces/<key>/` 아래에 격리됩니다.

---

## 5. HuggingFace 토큰

| 방식 | 동작 | 우선순위 |
|---|---|---|
| `cortex-ctl bootstrap --hf-token <T>` | `~/.cortex/.env`에 `HF_TOKEN=<T>` upsert | 1 |
| 셸 `export HF_TOKEN=<T>` | rc에 추가 | 2 |
| `huggingface-cli login` (1회) | `~/.cache/huggingface/token` 표준 위치 | 3 |

셋 중 하나만 해두면 cortex가 자동 인식합니다. 공개 모델만 쓸 때는 토큰이 없어도 동작합니다.

모델 캐시 기본 위치는 `~/.cache/huggingface/hub/`. 다른 위치를 쓰려면 `HF_HOME` 환경변수로 변경합니다.

---

## 6. 임베딩 모델 변경

기본 모델은 `Qwen/Qwen3-Embedding-0.6B` (컨텍스트 4096)입니다. 다른 모델로 옮기려면:

```bash
cortex-ctl bootstrap \
    --embedding-model google/embeddinggemma-300m \
    --embedding-max-seq-length 2048 \
    --warm-models
```

또는 환경변수로 직접:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

> **주의**: 벡터 차원이 기존과 다르면 `memories.db`·`graph_db_store/`의 기존 벡터와 호환되지 않습니다. 모델 변경 후 한 번 재인덱싱이 필요합니다:
>
> ```bash
> cortex-index --force
> ```
>
> 자세한 정책은 `rules/core/indexing-policy.md`를 참고하십시오.

---

## 7. MCP 서버 등록

Codex와 Claude Code는 `cortex-ctl bootstrap`이 hook을 자동 등록합니다(별도 MCP 등록 불필요). 그 외 CLI는 다음과 같이 수동 등록합니다.

### Gemini CLI

```powershell
$CORTEX_HOME = (uv tool dir)\cortex-agent
$CORTEX_WORKSPACE = "C:\path\to\your\workspace"

gemini mcp add -s user `
  -e PYTHONPATH="$CORTEX_HOME\Lib\site-packages" `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- cortex-mcp
```

Linux/WSL은 PowerShell 변수 대신 `$()`/`export`를 사용합니다.

### 마이그레이션 (구버전 사용자만)

기존 `<ws>/.cortex/data/`에 데이터가 있던 사용자는 다음을 1회 실행하여 글로벌 위치로 이동시킵니다:

```bash
cortex-ctl migrate --dry-run    # 이동 계획 확인
cortex-ctl migrate              # 실제 이동
```

---

## 8. 검증 절차

CI와 동일한 방향으로 로컬 검증하려면 (개발 모드에서):

```powershell
# 의존성 확인
uv sync

# Python 패키지 문법 확인
uv run python -m compileall -q src

# 회귀 테스트
uv run --group dev python -m pytest src/cortex/tests -m "not smoke" -q

# Rust MCP JSON-RPC smoke
uv run --group dev python -m pytest src/cortex/tests/test_mcp_smoke.py -q

# Rust 런타임 제어 바이너리 빌드
cargo build --manifest-path rust/Cargo.toml -p cortex-ctl -p cortex-runtime -p cortex-watcher

# Windows PowerShell: 프로세스/포트/VRAM 진단
powershell -ExecutionPolicy Bypass -File scripts/diagnostics/zombie-check.ps1
```

Windows 소스 체크아웃에서 `uv run cortex-ctl`은 Python entrypoint가 없으면 실패할 수 있으므로, 개발 모드 런타임 검증은 빌드된 `rust\target\debug\cortex-ctl.exe`를 기준으로 봅니다. `42384`/`42385` 포트가 이미 점유되어 있으면 먼저 기존 Cortex 프로세스를 정리한 뒤 재실행하십시오.

임베딩 모델 캐시가 없는 환경에서는 첫 실행 시 모델 다운로드가 발생할 수 있습니다(`--warm-models`로 사전 처리 가능). CI에서는 문법/import/회귀/MCP smoke를 중점 검증하고, 장시간 GPU/daemon 실기동 검증은 로컬 검증 대상으로 둡니다. OS별 프로세스/VRAM 실측 절차는 [OS Validation Runbook](./docs/runbook-os-validation.md)을 따릅니다. WSL 전환 후에는 Windows mounted drive(`/mnt/c/...`)보다 Linux home checkout에서 같은 절차를 다시 검증하는 편이 안전합니다.

---

## 라이선스

- **Code**: [MIT License](LICENSE)
- **Knowledge**: 외부 지식 라이브러리의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
