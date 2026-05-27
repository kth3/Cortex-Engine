# Cortex Agent Installation Guide

This guide separates the actual installation paths for use and development.

- Release package: use the prebuilt Windows binaries. This is the preferred path for ordinary use.
- Source build: use this when creating a release package or editing Cortex itself.
- GPU acceleration: optional on top of either path, only for machines with suitable GPU support.

## 1. Requirements

Release package requirements:

- Python 3.12
- [uv](https://docs.astral.sh/uv/) package manager

Install uv if needed:

```bash
# WSL / Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
iwr -useb https://astral.sh/uv/install.ps1 | iex
```

Machines that build from source or create a release package also need:

- Git
- Rust stable toolchain
- Windows: Rust `x86_64-pc-windows-msvc` toolchain
- Windows: Visual Studio Build Tools with C++ build tools
- CMake
- Ninja

Check:

```powershell
rustc --version
cargo --version
cmake --version
ninja --version
```

---

## 2. Release Package Install

On Windows, download and extract the release zip. The package keeps the source-root layout:

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

Install:

```powershell
cd C:\path\to\Cortex-agents_infra

# Python embedding worker/provider dependencies
uv sync

# Use Cortex binaries in this PowerShell session
$env:PATH = "$PWD\rust\target\release;$env:PATH"
```

If the target workspace is not the Cortex repository, set it explicitly:

```powershell
$env:CORTEX_WORKSPACE = "C:\path\to\your\project"
```

Run a basic lifecycle check:

```powershell
cortex-ctl status
cortex-ctl start
cortex-ctl status
cortex-ctl stop
```

Current `cortex-ctl` command surface:

```text
cortex-ctl start | stop | restart | status
cortex-ctl relay acquire | release | status | force-release
```

---

## 3. Build From Source

Use this path when creating a release package or editing Cortex itself. The runtime result is the same as the release package; this path additionally builds the Rust binaries locally.

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

Create a Windows release zip from the generated binaries and Python runtime source:

```powershell
Compress-Archive -Force `
  -Path pyproject.toml, uv.lock, src, rust\target\release\cortex-ctl.exe, rust\target\release\cortex-engine.exe, rust\target\release\cortex-watcher.exe, rust\target\release\cortex-mcp.exe, README.md, INSTALL.md `
  -DestinationPath Cortex-agents_infra-windows-x64.zip
```

---

## 4. Embedding And GPU Options

The default install works without GPU extras. Search, memory, and MCP features can run, and the embedding model is downloaded and loaded on CPU or any available device on first use.

For NVIDIA Ampere-or-newer GPUs with bf16 and Flash-Attention, install the GPU extra on Linux or WSL:

```bash
uv sync --extra gpu-accel
```

This extra adds `flash-attn` on Linux only. The PyTorch CUDA 12.4 wheel follows the `pytorch-cu124` index configured in `pyproject.toml`. If you think you need manual wheel downloads or a separate `pip install --index-url` command, check [DEPENDENCIES.md](./DEPENDENCIES.md) first.

Set a smaller embedding model through environment variables:

```powershell
$env:CORTEX_EMBEDDING_MODEL = "google/embeddinggemma-300m"
$env:CORTEX_EMBEDDING_MAX_SEQ_LENGTH = "2048"
```

Linux/WSL:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

Changing vector dimensions makes the existing vector index incompatible. Rebuild the target workspace index after changing model family or vector dimension.

---

## 5. HuggingFace Tokens

Public models work without a token. For gated models or authenticated downloads, use one of these:

| Method | Behavior |
|---|---|
| `HF_TOKEN` environment variable | Provides the token to the current shell or user environment |
| `huggingface-cli login` | Stores the token in the standard HuggingFace cache |
| Managed `.env` or wrapper script | Loads the token before starting Cortex |

The default model cache is `~/.cache/huggingface/hub/` on Linux/WSL/macOS and `%USERPROFILE%\.cache\huggingface\hub\` on Windows. Set `HF_HOME` to move it.

---

## 6. Path Model

| Environment variable | Meaning | Default behavior |
|---|---|---|
| `CORTEX_WORKSPACE` | Project root to index/edit | Current working directory |
| `CORTEX_DATA_HOME` | DB and graph index root | Standalone MCP/watcher default to `~/.cortex`; `cortex-ctl start` passes `<workspace>/.cortex` to child processes |
| `CORTEX_WORKSPACE_KEY` | Shared key for grouping multiple checkouts | sha1 prefix of the workspace absolute path |
| `CORTEX_PYTHON_EXECUTABLE` | Python executable for the embedding worker | `python` |
| `CORTEX_PYTHON_FALLBACK` | Python fallback executable | `python` |

When mixing `cortex-ctl`, `cortex-mcp`, and `cortex-watcher` for the same project, set `CORTEX_WORKSPACE` and `CORTEX_DATA_HOME` explicitly so all processes use the same data location.

```powershell
$env:CORTEX_WORKSPACE = "C:\path\to\your\project"
$env:CORTEX_DATA_HOME = "$env:USERPROFILE\.cortex"
```

---

## 7. MCP Server Registration

Register MCP by pointing your client at the built `cortex-mcp` binary and passing the workspace environment explicitly.

Gemini CLI, Windows PowerShell example:

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

## 8. Local Validation

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

`zombie-check.ps1` uses `cortex-ctl` from PATH or the source checkout's `rust\target\debug|release\cortex-ctl.exe`. If ports `42384` or `42385` are already occupied, stop existing Cortex processes and rerun the diagnostic.

---
