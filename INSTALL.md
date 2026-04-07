자동화 스크립트(`setup.sh`)를 사용하여 복잡한 환경 구축 및 폴더명 설정을 한 번에 완료할 수 있습니다.

---

## 0. 필수 시스템 패키지 설치 (Ubuntu)

Python 가상환경 및 수치 계산(FAISS/Torch) 라이브러리 빌드를 위해 아래 패키지가 반드시 필요합니다.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential \
                       libopenblas-dev liblapack-dev wget curl git
```

> [!IMPORTANT]
> **성능 및 스케일링 대응 (.agents 설정)**: 
> `skills/` 디렉토리에 수만 개의 참조 파일이 포함될 경우 인덱싱 속도가 저하될 수 있습니다. 본 패키지는 `.agents/settings.yaml`의 `exclude_paths`를 통해 이를 자동으로 차단하도록 설정되어 있어, 루트 `.gitignore` 없이도 쾌적한 성능을 유지합니다.

---

## 1. 초기 환경 구축 (선택)

### [방법 A] 자동화 스크립트 사용 (권장)
내려받은 에이전트 폴더를 프로젝트 루트에 위치시킨 후, 해당 폴더 안의 `setup.sh`를 실행하십시오. 폴더명을 자동으로 `.agents/`로 맞추고 가상환경을 구축합니다.

```bash
# 예: 폴더명이 agents-main 인 경우 (루트에서 실행)
bash agents-main/setup.sh
```

---

### [방법 B] 수동 설치 (Manual Step-by-Step)
자동화 스크립트 대신 직접 환경을 구축하려면 다음 순서를 따르십시오.

#### 1) 폴더명 정규화
내려받은 폴더의 이름을 `.agents`로 변경하여 프로젝트 루트에 배치합니다.

#### 2) 가상환경(venv) 구축
```bash
# .agents 디렉토리 내부에 가상환경 생성
python3 -m venv .agents/venv
```

#### 3) 의존성 패키지 설치
```bash
# pip 최신화 및 의존성 설치
.agents/venv/bin/pip install --upgrade pip
.agents/venv/bin/pip install -r .agents/requirements.txt
```

---

## 3. 스킬(Skills) 세팅 (지식 확보)

Cortex 엔진은 프로젝트 루트의 `skills/` 디렉토리를 탐색하여 인덱싱합니다. 인덱싱 전, 아래 명령어로 기본 스킬 셋을 확보하십시오.

```bash
# [방법 A] wget 사용 (권장)
mkdir -p skills && cd skills
wget -qO- https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
cd ..

# [방법 B] curl 사용 (wget이 없는 경우)
# mkdir -p skills && cd skills
# curl -L https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
# cd ..
```

---

## 4. 초기 지식 인덱싱

Cortex 엔진이 현재 프로젝트의 모든 코드와 문서를 읽어 분석을 시작합니다.

### [A] 처음 인덱싱 (신규 설치)
```bash
# 프로젝트 루트에서 실행 (PYTHONPATH 명시 필수)
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
```

### [B] 증분 업데이트 (일상 사용)
코드 변경이 있을 때 자동으로 변경된 파일만 갱신합니다. (`--force` 없이 실행)
```bash
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py .
```

### [C] 인덱스 초기화 (모델 교체, 인덱스 꼬임 등)
임베딩 모델이 바뀌거나 데이터가 꼬인 경우, 아래 명령어로 벡터 데이터만 삭제한 뒤 다시 실행합니다. **(소스 코드는 삭제되지 않으므로 안심하세요)**

```bash
# 1. 벡터 데이터 전체 삭제 (프로젝트별 인덱스 포함)
rm -rf .agents/cortex_data/*

# 2. 전체 다시 인덱싱
PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
```

> [!TIP]
> 인덱스는 프로젝트 폴더별로 독립 생성됩니다 (예: `skills.index`, `S14P31B107.index`).
> 특정 프로젝트만 다시 인덱싱하려면 해당 `.index` 파일만 삭제 후 재실행하면 됩니다.
> ```bash
> rm -f .agents/cortex_data/S14P31B107.index .agents/cortex_data/S14P31B107_meta.json
> PYTHONPATH=.agents/scripts .agents/venv/bin/python3 .agents/scripts/cortex/indexer.py . --force
> ```

---

## 5. IDE (MCP 서버) 등록 및 연동

Cortex 엔진을 활용하려면 사용 중인 에디터나 에이전트 클라이언트의 MCP 설정에 서버를 등록해야 합니다. **반드시 절대 경로를 사용해야 함에 유의하십시오.**

### 🍎 클라이언트별 설정 위치 (Config Path Finder)
자신의 환경에서 설정 파일을 찾으려면 터미널에서 아래 명령어를 실행해 보세요.

*   **Gemini CLI**: `find ~ -name "settings.json" | grep .gemini`
*   **Claude Desktop**: (macOS) `ls ~/Library/Application\ Support/Claude/claude_desktop_config.json`
*   **Windsurf / Cursor**: 각 도구의 설정(Settings) 메뉴에서 'MCP'를 검색하는 것이 가장 빠릅니다.

> [!TIP]
> **전체 검색 (Linux/macOS):** 모든 MCP 관련 설정 파일을 한 번에 찾으려면 다음 명령을 실행하십시오.
> ```bash
> find ~ -name "*config.json" -o -name "settings.json" 2>/dev/null | grep -E "mcp|claude|cursor|windsurf|.gemini"
> ```

### 📝 설정값 (JSON 형식 예시)
아래 내용을 복사하여 각 도구의 설정 파일 내 `mcpServers` 항목에 붙여넣으십시오.

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
> **PYTHONPATH 설정**: 스크립트 내부에서 `cortex` 모듈을 정상적으로 참조하기 위해 `env` 항목에 위 예시와 같이 `scripts` 폴더의 절대 경로를 반드시 추가해야 합니다.

> [!TIP]
> **현재 내 경로 확인용 한 줄 명령어:**
> 터미널에서 프로젝트 루트에서 아래 명령을 실행하면 현재 환경에 맞는 전체 JSON 블록을 출력해 줍니다.
> ```bash
> echo "{\"cortex-mcp\": {\"command\": \"$(pwd)/.agents/venv/bin/python3\", \"args\": [\"$(pwd)/.agents/scripts/cortex_mcp.py\"], \"env\": {\"PYTHONPATH\": \"$(pwd)/.agents/scripts\"}}}"
> ```

---

## 5. 트러블슈팅 (FAQ)

*   **Q: `sqlite3.OperationalError: database is locked`**
    - A: 여러 프로세스가 동시에 인덱싱을 시도할 때 발생합니다. 실행 중인 다른 에이전트 명령어나 백그라운드 작업을 종료하고 다시 시도하십시오.
*   **Q: 임베딩 모델 다운로드 중 HTTP 타임아웃 발생**
    - A: 네트워크가 불안정한 경우입니다. `.agents/.env` 파일에서 `CORTEX_EMBEDDING_MODE=api`로 변경하여 외부 API를 활용하는 방안을 검토하십시오. (Hugging Face 토큰 필요)
