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

## 🚀 설치 가이드 (Manual Installation)

기존의 자동화 스크립트(`setup.sh`)는 폐지되었습니다. 아래 상세 수동 설치 가이드를 따라 설치를 진행해 주시기 바랍니다.

- **상세 설치 매뉴얼**: [INSTALL.md](./INSTALL.md) (현재 디렉토리 위치)

> [!IMPORTANT]
> **준비 사항**: Ubuntu 환경에서 `python3-venv` 패키지가 설치되어 있어야 하며, 모든 명령어는 가상환경(`venv`)이 활성화된 상태에서 프로젝트 루트를 기준으로 실행해야 합니다.


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
