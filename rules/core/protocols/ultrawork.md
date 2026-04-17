---
trigger: model_decision
description: Ultrawork Protocol (v1.0)
---

# Ultrawork Protocol (v1.0)

> [!IMPORTANT]
> 이 프로토콜은 단순 구현을 넘어, 상용 수준의 품질을 보장하기 위한 5단계 강제 워크플로우입니다. 복잡한 기능 구현이나 대규모 리팩토링 시 활성화하십시오.

## Phase 1: PLAN (기획 및 계약)
- **Step 1**: 요구사항 분석 및 엣지 케이스 식별.
- **Step 2**: API 규격 및 데이터 모델 정의.
- **Step 3**: `pc_create_contract`를 통해 명확한 작업 명세서 작성.
- **Step 4**: `pc_todo_manager`에 단계별 Task 등록.

## Phase 2: IMPL (구현)
- **Step 5**: 테스트 코드(Unit/Integration) 우선 작성.
- **Step 6**: `pc_strict_replace`를 사용하여 원자적(Atomic) 코드 수정.
- **Step 7**: 각 함수/클래스 작성 후 즉시 구문 검사 실행.

## Phase 3: VERIFY (검증)
- **Step 8**: 전체 테스트 스위트 실행 및 Pass 확인.
- **Step 9**: `pc_save_observation`을 통해 검증 결과(로그, 스크린샷 등) 기록.
- **Step 10**: 의도한 동작 외에 부수 효과(Side-effect)가 없는지 영향도 분석.

## Phase 4: REFINE (최적화)
- **Step 11**: `ai-slop-cleaner` 규칙을 적용하여 코드 간소화 및 중복 제거.
- **Step 12**: 독스트링(Docstring) 및 타입 힌트 보강.
- **Step 13**: 성능 병목 지점 점검 및 개선.

## Phase 5: SHIP (배포 및 지식화)
- **Step 14**: `pc_todo_manager`에서 모든 작업 완료(`checked`) 확인.
- **Step 15**: `pc_memory_write`를 통해 새로 발견된 패턴이나 결정 사항 영구 저장.
- **Step 16**: `protocols/reporting.md` 규격에 맞춘 최종 작업 보고.