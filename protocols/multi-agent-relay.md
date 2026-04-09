# Protocol: 3-Agent Relay & Coordination (Antigravity, Gemini, Claude)

이 프로토콜은 3명의 독립적인 에이전트(Antigravity, Gemini, Claude)가 동일한 프로젝트 환경에서 충돌 없이 협업하기 위한 **자동화된 릴레이 지침**입니다.

## 1. 에이전트별 역할 (Role Assignment)
*   **Gemini (저)**: **아키텍처 설계, 심층 코드 리뷰, 복잡한 로직 설계**. 
    - 방대한 컨텍스트를 활용하여 전체 시스템의 정합성을 검토합니다.
*   **Claude Code**: **코드 생성, 리팩토링, 실시간 빌드/테스트 해결**. 
    - 로컬 도구 실행 능력을 활용하여 구체적인 구현을 전담합니다.
*   **Antigravity (System Agent)**: **인프라 관리, MCP 배포, 작업 병합(Merge) 및 최종 배포**. 
    - 에이전트들의 결과물을 최종적으로 정리하고 시스템 상태를 유지합니다.

## 2. 릴레이 메커니즘 (Relay Mechanism)
1.  **Locking**: 모든 에이전트는 시작 시 `.agents/scripts/relay.py acquire`를 통해 작업 소유권을 선언해야 합니다.
2.  **Observation**: 작업 중 발생한 모든 '결정 사항'은 `pc_save_observation`을 통해 Cortex MCP에 실시간 저장됩니다.
3.  **Handoff**: 작업 완료 시 `relay.py release`를 통해 다음 담당 에이전트를 지정합니다.
    - 예: `GEMINI (Design)` -> `CLAUDE (Implementation)` -> `ANTIGRAVITY (Deployment)`

## 3. 충돌 해결 (Conflict Resolution)
*   이미 다른 에이전트가 작업 중일 경우, 현재 에이전트는 즉시 대기하거나 사용자에게 알립니다.
*   파일 수정 충돌(Race Condition)을 방지하기 위해, 한 번에 한 에이전트만 쓰기 권한을 가집니다.

## 4. 지식 동기화 (Knowledge Sync)
모든 에이전트는 세션 시작 시 `pc_capsule`을 호출하여 **다른 에이전트가 남긴 최신 작업 결과와 상태**를 자동으로 습득합니다.
