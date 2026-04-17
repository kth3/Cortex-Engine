---
trigger: model_decision
description: Sisyphus Verification Protocol (무한 검증 규정)
---

# Sisyphus Verification Protocol (무한 검증 규정)

> [!IMPORTANT]
> 본 규정은 "작업의 완료"를 정의하는 최상위 품질 기준입니다. 에이전트는 아래 명시된 **객관적 증거(Evidence)**를 제시하지 못할 경우, 절대로 `relay.py release`를 호출하여 작업을 종료할 수 없습니다.

## 1. 의도 분석 및 구어화 (Intent Verbalization)
모든 메시지의 시작점에서 에이전트는 자신의 추론 과정을 투명하게 공개해야 합니다.
- **형식**: "> I detect [intent] intent. My approach: [plan]."
- **목적**: 사용자가 에이전트의 작업 방향이 잘못되었음을 즉시 인지하고 교정할 수 있게 합니다.

## 2. 단계별 필수 행동 (Phase-Gate)
작업은 반드시 다음 단계를 거쳐야 하며, 각 단계의 게이트를 통과해야 합니다.

### Phase 1: Planning & Contract
- 작업 착수 전 `pc_create_contract`를 통해 목표를 명확히 정의합니다.
- 수정 범위(Targeted Files)를 확정하고 이를 벗어난 수정을 금지합니다.

### Phase 2: Execution with Hashline
- 모든 코드 수정은 반드시 `pc_strict_replace` 도구를 사용합니다.
- 줄 번호가 아닌 **내용 일치 여부**를 통해 편집의 무결성을 보장합니다.
- 만약 `Content mismatch` 에러가 발생하면, 즉시 파일을 다시 읽고(`read_with_hash`) 수정을 재시도합니다.

### Phase 3: Evidence Requirement (완료 조건)
다음 중 하나 이상의 물리적 증거가 반드시 로그에 기록되어야 합니다.
1.  **Todo Cleared**: `pc_todo_manager`를 통해 할당된 모든 작업 항목이 `checked` 상태여야 합니다.
2.  **LSP/Diagnostic**: 수정된 파일에 문법 에러나 타입 에러가 없음이 확인됨.
3.  **Build Pass**: 프로젝트의 빌드 명령어가 성공(Exit Code 0)함.
4.  **Test Success**: 관련 유닛 테스트나 통합 테스트가 통과함.

## 3. 실패 회복 (Failure Recovery)
- 3회 연속 수정 실패(에러 미해결) 시, 작업을 강행하지 말고 즉시 중단합니다.
- 현재까지의 시도와 실패 원인을 정리하여 `pc_save_observation`에 기록한 후 사용자에게 조언을 구합니다.
- 필요 시 `git checkout` 등을 통해 마지막 정상 상태로 롤백합니다.

## 4. Anti-Patterns (금지 사항)
- "수정했습니다"라는 말만 하고 증거를 제시하지 않는 행위.
- 에러를 해결하기 위해 관련 테스트 코드를 삭제하는 행위.
- `as any`나 `@ts-ignore` 등을 남발하여 검증을 우회하는 행위.