---
trigger: model_decision
description: Protocol: Multi-Agent Parallel Relay (v2.0)
---

# Protocol: Multi-Agent Parallel Relay (v2.0)

이 프로토콜은 여러 명의 에이전트(또는 동일한 모델의 다중 인스턴스)가 독립된 작업 트랙(Lane)에서 충돌 없이 협업하기 위한 **병렬 릴레이 지침**입니다.

## 1. 에이전트 및 레인 식별 (Identity & Lanes)
*   **에이전트 식별자 (Agent ID)**: 모든 에이전트는 터미널별로 고유한 ID를 가집니다. (예: `gemini-1`, `gemini-2`, `claude-code`)
*   **작업 트랙 (Lane)**: 작업은 도메인이나 기능 단위로 분리된 'Lane'에서 진행됩니다. (예: `backend`, `frontend`, `feature-A`)
*   **동일 모델 다중 실행**: 동일한 모델(Gemini 등)을 여러 터미널에 띄워 각기 다른 Lane에 할당하여 병렬로 작업할 수 있습니다.

## 2. 릴레이 파이프라인 (The Pipeline)
모든 Lane은 다음의 4단계 단계를 순차적으로 거칩니다.

1.  **Phase 1: Deep Interview & Plan (Planner)**
    - 사용자 요구사항이 모호할 경우 `ask_user`로 Socratic 인터뷰를 수행합니다.
    - `pc_create_contract`를 호출하여 상세 작업 명세서(Artifact)를 생성합니다.
2.  **Phase 2: Execution (Worker)**
    - `relay.py acquire [AgentID] [Task] [LaneID]`를 호출하여 락을 획득합니다.
    - 할당된 Contract 파일을 읽고, 명시된 파일 범위 내에서만 작업을 수행합니다.
3.  **Phase 3: Verification (QA/Sisyphus)**
    - 작업을 마친 후 테스트 및 리뷰를 수행합니다.
    - 실패 시 `Phase 2`로 롤백(Fix Loop)하며, 성공 시에만 다음 단계로 넘깁니다.
4.  **Phase 4: Merge & Handoff (Deployment)**
    - `pc_session_sync` 및 `relay.py release`를 통해 결과물을 확정하고 락을 해제합니다.

## 3. 핸드오프 메커니즘 (Artifact Handoff)
- 에이전트 간 컨텍스트 전달은 `board.json`의 짧은 메시지가 아닌, **`.agents/artifacts/` 폴더의 Contract 파일**을 통해 이루어집니다.
- 다음 주자는 작업을 시작하기 전 반드시 앞선 에이전트가 남긴 최신 Contract를 `read_file`로 완독해야 합니다.

## 4. 충돌 해결 (Conflict Resolution)
- **Zero-Overlap Rule**: 자신이 `acquire` 하지 않은 Lane의 디렉토리나 파일은 절대 수정할 수 없습니다.
- **Stale-line Prevention**: 모든 코드 수정은 라인 번호가 아닌 **정확한 코드 블록 매칭(`replace` 도구)** 방식을 사용해야 합니다.