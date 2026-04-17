# 🌌 Cortex Agent Infrastructure (`.agents`)

**"The Bridge between Human Intent and Agent Intelligence."**

Cortex는 파편화된 에이전트의 기억을 영속화하고, 어떤 프로젝트에서든 즉시 작업 맥락을 형성할 수 있도록 설계된 **범용 에이전트 엔지니어링 인프라**입니다. 본 프로젝트는 최신 멀티 에이전트 오케스트레이션 패턴과 하이브리드 데이터베이스 기술을 결합하여 강력한 컨텍스트 엔진을 제공합니다.

---

## 📂 디렉토리 구조 (Directory Structure)

```
.agents/
├── rules/          # 에이전트 행동 규칙 및 정밀 편집 지침
├── workflows/      # 슬래시 커맨드 워크플로 (/진행, /지식화, /작성 등)
├── protocols/      # 멀티 에이전트 릴레이 및 보고 프로토콜
├── scripts/        # MCP 서버 및 릴레이 관리 스크립트
│   ├── cortex/             # Cortex 하이브리드 코어 모듈 (SQLite + Kuzu)
│   ├── relay.py            # Multi-Lane 락 관리자
│   ├── cortex_mcp.py       # 메인 MCP 서버 (Artifact 생성 지원)
│   └── jules_mcp.py        # 코드 리뷰용 MCP (Jules)
├── artifacts/      # 에이전트 간 인계용 Task Contract (.md)
├── history/        # 세션별 작업 이력 및 관찰 기록
├── memories.db     # 하이브리드 핵심 DB (Vector + FTS5)
├── graph.kuzu/     # Kuzu DB 기반 코드 의존성 그래프
└── settings.yaml   # 인프라 전역 설정
```

## 🚀 주요 특징 (Key Features)

### 1. Hybrid Context Engine (Vector + Graph + RDB)
Cortex는 세 가지 데이터 모델을 결합하여 최적의 검색 성능을 냅니다.
*   **Vector Search (`sqlite-vec`)**: 시맨틱 검색을 통해 관련 지식과 코드를 10초 내에 복원합니다.
*   **Graph Analysis (`Kuzu DB`)**: Cypher 쿼리를 사용해 코드 간의 복잡한 호출 관계 및 의존성을 정밀 분석합니다.
*   **FTS5 Text Search**: 키워드 기반의 고속 텍스트 검색을 지원합니다.

### 2. Multi-Lane Parallel Execution
단일 전역 Lock의 한계를 넘어, **도메인(Lane) 기반의 병렬 락 시스템**을 지원합니다. 여러 터미널에서 동일한 모델(Gemini 등)을 다수 실행하더라도 각자 할당된 레인(예: `frontend`, `backend`)에서 충돌 없이 동시에 작업할 수 있습니다.

### 3. Artifact-Based Handoff
에이전트 간의 컨텍스트 전달 시 휘발성 메시지가 아닌, **물리적인 마크다운 계약서(Contract)**를 생성하여 인계합니다. 이를 통해 복잡한 지시사항도 유실 없이 100% 전달되며, 다른 모델 간의 협업 시에도 완벽한 컨텍스트 동기화가 가능합니다.

### 4. Precision Editing (Hashline Style)
줄 번호(Line Number)의 어긋남으로 인한 코드 훼손을 방지하기 위해, **내용 기반 치환(Content-based Replacement)** 방식을 강제합니다. 내가 본 바로 그 코드 블록이 일치할 때만 수정을 허용하여 멀티 에이전트 환경의 안전성을 극대화했습니다.

---

## 📜 프로젝트 출처 및 영감 (Attribution & Inspiration)

Cortex는 다음의 훌륭한 프로젝트들의 개념을 파이썬 기반으로 경량화하고 통합하여 탄생했습니다.

- **Vexp ([https://vexp.dev/](https://vexp.dev/))**: 
  - 범용 워크플로 프레임워크의 구조와 DB 스키마 형식을 참고하여 로컬 컨텍스트 엔진으로 재구현하였습니다.
- **oh-my-agent ([first-fluke/oh-my-agent](https://github.com/first-fluke/oh-my-agent))**: 
  - 역할 기반의 에이전트 전문화 및 포터블한 에이전트 정의 개념을 도입했습니다.
- **oh-my-claudecode ([Yeachan-Heo/oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode))**: 
  - 소크라테스식 심층 인터뷰(Deep Interview)와 아티팩트 기반 핸드오프 패턴을 흡수했습니다.
- **oh-my-openagent ([code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent))**: 
  - 해시 기반 정밀 편집(Hashline)과 무한 검증 루프(Sisyphus-style)를 통한 작업 완료 보장 메커니즘을 이식했습니다.

---

## 🛠 설치 및 사용 (Installation)

- **상세 가이드**: [INSTALL.md](./INSTALL.md)
- **핵심 커맨드**:
  - `/진행`: 세션 복원 및 작업 시작
  - `/지식화`: 주요 결정 사항 영구 저장
  - `python3 .agents/scripts/relay.py status`: 현재 릴레이 상태 확인

---

## ⚖️ 라이선스 (License)
- **Code**: [MIT License](LICENSE)
- **Skills**: 스킬 가이드의 원본은 [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills)이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 라이선스를 따릅니다.
