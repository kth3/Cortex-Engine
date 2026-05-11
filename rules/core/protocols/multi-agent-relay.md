---
trigger: model_decision
description: Protocol: Multi-Agent Parallel Relay (v2.0)
---

# Protocol: Multi-Agent Parallel Relay (v2.0)

> [!IMPORTANT]
> 본 규정은 멀티 에이전트 병렬 환경에서 시스템 상태의 오염을 방지하고 연속적인 Handoff를 보장하기 위해 모든 에이전트(동일 모델 다중 인스턴스 포함)가 최우선으로 준수해야 하는 물리적 아키텍처 규칙입니다.

## 1. 에이전트 및 레인 식별 (Identity & Lanes)
*   **에이전트 식별자 (Agent ID)**: 모든 에이전트는 터미널별로 고유한 ID를 가집니다. (예: `g1`, `g2`, `claude`)
*   **작업 트랙 (Lane)**: 작업은 도메인이나 기능 단위로 분리된 'Lane'에서 진행됩니다.
*   **동일 모델 다중 실행**: 동일한 모델을 여러 터미널에 띄워 각기 다른 Lane에 할당하여 병렬로 작업할 수 있습니다.

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
    - `pc_session_sync` 도구에 `auto_release_agent` 및 `auto_release_lane` 파라미터를 제공하여 변경 사항 추출부터 락 해제까지 단 한 번의 호출로 자동화하십시오. (또는 수동으로 `relay.py release` 수행)

## 3. 정밀 편집 및 충돌 방지 (Stale-line Prevention)
- **Content-Based Editing 강제**: 모든 코드 수정 시 라인 번호(Line Number)에 의존하지 마십시오. 반드시 수정 대상 코드 블록의 **정확한 원본 텍스트 매칭(`replace` 도구의 `old_string`)** 방식을 사용하여 치환하십시오.
- **Matched Rejection**: 만약 `replace` 도구가 대상 코드를 찾지 못해 실패한다면, 다른 에이전트가 이미 해당 부분을 수정했음을 의미합니다. 즉시 작업을 중단하고 최신 파일을 다시 읽어(`pc_read_with_hash` 또는 `read_file`) 수정 계획을 갱신하십시오.

## 4. 작업 영역 격리 (Zero-Overlap / Lane Isolation)
- 에이전트는 할당받은 Lane의 범위 밖의 파일이나 디렉토리를 절대 수정해서는 안 됩니다.
- 공유 파일(예: `settings.yaml`, `README.md`)을 동시에 수정해야 할 경우, 한 에이전트가 작업을 마치고 락을 `release` 한 뒤에만 다른 에이전트가 `acquire` 할 수 있습니다.

## 5. Artifact 기반 Handoff (Contract-First)
- Agent A는 작업을 마치고 `relay.py release` (또는 `pc_session_sync` 자동화) 호출 시 `contract_id`를 전달합니다.
- Agent B가 `acquire` 도구를 사용하거나 세션 시작 시 `pc_auto_context`를 호출하면, 자동으로 인계된 계약서(`.agents/artifacts/{contract_id}`) 위치가 노출됩니다. 
- 다음 주자는 작업 시작 전 해당 파일을 반드시 `read_file` 등으로 완독해야 합니다.