---
trigger: model_decision
description: AI Slop Cleaner (v1.0)
---

# AI Slop Cleaner (v1.0)

> [!IMPORTANT]
> AI가 생성한 코드는 가끔 작동은 하지만 불필요한 추상화나 중복된 로직, 그리고 관리하기 힘든 'Slop(찌꺼기)'을 포함할 수 있습니다. 이를 방지하고 간결한 코드를 유지하기 위한 규칙입니다.

## 적용 시점 (When to Use)
- 사용자가 "데슬롭(deslop)", "리팩토링(refactor)", "정리(cleanup)"를 요청할 때.
- 코드에 중복 로직, 불필요한 래퍼(Wrapper), 사용되지 않는 변수/함수가 보일 때.
- AI가 이전 작업에서 부수적으로 생성한 불필요한 코드가 남아 있을 때.

## 핵심 강령 (Core Principles)
1. **Deletion First**: 추가보다 삭제를 선호합니다. 코드의 양이 줄어들면서 기능이 유지되는 것이 최고의 결과입니다.
2. **Regression Protection**: 코드를 삭제하거나 변경하기 전, 반드시 해당 구간의 동작을 검증하는 테스트 코드를 먼저 확보하십시오.
3. **Simplicity over Abstraction**: 과도한 추상화나 복잡한 디자인 패턴보다 읽기 쉽고 명확한 절차적 코드를 지향합니다.
4. **No Side-Effects**: 기능의 변경 없이 구조만 개선해야 합니다.

## 행동 지침 (Action Guide)
- **Step 1: Inspect**: 중복된 유틸리티, 데드 코드(Dead code), 경계가 모호한 모듈을 탐색합니다.
- **Step 2: Secure**: `pc_save_observation`을 통해 현재의 정상 동작 로그를 확보하거나 유닛 테스트를 실행합니다.
- **Step 3: Prune**: 과감하게 찌꺼기 코드를 제거하고 핵심 로직만 남깁니다.
- **Step 4: Verify**: 리팩토링 후에도 동일한 테스트 결과를 얻는지 확인합니다.
- **Step 5: Report**: 삭제한 코드 라인 수와 간소화된 구조를 명확히 보고합니다.