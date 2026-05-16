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
