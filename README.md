# 🌌 Cortex Agent Infrastructure (`.agents`)

**본 프로젝트는 아직 미완성입니다.**

> **"The Bridge between Human Intent and Agent Intelligence."**
> 
> Cortex는 파편화된 에이전트의 기억을 영속화하고, 어떤 프로젝트에서든 즉시 작업 맥락을 형성할 수 있도록 설계된 **범용 에이전트 엔지니어링 인프라**입니다.

---

## 📂 디렉토리 구조 (Directory Structure)

```text
.agents/
├── rules/          # 에이전트 행동 규칙 (rule.md, governance.md 등)
├── workflows/      # 슬래시 커맨드 워크플로 (/진행, /지식화, /작성 등)
├── protocols/      # 보고 프로토콜
├── scripts/        # MCP 서버 및 유틸리티 엔진
│   ├── cortex/             # Cortex 하이브리드 코어 모듈
│   ├── cortex_mcp.py       # 메인 MCP 서버 (Cortex)
│   └── jules_mcp.py        # 코드 리뷰용 MCP (Jules)
├── cortex_data/    # 인덱싱된 지식 데이터 (Git 제외)
├── history/        # 세션별 작업 이력 메타데이터
├── setup.sh        # [통합] 인젝션 및 환경 구축 일원화 스크립트
├── settings.yaml   # 에이전트 및 인프라 설정 파일
└── README.md       # 본 프로젝트 인프라 가이드
```

---

## 🚀 설치 및 빠른 시작 (Installation & Quick Start)

### 🤖 [FOR AGENTS] 자동 설치 프롬프트 (Auto-Install Prompt)
새로운 환경(다른 PC)에서 에이전트에게 인프라 구성을 지시할 때 **아래 프롬프트를 그대로 복사하여 전달**하세요. 에이전트가 헤매지 않고 스스로 환경을 구축합니다.

> ```markdown
> [CORTEX INFRA INSTALLATION]
> 이 프로젝트에 Cortex 인프라를 설치하고 MCP 서버를 구성해줘. 다음 단계를 순서대로 실행할 것:
> 1. 프로젝트 루트 경로에서 `.agents/setup.sh` 스크립트를 실행하여 가상환경(venv)과 의존성을 설치해. (예: `cd .agents && chmod +x setup.sh && ./setup.sh`)
> 2. 설치가 완료되면, IDE의 MCP 클라이언트 설정(예: Cursor, Windsurf, Gemini CLI)에 다음 서버를 추가해:
>    - Command: `<프로젝트루트>/.agents/venv/bin/python3`
>    - Args: `["<프로젝트루트>/.agents/scripts/cortex_mcp.py"]`
> 3. 설정 후 `.agents/rules/diagnostics.md`를 읽어 가상환경 충돌 방지 원칙(Phase 0)을 숙지해.
> ```

### 🧑‍💻 수동 셋업 (Manual Setup)
자동 스크립트(`setup.sh`)가 실패하거나 권한 문제가 발생할 경우, 사람이 직접 터미널에 아래 명령어를 순서대로 입력하여 확실하게 설치를 진행하세요.

**1. 폴더 이동 및 가상환경 생성**
```bash
# .agents 디렉토리로 이동
cd .agents

# 격리된 Python 가상환경 생성 (python3-venv 패키지 필요)
python3 -m venv venv
```

**2. 가상환경 활성화 및 패키지 설치**
```bash
# 가상환경 활성화 (Linux/Mac 기준)
source venv/bin/activate

# 의존성 설치 전 pip 최신화 및 필수 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt
```

**3. 초기 지식 인덱싱 실행**
```bash
# Cortex 엔진의 DB를 생성하고 워크스페이스를 초기 스캔합니다.
python3 scripts/cortex/indexer.py --force
```

**4. IDE (MCP 클라이언트) 등록**
모든 설치가 끝났습니다. 이제 사용 중인 에디터(Cursor, Windsurf, Gemini CLI 등)의 MCP 설정에 **Cortex MCP**를 아래와 같이 등록하세요.
*(주의: 상대 경로 `./` 나 `~` 대신 반드시 전체 절대 경로를 기입해야 합니다.)*

* **이름 (Name)**: `cortex-mcp`
* **유형 (Type)**: `stdio`
* **명령어 (Command)**: `<프로젝트 절대 경로>/.agents/venv/bin/python3`
* **인자 (Arguments)**: `<프로젝트 절대 경로>/.agents/scripts/cortex_mcp.py`

**💡 [참고] 설정 파일(`mcp.json` 등)을 직접 수정하는 경우의 예시:**
```json
{
  "mcpServers": {
    "cortex-mcp": {
      "command": "/Users/myname/workspace/project/.agents/venv/bin/python3",
      "args": [
        "/Users/myname/workspace/project/.agents/scripts/cortex_mcp.py"
      ]
    }
  }
}
```

---

## 🛠 상세 환경 설정 (Detailed Configuration)

