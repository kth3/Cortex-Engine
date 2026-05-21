[Korean Version Available](README.md)

# Cortex Agent Infrastructure (`.cortex`)

**"The Bridge between Human Intent and Agent Intelligence."**

Cortex is a local-first agent infrastructure for persistent memory, semantic code search, graph analysis, and MCP integration. The current model installs Cortex once as a global tool and stores per-workspace data under `~/.cortex/workspaces/<key>/`, so user projects do not need to carry Cortex runtime files.

---

## System Architecture

The MCP server, tool dispatcher, vector engine server, embedding worker, watcher, and runtime control layers are separated. `cortex-ctl` owns start/status/stop orchestration, while the embedding model is isolated in a worker process.

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
      Entry["MCP Entry Point<br/>Receives requests · Returns responses"]
      Router["Request Router<br/>Input validation · Capability routing"]
      Handler["Capability Handlers<br/>Search · Memory · Indexing · Edit · Session"]
    end

    subgraph Retrieval["Retrieval Layer"]
      direction TB
      Plan["Query Planning<br/>Scope · Filters · Intent normalization"]
      Search["Hybrid Retrieval<br/>Keyword + Vector + Structure search"]
      Format["Rank & Format<br/>Merge candidates · Rank context · Include locations"]
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
      FileSelect["File Selection<br/>Workspace scan · Scope filtering · Changed files"]
      Extract["Parse & Extract<br/>Symbols · References · Call relations"]
      Chunk["Chunk & Metadata<br/>Search units · Line ranges · Source context"]
      GraphSync["Graph Sync<br/>Update code structure graph"]
    end

    subgraph Runtime["Runtime Layer"]
      direction TB
      RuntimeService["Runtime Service<br/>Long-running process · Job broker"]
      EmbeddingWorker["Embedding Worker<br/>Generate text embeddings"]
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
    SQLVector[("Physical Store 1<br/>SQLite + Text Index + Vector<br/>Files · Chunks · Memory · Symbols · Edges · Vectors")]
    GraphDB[("Physical Store 2<br/>Kuzu Graph Store<br/>Code graph nodes · Relations")]
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
---

## Key Features

### 1. Hybrid Context Engine

- **Tree-sitter parsing**: extracts classes, functions, and call relationships from Python, C#, TypeScript, and related source files.
- **Vector search**: uses `sqlite-vec` for local semantic search.
- **Graph analysis**: stores call and containment relationships in Kuzu.
- **FTS5 search**: combines keyword search with Reciprocal Rank Fusion scoring.

### 2. Runtime Modularization

The runtime is split into path resolution, IPC, process launch, locks, logging, control, engine routing, worker lifecycle, and watcher launch modules under `cortex/runtime/`. This keeps GPU/PyTorch dependencies inside the embedding worker and leaves control/server/router code relatively lightweight.

### 3. Global Data Model

- `CORTEX_HOME`: Cortex package/runtime root.
- `CORTEX_WORKSPACE`: project root to index and edit.
- `CORTEX_DATA_HOME`: global data root, default `~/.cortex`.
- `CORTEX_WORKSPACE_KEY`: optional shared key for grouping multiple folders into one Cortex workspace.
- `CORTEX_ENV_PATH`: explicit dotenv path.
- `CORTEX_START_TIMEOUT`: seconds `cortex-ctl start` waits for the engine. Default 35; use 60-120 on WSL/CUDA. If the deadline elapses while the engine is still `loading`, `start` emits an INFO note and returns success while the engine keeps loading in the background.
- `CORTEX_DIAG_READY_TIMEOUT`: seconds the diagnostic scripts (`zombie-check.{sh,ps1}`) poll for `READY` before accepting `LOADING`. Default 90.

Code indexes, memory DBs, graph stores, and session history live under `<CORTEX_DATA_HOME>/workspaces/<key>/`. The default key is derived from the workspace path; set `CORTEX_WORKSPACE_KEY` when multiple repositories should share one Cortex data directory.

---

## Directory Model

