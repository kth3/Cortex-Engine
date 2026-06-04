# Cortex Agent Installation Guide

Pick one environment section and run it from top to bottom.

The Cortex install location and the project data location are different.

- Cortex install: clone and build Cortex in a tool directory you choose.
- Project data: created under the global data root `~/.cortex/workspaces/<workspace-key>/` by default.
- In short, the commands below install Cortex as a tool and put its binaries on PATH.

## 1. Install On WSL / Linux

Run this in an Ubuntu/WSL terminal.

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

Add the PATH entry to your shell rc file for new terminals.

```bash
echo 'export PATH="$HOME/.local/share/cortex/rust/target/release:$PATH"' >> ~/.bashrc
```

Use it from a project:

```bash
cd /path/to/your-project
cortex status
```

If `Engine Server: STOPPED` and `Watcher Daemon: STOPPED` are shown, install and runtime lookup are working.

## 2. Install On Windows PowerShell

Run this in PowerShell. These tools must already be installed.

- Python 3.12
- Git
- Rust stable toolchain + `x86_64-pc-windows-msvc`
- Visual Studio Build Tools with C++ build tools
- CMake
- Ninja
- SQLite development library

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

Add the build output to the user PATH for new terminals.

```powershell
$Bin = Join-Path $CortexHome "rust\target\release"
[Environment]::SetEnvironmentVariable("Path", "$([Environment]::GetEnvironmentVariable('Path', 'User'));$Bin", "User")
```

Use it from a project:

```powershell
cd C:\path\to\your-project
cortex status
```

If `Engine Server: STOPPED` and `Watcher Daemon: STOPPED` are shown, install and runtime lookup are working.

Current `cortex` command surface:

```text
cortex start | stop | restart | status
cortex relay acquire | release | status | force-release
```

Index command surface:

```text
cortex index                 # index the current project
cortex index --force         # force re-indexing
cortex index scan            # preview selected files
cortex index roots           # list indexing roots
cortex index add <path>      # add an indexing root
cortex index add <path> --alias <name>
cortex index remove <target>
cortex index file <path>     # index one file
cortex watch                 # watch the current project
```

---

## 3. Embedding Model And HuggingFace Token


Embedding is a core Cortex feature. It is not optional.
On first use, the default model `Qwen/Qwen3-Embedding-0.6B` is downloaded into the HuggingFace cache.

Public models work without a token. If you use gated models, authenticated downloads, or want fewer download limits, set the token in the environment you are using.

WSL / Linux:

```bash
export HF_TOKEN=<token>
```

Windows PowerShell:

```powershell
$env:HF_TOKEN = "<token>"
```

Token storage options:

| Method | Behavior |
|---|---|
| `HF_TOKEN` environment variable | Provides the token to the current shell or user environment |
| `huggingface-cli login` | Stores the token in the standard HuggingFace cache |
| Managed `.env` or wrapper script | Loads the token before starting Cortex |

The default model cache is `~/.cache/huggingface/hub/` on Linux/WSL/macOS and `%USERPROFILE%\.cache\huggingface\hub\` on Windows. Set `HF_HOME` to move it.

Default model:

```text
Qwen/Qwen3-Embedding-0.6B
max_seq_length = 4096
```

Override through environment variables:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

```powershell
$env:CORTEX_EMBEDDING_MODEL = "google/embeddinggemma-300m"
$env:CORTEX_EMBEDDING_MAX_SEQ_LENGTH = "2048"
```

Changing vector dimensions makes the existing vector index incompatible. Rebuild the target workspace index after changing model family or vector dimension.

---

## 4. Optional GPU Acceleration

GPU acceleration is optional. Add it only for NVIDIA RTX 3000-series or newer GPUs, where Ampere-or-newer bf16/Flash-Attention paths are available.

WSL / Linux:

```bash
uv sync --extra gpu-accel
```

This extra adds `flash-attn` on Linux only. The PyTorch CUDA 12.4 wheel follows the `pytorch-cu124` index configured in `pyproject.toml`. If you think you need manual wheel downloads or a separate `pip install --index-url` command, check [DEPENDENCIES.md](./DEPENDENCIES.md) first.

---

## 5. Path Model

| Environment variable | Meaning | Default behavior |
|---|---|---|
| `CORTEX_WORKSPACE` | Project root to index/edit | Current working directory |
| `CORTEX_DATA_HOME` | DB and graph index root | Defaults to `~/.cortex`; workspace data is stored under `workspaces/<workspace-key>/` |
| `CORTEX_WORKSPACE_KEY` | Shared key for grouping multiple checkouts | sha1 prefix of the workspace absolute path |
| `CORTEX_PYTHON_EXECUTABLE` | Python executable for the embedding worker | `python` |
| `CORTEX_PYTHON_FALLBACK` | Python fallback executable | `python` |

When mixing `cortex`, `cortex-mcp`, and `cortex-watcher` for the same project, set `CORTEX_WORKSPACE` and optionally `CORTEX_WORKSPACE_KEY` explicitly so all processes use the same workspace key.

```powershell
$env:CORTEX_WORKSPACE = (Resolve-Path ..\your-project).Path
$env:CORTEX_DATA_HOME = "$env:USERPROFILE\.cortex"
```

---

## 6. MCP Server Registration

Register MCP by pointing your client at the built `cortex-mcp` binary and passing the workspace environment explicitly.

Gemini CLI, Windows PowerShell example:

```powershell
$CORTEX_WORKSPACE = (Resolve-Path ..\your-project).Path

gemini mcp add -s user `
  -e CORTEX_WORKSPACE="$CORTEX_WORKSPACE" `
  -e CORTEX_DATA_HOME="$env:USERPROFILE\.cortex" `
  cortex-mcp -- "./rust/target/release/cortex-mcp.exe"
```

---

## 7. Local Validation

```powershell
# Python dependencies and syntax
uv sync
uv run python -m compileall -q src

# Maintained Python tests
uv run --group dev python -m pytest src/cortex/tests -m "not smoke" -q

# Rust workspace build/test
cargo test --manifest-path rust/Cargo.toml --workspace

# Rust MCP JSON-RPC smoke
uv run --group dev python -m pytest src/cortex/tests/test_mcp_smoke.py -q

# Windows PowerShell: process, port, and VRAM diagnostics
powershell -ExecutionPolicy Bypass -File scripts/diagnostics/zombie-check.ps1
```

`zombie-check.ps1` uses `cortex` from PATH or the source checkout's `rust\target\debug|release\cortex.exe`. If ports `42384` or `42385` are already occupied, stop existing Cortex processes and rerun the diagnostic.

---
