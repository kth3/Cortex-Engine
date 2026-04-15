# Cortex Agent — 설치 가이드 (V2)

> **V2 아키텍처**: FAISS를 제거하고 `sqlite-vec` + `kuzu` 기반의 완전 내장형(Zero Infrastructure) Polystore + Graph-RAG 엔진으로 전환되었습니다.  
> 외부 서버, 인덱스 파일, 별도 데몬이 **일절 필요 없습니다**.

### 데이터 저장 구조

```
your-project/
└── .cortex/              ← 에이전트 툴 + 런타임 데이터 통합 폴더
    ├── venv/             ← Python 가상환경
    ├── scripts/          ← Cortex 엔진 소스
    ├── requirements.txt
    ├── memories.db       ← SQLite-vec 벡터/메모리 DB (단일 파일)
    └── graph.kuzu/       ← Kuzu 그래프 DB (폴더 형태로 저장)
```

> [!NOTE]
> `memories.db`와 `graph.kuzu/`는 첫 인덱싱 시 자동으로 생성됩니다. `.gitignore`에 `.cortex/memories.db`와 `.cortex/graph.kuzu/`를 추가하는 것을 권장합니다.

---

## 0. 필수 시스템 패키지 설치 (Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential git
```

---

## 0-1. GPU 가속 설정 (선택, NVIDIA 전용)

기본 설치만으로도 CPU 모드로 정상 동작합니다. NVIDIA GPU가 있다면 아래를 추가 수행하면 인덱싱 속도가 대폭 향상됩니다.

> [!NOTE]
> 이 단계를 건너뛰면 CPU 모드로 자동 동작합니다.

```bash
# 가상환경 내 torch를 CUDA 빌드로 교체
.cortex/venv/bin/pip uninstall -y torch torchvision torchaudio
.cortex/venv/bin/pip install torch==2.5.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Flash-Attention 설치 (Ampere 이상 GPU에서 bf16 활성화)
.cortex/venv/bin/pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3%2Bcu12torch2.5cxx11abiFALSE-cp312-cp312-linux_x86_64.whl
```

> [!CAUTION]
> Flash-Attention 소스 빌드 시 RAM 16GB 이하 환경에서는 OS가 멈출 수 있습니다. 반드시 위 Wheel 방식을 사용하십시오.

---

## 1. 설치

### 1) 저장소 배치

내려받은 폴더를 프로젝트 루트에 **`.cortex`** 이름으로 배치합니다.

```bash
# 클론 직접 지정
git clone https://github.com/yourname/cortex-temp.git .cortex

# 또는 수동 이동
mv cortex-temp .cortex
```

### 2) 가상환경 생성

```bash
python3 -m venv .cortex/venv
```

### 3) 의존성 설치

```bash
.cortex/venv/bin/pip install --upgrade pip
.cortex/venv/bin/pip install -r .cortex/requirements.txt
```

> [!IMPORTANT]
> `requirements.txt`에 `sqlite-vec>=0.1.2`와 `kuzu>=0.11.3`이 포함되어 있습니다. 이 두 패키지가 V2 핵심 엔진입니다.

---

## 2. 스킬(Skills) 세팅 (선택)

Cortex는 `skills/` 디렉토리를 탐색하여 추가 지식으로 인덱싱합니다.

```bash
mkdir -p skills && cd skills
wget -qO- https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
cd ..
```

---

## 3. 초기 인덱싱

### [A] 처음 인덱싱 (신규 설치 / 전체 재인덱싱)

```bash
PYTHONPATH=.cortex/scripts .cortex/venv/bin/python3 .cortex/scripts/cortex/indexer.py . --force
```

### [B] 증분 업데이트 (코드 변경 후)

```bash
PYTHONPATH=.cortex/scripts .cortex/venv/bin/python3 .cortex/scripts/cortex/indexer.py .
```

### [C] 인덱스 완전 초기화

```bash
# SQLite DB 및 Kuzu 그래프 DB 모두 삭제
rm -f .cortex/memories.db
rm -rf .cortex/graph.kuzu/

# 전체 재인덱싱
PYTHONPATH=.cortex/scripts .cortex/venv/bin/python3 .cortex/scripts/cortex/indexer.py . --force
```

> [!TIP]
> MCP 툴로 인덱싱을 실행하려면 에이전트 클라이언트에서 `pc_reindex` 툴을 호출하면 됩니다.

---

## 4. MCP 서버 등록

### 클라이언트별 설정 파일 위치

| 클라이언트 | 탐색 명령 |
|---|---|
| Gemini CLI | `find ~ -name "settings.json" \| grep .gemini` |
| Claude Desktop | `ls ~/Library/Application\ Support/Claude/claude_desktop_config.json` |
| Windsurf / Cursor | 각 도구 설정(Settings) → MCP 검색 |

### JSON 설정값

```json
"cortex-mcp": {
  "command": "/절대/경로/to/project/.cortex/venv/bin/python3",
  "args": ["/절대/경로/to/project/.cortex/scripts/cortex_mcp.py"],
  "env": {
    "PYTHONPATH": "/절대/경로/to/project/.cortex/scripts"
  }
}
```

현재 경로 기준으로 자동 출력:

```bash
echo "{\"cortex-mcp\": {\"command\": \"$(pwd)/.cortex/venv/bin/python3\", \"args\": [\"$(pwd)/.cortex/scripts/cortex_mcp.py\"], \"env\": {\"PYTHONPATH\": \"$(pwd)/.cortex/scripts\"}}}"
```

---

## 5. 트러블슈팅

**Q: `sqlite3.OperationalError: database is locked`**  
→ WAL 모드가 활성화되어 있어 대부분 자동 해소됩니다. 지속된다면 다른 에이전트 프로세스를 종료 후 재시도하십시오.

**Q: `ModuleNotFoundError: No module named 'kuzu'` 또는 `'sqlite_vec'`**  
→ MCP 설정의 `command` 경로가 `.cortex/venv/bin/python3`인지 확인하십시오. 시스템 Python으로 실행 시 발생합니다.

**Q: 임베딩 모델 다운로드 타임아웃**  
→ 처음 실행 시 `Qwen/Qwen3-Embedding-0.6B` 모델을 Hugging Face에서 자동 다운로드합니다. `.cortex/.env`에 `HF_TOKEN=your_token`을 설정하면 안정적인 다운로드가 가능합니다.
