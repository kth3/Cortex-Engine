# 멀티 에이전트 릴레이 및 메모리 기록 규정 (Relay & Memory Protocol)

> [!IMPORTANT]
> 본 규정은 멀티 에이전트 환경에서 시스템 상태(Lock)의 오염을 방지하고 연속적인 Handoff를 보장하기 위해 모든 에이전트가 최우선으로 준수해야 하는 물리적 아키텍처 규칙입니다.

## 1. board.json 내 임시 생각 기록 금지 (Zero Scratchpad Policy)
- 에이전트는 멀티 에이전트 협업 시 자신의 작업 진척도나 의식의 흐름(예: "7번 문서를 읽는 중", "이 버그는 A 때문인 것 같음")을 **절대 `.agents/state/board.json` 파일에 직접 기록해서는 안 됩니다.**
- `board.json`은 락(Lock) 상태 관리(`BUSY` / `IDLE` / `HANDOFF`)와 시스템의 다음 주자에게 넘기는 최종 신호등(Semaphore) 역할만을 수행해야 하는 초경량 파일입니다. 

## 2. 중간 상태 및 관찰 기록 (Cortex DB Observation)
- 작업 중 발생한 일시적인 생각, 주요 통찰, 중간 성과 등은 반드시 **Cortex DB 메모리 도구(`pc_save_observation`)**를 호출하여 분리된 저장소에 안전하게 기록하십시오.
- 이를 통해 시스템 파일의 오염 없이, 다른 에이전트가 `pc_search_memory`를 통해 해당 사고의 맥락을 완벽하게 추론할 수 있게 됩니다.

## 3. 최종 인계 및 오토 릴리즈 (Handoff & Auto-Release)
- 에이전트는 자신이 부여받은 작업을 완전히 종료하고 다른 에이전트나 사용자에게 제어권을 넘기는 마지막 시점에만 `board.json`의 `handoff_message` 필드를 사용해야 합니다.
- **권장 사항**: 작업 종료 시 `pc_session_sync` 도구에 `auto_release_agent` 파라미터를 제공하여 변경된 파일 목록 추출부터 요약 및 락 해제를 단 한 번의 호출로 무인 자동화(Autonomous Handoff)하십시오.
