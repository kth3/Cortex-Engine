---
trigger: model_decision
description: 멀티 에이전트 릴레이 및 메모리 기록 규정 (Relay & Memory Protocol)
---

# 멀티 에이전트 릴레이 및 메모리 기록 규정 (Relay & Memory Protocol)

> [!IMPORTANT]
> 본 규정은 멀티 에이전트 병렬 환경에서 시스템 상태의 오염을 방지하고 연속적인 Handoff를 보장하기 위해 모든 에이전트(동일 모델 다중 인스턴스 포함)가 최우선으로 준수해야 하는 물리적 아키텍처 규칙입니다.

## 1. 정밀 편집 및 충돌 방지 (Stale-line Prevention)
- **Content-Based Editing 강제**: 모든 코드 수정 시 라인 번호(Line Number)에 의존하지 마십시오. 반드시 수정 대상 코드 블록의 **정확한 원본 텍스트 매칭(`replace` 도구의 `old_string`)** 방식을 사용하여 치환하십시오.
- **Matched Rejection**: 만약 `replace` 도구가 대상 코드를 찾지 못해 실패한다면, 다른 에이전트가 이미 해당 부분을 수정했음을 의미합니다. 즉시 작업을 중단하고 최신 파일을 다시 읽어(`read_file`) 수정 계획을 갱신하십시오.

## 2. 작업 영역 격리 (Zero-Overlap / Lane Isolation)
- 에이전트는 `relay.py acquire`를 통해 할당받은 **Lane(작업 트랙)**의 범위 밖의 파일이나 디렉토리를 절대 수정해서는 안 됩니다.
- 공유 파일(예: `settings.yaml`, `README.md`)을 동시에 수정해야 할 경우, 한 에이전트가 작업을 마치고 `release` 한 뒤에만 다른 에이전트가 `acquire` 할 수 있습니다.

## 3. Artifact 기반 Handoff (Contract-First)
- 에이전트 간의 상세한 인계 사항은 `board.json`의 `handoff_message` 필드에 직접 적지 않습니다.
- 대신 **Cortex MCP 도구(`pc_create_contract`)**를 호출하여 물리적인 마크다운 계약서 파일(`.agents/artifacts/contract_*.md`)을 생성하고, 다음 주자에게 해당 파일명을 전달하십시오.

## 4. 최종 인계 및 오토 릴리즈 (Handoff & Auto-Release)
- **Autonomous Handoff**: 작업 종료 시 `pc_session_sync` 도구에 `auto_release_agent` 및 `auto_release_lane` 파라미터를 제공하여 변경 사항 추출부터 락 해제까지 단 한 번의 호출로 자동화하십시오.