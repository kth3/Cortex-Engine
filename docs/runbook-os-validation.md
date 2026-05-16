# OS Validation Runbook

이 문서는 Cortex 엔진화 이후 Windows/WSL에서 프로세스 종료, VRAM 해제, 설정 키 참조 상태를 확인하는 절차다. 실제 OS별 좀비/VRAM 판정은 로컬 환경 의존성이 커서 진단 스크립트 출력 전체를 증거로 보관한다.

## 1. Windows 프로세스/VRAM 검증

PowerShell에서 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/diagnostics/zombie-check.ps1
```

확인 기준:

- `cortex-ctl start` 이후 engine, worker, watcher PID가 출력된다.
- `cortex-ctl stop` 이후 앞서 기록한 PID가 남지 않아야 한다.
- 강제 종료 시나리오 이후 orphan Cortex PID가 없어야 한다.
- CUDA 환경이면 `nvidia-smi` before/after 출력에서 Cortex worker 점유 VRAM이 해제되어야 한다.

## 2. WSL/Linux 프로세스/VRAM 검증

bash에서 실행한다.

```bash
bash scripts/diagnostics/zombie-check.sh
```

확인 기준은 Windows와 같다. WSL2에서는 `/mnt/c/...` mounted drive보다 Linux home 아래 checkout에서 검증하는 것을 우선한다. `portalocker`는 OS advisory lock을 사용하므로 mounted drive에서는 lock 동작이 Windows/Linux 양쪽 파일시스템 semantics에 걸릴 수 있다.

## 3. 설정 키 참조 감사

설정 키를 바꾼 뒤에는 다음 검색으로 실제 참조 지점을 확인한다.

```bash
rg -n "settings\\.get|indexing_rules|index_roots|include_paths|exclude_paths|config_whitelist|linked_workspaces|idle_timeout|modules|tuning|get_tuning_params|CORTEX_EMBEDDING_MAX_SEQ_LENGTH|MAX_SEQ_LENGTH" scripts/cortex
```

현재 런타임 참조 상태:

| Key | Runtime reference |
| --- | --- |
| `CORTEX_DATA_HOME` | `cortex.paths.data_home()` |
| `CORTEX_WORKSPACE_KEY` | `cortex.paths.workspace_key()` |
| `CORTEX_EMBEDDING_MODEL` | `cortex.embeddings.provider._resolve_model_id()` |
| `CORTEX_EMBEDDING_MAX_SEQ_LENGTH` | `cortex.embeddings.provider._resolve_max_seq_length()` |
| `CORTEX_EMBEDDING_TRUST_REMOTE_CODE` | `cortex.embeddings.provider._resolve_trust_remote_code()` |
| `HF_TOKEN` | `cortex.embeddings.provider._resolve_hf_token()` |
| `CORTEX_START_TIMEOUT` | `cortex.runtime.control._resolve_start_timeout()` |
| `CORTEX_DIAG_READY_TIMEOUT` | `scripts/diagnostics/zombie-check.{sh,ps1}` 폴링 시간 |
| `CORTEX_LOCAL_DAEMON` | `cortex.runtime.local_daemon.resolve_local_daemon_script()` |
| `CORTEX_HOME`, `CORTEX_WORKSPACE`, `CORTEX_ENV_PATH` | `cortex.paths.*` (기존 경로 결정 로직) |
| `indexing_rules.idle_timeout` | `cortex.runtime.idle_monitor.get_idle_timeout()` |
| `indexing_rules.index_roots` | scanner discovery and MCP index-roots tools |
| `indexing_rules.include_paths` | scanner filters |
| `indexing_rules.exclude_paths` | scanner discovery and watcher filters |
| `indexing_rules.config_whitelist` | scanner filters |
| `indexing_rules.modules` | scanner filters |
| `tuning.mode`, `batch_size`, `max_chars`, `cache_clear_freq` | `cortex.config.tuning.get_tuning_params()` |

주의:

- `agent_behavior`와 `project_preferences`는 현재 Cortex runtime이 직접 소비하지 않는 agent-facing 설정이다.
- `indexing_rules.linked_workspaces`는 현재 runtime 참조가 없는 placeholder다. 기능 구현 전까지 동작 계약으로 취급하지 않는다.

## 4. HF 토큰 폴백 검증

`HF_TOKEN` 없이 HuggingFace cached token을 쓰는 경로는 다음 순서로 확인한다.

```bash
huggingface-cli login
unset HF_TOKEN
uv run cortex-ctl start
```

기대 결과:

- `HF_TOKEN`이 unset/blank여도 Cortex는 빈 문자열 token을 전달하지 않는다.
- HuggingFace client는 `~/.cache/huggingface/token` 또는 anonymous access로 fallback한다.
- 기본 Qwen 모델을 사용할 때 remote code가 필요하면 `CORTEX_EMBEDDING_TRUST_REMOTE_CODE=true`를 명시한다.

## 5. 자가 모드 (개발자가 `.cortex` 안에서 cortex 명령을 실행할 때)

cortex 코드 자체를 수정하면서 `.cortex` 폴더에서 `uv run cortex-ctl ...`을 호출하는 경우의 동작 규칙이다. 일반 사용자(글로벌 설치 + 자기 프로젝트 cwd) 시나리오에는 영향이 없다.

### 5.1 워크스페이스 키 결정

`cortex.paths.workspace_key()`는 `Path(workspace or os.getcwd()).resolve()`의 sha1 앞 12자를 키로 사용한다.

| 실행 위치 | 결정된 워크스페이스 키 |
|---|---|
| 사용자 프로젝트 루트(예: `~/projects/foo`) | sha1(`~/projects/foo`) |
| `.cortex` 폴더 자체(자체 git repo) | sha1(`<path>/.cortex`) |
| `cortex-ctl migrate --source <ws>` 호출 | `<ws>`에서 `_legacy_root_from()`이 `.cortex`를 찾아 그 부모를 `workspace_data_dir`에 넘긴다. 즉 `.cortex`의 부모 키 |

결과적으로 cortex 코드 안에서 `cortex-ctl bootstrap`을 호출한 키와 `cortex-ctl migrate --source <parent>`의 키가 다를 수 있다. 개발 모드에서는 `CORTEX_WORKSPACE_KEY` 환경변수를 명시 박아 일관성을 보장하는 것이 안전하다.

### 5.2 WSL2 mounted drive 경고

WSL2의 `/mnt/c/...` 경로 아래에서 cortex를 실행하면 `portalocker`의 advisory lock이 Windows/Linux 양쪽 파일시스템 semantics를 가로지른다. 안정성 검증은 반드시 Linux home(`~/...`) 아래 checkout에서 수행한다. mounted drive는 일시 작업·읽기 전용 검토에만 사용한다.

### 5.3 자가 모드 데이터 격리

`.cortex/.uv-cache-local`과 `.pytest_cache`, `*.egg-info/`는 `.gitignore`로 무시되지만, 글로벌 데이터(`~/.cortex/workspaces/<key>/`)는 자가 모드에서도 동일하게 사용된다. cortex 본체를 수정·테스트하면서 실제 메모리 DB가 글로벌 위치에 쌓이는 것을 막고 싶다면 `CORTEX_DATA_HOME`을 임시 디렉토리로 박는다.

```bash
CORTEX_DATA_HOME=/tmp/cortex-dev uv run cortex-ctl start
```