```text
.cortex/                                  # Cortex source/package root
├── hooks/                                # runtime lifecycle hooks
├── rules/                                # agent rules and editing policies
├── scripts/                              # Cortex modules, MCP server, runtime control
├── knowledge/
│   └── knowledge.zip                     # optional knowledge seed
├── pyproject.toml                        # uv dependency declaration
└── settings.yaml                         # infrastructure settings

~/.cortex/                                # CORTEX_DATA_HOME
├── .env                                  # optional global Cortex environment
└── workspaces/
    └── <workspace-key>/
        ├── memories.db
        ├── graph_db_store/
        └── history/
```

---

## Installation

See [INSTALL.en.md](./INSTALL.en.md) for the full installation guide.

```bash
# Install Cortex once as a uv tool.
uv tool install "git+https://github.com/kth3/Cortex-agents_infra.git"

# Install supported Codex/Claude hooks and initialize the data directory.
cortex-ctl bootstrap --include-all

# Optional: save a HuggingFace token and warm the embedding model cache.
cortex-ctl bootstrap --include-all --hf-token <YOUR_HF_TOKEN> --warm-models
```

Update:

```bash
uv tool upgrade cortex-agent
```

Development mode from a source checkout:

```bash
uv sync
uv run cortex-ctl bootstrap --include-all
uv run cortex-index --force
```

---

## `cortex-ctl` Surface

```text
cortex-ctl start | stop | restart | status
cortex-ctl bootstrap [--include-all] [--enable-knowledge]
                     [--hf-token <T>] [--warm-models]
                     [--embedding-model <id>] [--embedding-max-seq-length <n>]
                     [--dry-run]
cortex-ctl knowledge enable | disable | status [--force]
cortex-ctl migrate [--source <workspace>] [--dry-run] [--force]
```

---

## HuggingFace Token Policy

Cortex does not require `HF_TOKEN` for public models. Use one of these methods only when a gated model or faster authenticated access is needed:

| Method | Behavior |
|---|---|
| `cortex-ctl bootstrap --hf-token <T>` | Stores `HF_TOKEN=<T>` in `~/.cortex/.env`. |
| `HF_TOKEN=<T>` environment variable | Uses the shell-provided token. |
| `huggingface-cli login` | Uses the standard `~/.cache/huggingface/token` file. |

The implementation passes `token=None` when `HF_TOKEN` is unset or blank, so the HuggingFace library can still use its standard cached-token fallback.

---

## Embedding Model Policy

Default model:

```text
Qwen/Qwen3-Embedding-0.6B
max_seq_length = 4096
```

Override through bootstrap:

```bash
cortex-ctl bootstrap \
  --embedding-model google/embeddinggemma-300m \
  --embedding-max-seq-length 2048 \
  --warm-models
```

Or through environment variables:

```bash
export CORTEX_EMBEDDING_MODEL=google/embeddinggemma-300m
export CORTEX_EMBEDDING_MAX_SEQ_LENGTH=2048
```

`trust_remote_code` is disabled by default. The default Qwen embedding model requires it, so enable it explicitly after reviewing the model code:

```bash
export CORTEX_EMBEDDING_TRUST_REMOTE_CODE=true
```

Changing embedding model dimensions makes existing vectors incompatible. Run a full reindex after changing model family or vector dimension:

```bash
cortex-index --force
```

---

## MCP Registration

Codex and Claude Code hooks are installed through `cortex-ctl bootstrap`. MCP entrypoints remain available for platforms that support MCP directly:

```bash
cortex-mcp
cortex-index <workspace> --force
```

When registering MCP manually, pass `CORTEX_HOME`, `CORTEX_WORKSPACE`, and optionally `CORTEX_WORKSPACE_KEY` explicitly so the server resolves the same workspace data directory across platforms.

---

## CI Coverage

GitHub Actions verifies `uv sync --group dev`, `py_compile`, runtime import smoke checks, `pytest -m "not smoke"` regression tests, test workspace indexing, and `pytest -m smoke` MCP JSON-RPC smoke tests on Windows and Ubuntu. Long-running daemon behavior, real GPU/CUDA memory behavior, and local model cache state remain local validation targets. Use the [OS Validation Runbook](./docs/runbook-os-validation.md) for local process and VRAM checks.

---

## License

- **Code**: [MIT License](LICENSE)
- **Knowledge**: The external knowledge seed originates from [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills) and follows the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license.
