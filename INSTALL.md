# Cortex Agent — 설치 가이드 (V2)

> **V2 아키텍처**: FAISS를 제거하고 `sqlite-vec` + `kuzu` 기반의 완전 내장형(Zero Infrastructure) Polystore + Graph-RAG 엔진으로 전환되었습니다.  
> 외부 서버, 인덱스 파일, 별도 데몬이 **일절 필요 없습니다**.

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
> 이 단계를 건너뛰면 `float16` CPU 모드로 자동 동작합니다.

### Wheel 기반 빠른 설치 (권장)

```bash
# 가상환경 내 torch를 CUDA 빌드로 교체
.agents/venv/bin/pip uninstall -y torch torchvision torchaudio
.agents/venv/bin/pip install torch==2.5.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Flash-Attention 설치 (Ampere 이상 GPU에서 bf16 활성화)
.agents/venv/bin/pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3%2Bcu12torch2.5cxx11abiFALSE-cp312-cp312-linux_x86_64.whl
```

> [!CAUTION]
> Flash-Attention 소스 빌드 시 RAM 16GB 이하 환경에서는 OS가 멈출 수 있으므로 반드시 위의 Wheel 방식을 사용하십시오.

---

## 1. 설치

### 1) 저장소 배치

내려받은 `cortex-temp` 폴더(또는 클론한 폴더)를 프로젝트 루트에 `.agents`로 이름을 바꿔 배치합니다.

```bash
# 예: 클론 후 이름 변경
git clone https://github.com/yourname/cortex-temp.git .agents
```

또는 수동으로 폴더를 이동한 후:

```bash
mv cortex-temp .agents
```

### 2) 가상환경 생성

```bash
python3 -m venv .agents/venv
```

### 3) 의존성 설치

```bash
.agents/venv/bin/pip install --upgrade pip
.agents/venv/bin/pip install -r .agents/requirements.txt
```

> [!IMPORTANT]
> `requirements.txt`에 `sqlite-vec>=0.1.2`와 `kuzu>=0.11.3`이 포함되어 있습니다. 이 두 패키지가 V2 핵심 엔진입니다.

---

## 2. 스킬(Skills) 세팅 (선택)

Cortex는 `skills/` 디렉토리를 탐색하여 추가 지식으로 인덱싱합니다.

```bash
# wget 사용
mkdir -p skills && cd skills
wget -qO- https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
cd ..

# curl 사용 (wget이 없는 경우)
# mkdir -p skills && cd skills
# curl -L https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
# cd ..
```

---

## 3. 초기 인덱싱

> [!NOTE]
> 인덱스 데이터는 `.cortex/memories.db` 및 `.cortex/graph.kuzu/`에 자동 생성됩니다.  
> 별도로 폴더를 만들 필요가 없으며, `.gitignore`에 `.cortex/`를 추가하는 것을 권장합니다.

### [A] 처음 인덱싱 (신규 설치 / 전체 재인덱싱)

```bash
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
```

### [B] 증분 업데이트 (코드 변경 후)

```bash
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py .
```

### [C] 인덱스 초기화 (데이터 완전 삭제 후 재시작)

```bash
# V2는 단일 DB 파일이므로 삭제가 간단합니다
rm -rf .cortex/

# 전체 재인덱싱
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
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

아래 내용을 설정 파일 내 `mcpServers` 항목에 추가하십시오.

```json
"cortex-mcp": {
  "command": "/절대/경로/to/project/.agents/venv/bin/python3",
  "args": ["/절대/경로/to/project/.agents/scripts/cortex_mcp.py"],
  "env": {
    "PYTHONPATH": "/절대/경로/to/project/.agents/scripts"
  }
}
```

> [!IMPORTANT]
> `PYTHONPATH`에 `scripts` 폴더의 절대 경로를 반드시 지정해야 `cortex` 모듈을 정상적으로 참조합니다.

현재 환경에 맞는 설정값을 자동 출력하는 명령어:

```bash
echo "{\"cortex-mcp\": {\"command\": \"$(pwd)/.agents/venv/bin/python3\", \"args\": [\"$(pwd)/.agents/scripts/cortex_mcp.py\"], \"env\": {\"PYTHONPATH\": \"$(pwd)/.agents/scripts\"}}}"
```

---

## 5. 트러블슈팅

**Q: `sqlite3.OperationalError: database is locked`**  
→ 여러 프로세스가 동시에 DB에 접근할 때 발생합니다. WAL 모드가 활성화되어 있으므로 잠시 후 재시도하거나, 다른 에이전트 프로세스를 종료 후 재실행하십시오.

**Q: `ModuleNotFoundError: No module named 'kuzu'` 또는 `'sqlite_vec'`**  
→ 가상환경이 아닌 시스템 Python으로 실행된 경우입니다. MCP 설정의 `command` 경로가 `.agents/venv/bin/python3`로 되어 있는지 확인하십시오.

**Q: 임베딩 모델 다운로드 타임아웃**  
→ 처음 실행 시 `Qwen/Qwen3-Embedding-0.6B` 모델을 Hugging Face에서 다운로드합니다. `.agents/.env` 파일에 `HF_TOKEN=your_token`을 설정하면 Private 모델 및 안정적인 다운로드가 가능합니다.
