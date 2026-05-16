# Cortex Agent - Installation Guide

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/) package manager

Install uv first if it is not available:

```bash
# WSL / Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
iwr -useb https://astral.sh/uv/install.ps1 | iex
```

---

## 1. Quick Start (Global Install - Recommended)

Install Cortex once as a global tool. Workspace data is isolated under `~/.cortex/workspaces/<key>/`, so user projects do not carry Cortex runtime files.

```bash
# 1) Install Cortex globally.
#    PATH gets cortex-ctl, cortex-codex-hook, cortex-claude-hook,
#    cortex-mcp, and cortex-index.
uv tool install "git+https://github.com/kth3/Cortex-agents_infra.git"

# 2) Install Codex + Claude Code hooks and initialize the data directory.
cortex-ctl bootstrap --include-all

# 3) Optional: save an HF token and pre-download embedding models.
cortex-ctl bootstrap --include-all \
    --hf-token <YOUR_HF_TOKEN> \
    --warm-models

# 4) Optional: expand the bundled knowledge seed.
cortex-ctl bootstrap --include-all --enable-knowledge
```

### Update

```bash
uv tool upgrade cortex-agent
```

`uv` reinstalls from the same source and keeps data under `~/.cortex/workspaces/<key>/`.

### Uninstall

```bash
uv tool uninstall cortex-agent

# Remove data too, if desired.
rm -rf ~/.cortex
```

---

## 2. Development Mode (Source Checkout)

Use this mode only when editing Cortex itself.

```bash
# 1) Clone the repository.
git clone https://github.com/kth3/Cortex-agents_infra.git
cd Cortex-agents_infra

# 2) Install standard dependencies.
uv sync

# 3) Optional Linux NVIDIA GPU acceleration extras.
uv sync --extra gpu-accel

# 4) Run local entrypoints.
uv run cortex-ctl bootstrap --include-all
uv run cortex-index --force
```

When running from WSL2, prefer a Linux-home checkout such as `~/src/...`. Advisory locks can be less reliable on mounted Windows drives such as `/mnt/c/...`.

---

## 3. Path Model

| Environment variable | Meaning | Default |
|---|---|---|
| `CORTEX_HOME` | Cortex package/runtime root | Auto-detected from uv tool install or source checkout |
| `CORTEX_WORKSPACE` | Project root to index/edit | Walk upward from cwd to `.git` |
| `CORTEX_DATA_HOME` | Global data root for workspace DBs/indexes | `~/.cortex` |
| `CORTEX_WORKSPACE_KEY` | Shared key for grouping multiple folders | sha1 of workspace absolute path |
| `CORTEX_ENV_PATH` | Explicit dotenv path | unset |

Indexes (`memories.db`, `graph_db_store/`) and history are isolated under `<CORTEX_DATA_HOME>/workspaces/<key>/`.

---

## 4. HuggingFace Tokens

| Method | Behavior | Priority |
|---|---|---|
| `cortex-ctl bootstrap --hf-token <T>` | Upserts `HF_TOKEN=<T>` into `~/.cortex/.env` | 1 |
| Shell `HF_TOKEN=<T>` | Uses the shell environment | 2 |
| `huggingface-cli login` | Uses `~/.cache/huggingface/token` | 3 |

Only one method is needed. Public models work without a token. Cortex passes `token=None` when `HF_TOKEN` is unset or blank, so the HuggingFace library can still use the standard cached-token fallback.

The default model cache is `~/.cache/huggingface/hub/`. Set `HF_HOME` to move it.

---

## 5. Embedding Model Changes

Default:

```text
Qwen/Qwen3-Embedding-0.6B
max_seq_length = 4096
```

Change model and context window:

```bash
cortex-ctl bootstrap \
    --embedding-model google/embeddinggemma-300m \
    --embedding-max-seq-length 2048 \
    --warm-models
```

Or set environment variables directly:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

`trust_remote_code` is disabled by default. The default Qwen model requires it, so enable it explicitly after reviewing the model repository:

```bash
export CORTEX_EMBEDDING_TRUST_REMOTE_CODE=true
```

If vector dimensions differ from the previous model, rebuild the index:

```bash
cortex-index --force
```

---

## 6. MCP Server Registration

Codex and Claude Code hooks are installed by `cortex-ctl bootstrap`; no separate MCP registration is required for those integrations. Other CLIs can register the MCP server manually.

### Gemini CLI Example

```powershell
$CORTEX_HOME = (uv tool dir) + "\cortex-agent"
$CORTEX_WORKSPACE = "C:\path\to\your\workspace"

gemini mcp add -s user `
  -e CORTEX_HOME="$CORTEX_HOME" `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  cortex-mcp -- cortex-mcp
```

Use shell variables and `export` on Linux/WSL.

### Migration for Old Workspace-local Data

If an older install stored data under `<workspace>/.cortex/data/`, migrate once:

```bash
cortex-ctl migrate --dry-run
cortex-ctl migrate
```

---

## 7. Optional Local Daemon

Set this in `.env` to start an additional local daemon after the engine server is ready:

```env
CORTEX_LOCAL_DAEMON=path/to/daemon.py
```

Relative paths are resolved from `CORTEX_HOME`.

---

## 8. Validation

For development-mode validation:

```bash
# Dependency check
uv sync

# Compile all Python files
uv run python - <<'PY'
from pathlib import Path
import py_compile
for path in Path('scripts').rglob('*.py'):
    py_compile.compile(str(path), doraise=True)
print('py_compile ok')
PY

# Unit regression tests
uv run python -m pytest scripts/cortex/tests/ -q --ignore=scripts/cortex/tests/test_mcp_smoke.py

# Runtime control
uv run cortex-ctl status
uv run cortex-ctl stop
uv run cortex-ctl start
```

If the embedding model is not cached, the first model-backed run may download it. Use `--warm-models` to pre-download.

---

## License

- **Code**: [MIT License](LICENSE)
- **Knowledge**: The bundled knowledge seed originates from [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills) and follows the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license.
