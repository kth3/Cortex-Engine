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

## 0-1. GPU 풀 가속 설정 (선택, NVIDIA 전용)

기본 설치만으로도 정상 동작하지만, **NVIDIA Ampere 이상(RTX 3000/4000번대)** GPU를 사용하는 경우 아래 단계를 추가로 수행하면 `bfloat16 + FlashAttention2` 모드가 활성화되어 인덱싱 속도가 획기적으로 향상됩니다.

> [!NOTE]
> 이 단계를 건너뛰어도 `flash-attn` 미설치를 감지하여 `float16`으로 자동 전환되므로 **인덱싱은 정상 동작**합니다.  
> 로그에 `Precision mode: fp16`이 출력되면 현재 이 모드로 동작 중인 것입니다.

### Step 1. 사전 빌드(Wheel) 기반 3초 컷 설치 (적극 권장)

소스 빌드는 메모리 요구량이 극심하여 쉽게 실패합니다. GitHub Releases에 미리 준비된 완성 파일(`.whl`)을 직접 다운받아 설치하는 것이 압도적으로 빠르고 안전합니다. (에러 방지를 위해 호환성 검증이 끝난 파이토치 `2.5.1` 버전을 고정하여 설치합니다.)

```bash
# 1. 휠(Wheel) 파일과 호환되도록 가장 안정적인 파이토치 버전(2.5.1) 강제 설치
.agents/venv/bin/pip uninstall -y torch torchvision torchaudio
.agents/venv/bin/pip install torch==2.5.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 2. Flash-Attention 3초 설치 (직접 링크)
.agents/venv/bin/pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3%2Bcu12torch2.5cxx11abiFALSE-cp312-cp312-linux_x86_64.whl
```

### Step 2. 소스 빌드 설치 (Wheel이 없거나 실패할 경우)

만약 앞선 단계가 실패한다면, 시스템의 `nvcc` 버전과 Python 패키지 내 `torch`의 CUDA 버전 간 불일치 때문입니다. 우분투 기본 패키지(`nvidia-cuda-toolkit`)를 설치하지 말고, **가상환경에 설치된 PyTorch 버전에 맞는 공식 NVIDIA CUDA Toolkit을 직접 설치**해야 합니다. (현재 기본 권장: CUDA 13.0)

```bash
# 1. NVIDIA 공식 레포 추가 및 CUDA 13.0 설치 (Ubuntu 24.04 예시)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y cuda-toolkit-13-0

# 2. 컴파일용 임시 16GB 가상 메모리(Swap) 생성 ★ (RAM 부족 방지용)
sudo fallocate -l 16G /swapfile_build
sudo chmod 600 /swapfile_build
sudo mkswap /swapfile_build
sudo swapon /swapfile_build

# 3. 강제로 새 CUDA 버전을 바라보도록 환경변수 세팅 후 pip 빌드
# (MAX_JOBS=1 은 필수입니다. 다중 코어 빌드 시 RAM 한계를 초과하여 서버가 멈출 수 있습니다.)
CUDA_HOME=/usr/local/cuda-13.0 PATH=/usr/local/cuda-13.0/bin:$PATH \
MAX_JOBS=1 .agents/venv/bin/pip install flash-attn --no-build-isolation

# 4. 완료 후 임시 가상 메모리 삭제
sudo swapoff /swapfile_build
sudo rm /swapfile_build
```

> [!CAUTION]
> `--no-build-isolation` 플래그와 `MAX_JOBS=1` 설정이 필수입니다. 
> `flash-attn`의 C++ 컴파일은 극도의 메모리를 요구하므로, **램 16GB 이하 환경에서는 MAX_JOBS=1과 더불어 16GB 가상 메모리(Swap) 설정을 하지 않으면 운영체제(OS) 전체가 멈추거나 재부팅되는 치명적인 문제**가 발생합니다. 위의 스크립트를 그대로 복사하여 진행하시고, 소요 시간은 10~30분입니다.

### Step 3. 활성화 확인

인덱싱 실행 후 터미널 출력에서 아래 줄을 확인하십시오.

```
[cortex-vector] Precision mode: bf16+FlashAttn2
```

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