### 1. 환경 변수 및 임베딩 모드
`.env` 설정을 통해 Cortex 엔진의 핵심 동작을 제어할 수 있습니다.

| 모드 | 환경 변수 | 조건 |
|------|----------|------|
| **로컬 (기본값)** | `CORTEX_EMBEDDING_MODE=local` | GPU 또는 CPU로 BGE-M3 실행 (RAM 4GB+) |
| **API (폴백)** | `CORTEX_EMBEDDING_MODE=api` | GPU/RAM 부족 환경, 외부 API 사용 |

### 2. rclone (백업/로드)
Google Drive 등 원격 저장소를 통한 지식 동기화를 위해 설정이 필요합니다.
```bash
rclone config
# 이름: gdrive, 유형: Google Drive 로 설정 후 인증 진행 (https://rclone.org/drive/)
```

### 3. Skills 세팅 (Essential)
Cortex는 `skills/` 폴더 내의 마크다운 파일들을 자동 인덱싱하여 에이전트의 능력치를 확장합니다. `skills/`는 프로젝트 최상단(Root)에 위치해야 합니다.

**올바른 디렉토리 구조 예시:**
```
user/
├── .agents/       # (현재 저장소) Cortex 인프라
├── skills/        # AI 스킬 보관함
└── src/           # 프로젝트 소스 코드
```

**추천 스킬 킷 다운로드 (One-liner):**
```bash
# 최상위 디렉터리에서 실행
mkdir -p skills && cd skills
wget -qO- https://api.github.com/repos/sickn33/antigravity-awesome-skills/tarball/main | tar xz --strip-components=2 "*/skills"
cd ..
```

---

## 🤖 [FOR AGENTS] 부트스트랩 프롬프트 (Bootstrap Prompt)

**설치 및 MCP 연동이 모두 끝난 후**, 새로운 채팅 세션을 시작할 때 에이전트에게 **아래 블록을 통째로 복사하여 전달**하세요. 

> [!TIP]
> **"Agent's First Day Secret Command"**
> ```markdown
> [CONTEXT SYNC] 
> 안녕, 파트너. 너는 지금 이 프로젝트의 Cortex 인프라와 함께 작업하게 될 거야.
> 지금 즉시 다음 단계를 수행해서 프로젝트 맥락을 확보해:
> 
> 1. `cortex-mcp`의 `pc_index_status` 도구를 호출해서 현재 인덱싱된 지식의 양을 확인해.
> 2. `pc_memory_search_knowledge`로 카테고리를 `rule`로 설정해 핵심 규칙(rule.md 등)을 검색하고 숙지해.
> 3. `pc_memory_search_knowledge`로 프로젝트의 '기존 지식'이나 '아키텍처'를 검색해.
> 4. 마지막으로 `/진행` 워크플로우를 호출해서 이전 세션의 작업 지점을 동기화해.
> ```

---

## 🌊 주요 워크플로 (Workflows)

| 커맨드 | 설명 |
|--------|------|
| `/진행` | 세션 재개 시 컨텍스트 10초 내 복원 |
| `/지식화` | 성공 패턴 및 아키텍처 결정을 DB에 영속화 |
| `/작성` | 커밋 메시지 / MR 요약 / Jira 문서 자동 생성 |
| `/검토` | 코드 품질 및 컨벤션 준수 여부 자가 점검 |
| `/백업` | 전체 지식 DB 및 설정을 Google Drive에 동기화 |

---

## 📜 프로젝트 역사 및 라이선스 (History & Attribution)

### 1. 탄생 배경 (History & Attribution)
본 프로젝트는 무겁고 파편화된 기존 에이전트 보조 도구들의 한계를 극복하기 위해, 핵심 기능들을 파이썬(Python)으로 가볍게 재작성(Porting) 및 독자 구현하여 하나로 통합한 결과물입니다.

- **Agent Memory MCP ([webzler/agentMemory](https://github.com/webzler/agentMemory))**: 
  - 기존 Node.js 기반 환경을 탈피하여, 에이전트의 영구 지식 저장 메커니즘을 **Python 기반 경량화 구조로 완전히 새로 작성**하였습니다.
- **Vexp (Legacy Structure)**: 
  - 비공개 상태였던 유용한 워크플로 프레임워크의 기능 명세와 DB 스키마 형식을 참고하여, 동일 기능을 수행하는 로직을 **Python으로 독자 구현(Reverse Engineering)** 하였습니다.
- **Cortex Engine**: 
  - 위 개념들을 병합하여 가장 빠르고 가벼운 SQLite 기반 하이브리드 검색 엔진으로 고도화했습니다.

### 2. 라이선스 고지 (License Details)
- **Cortex Agent 인프라 (Code)**: 본 프로젝트의 모든 코드는 [MIT License](LICENSE)에 따라 자유롭게 배포/수정 가능합니다.
- **Skills (`skills/` 폴더 콘텐츠)**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)입니다.
  - 문서 및 비코드 콘텐츠: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따르며, 원본 저작권자에 대한 출처 표기가 필요합니다.
