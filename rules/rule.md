---
trigger: model_decision
---

# Agent Core Rules (rule.md)

## [Efficiency & Automation Protocol]
- **스크립트 위임 원칙**: 3개 이상의 파일을 동시 수정하거나, 1,000줄 이상의 대량 코드를 처리/반복해야 할 경우, 에이전트가 직접 수행하지 않고 반드시 `.agents/scripts/` 하위에 파이썬(Python) 스크립트를 생성하여 작업을 위임하십시오.
- **1-Depth 구조 유지**: `.agents/` 하위의 폴더 구조를 복잡한 계층(Depth 2 이상)으로 만들지 말고, 가급적 평면적으로 유지하여 탐색 효율을 극대화하십시오.

## [Reporting & Documentation Priority]
- **최우선 참조 원칙**: 커밋 메시지, MR 요약, Jira 이슈 작성 등 모든 기록 활동 시 반드시 `.agents/protocols/reporting.md`를 1순위로 참조하십시오.
- **Zero Path Policy**: 보고서나 메시지 내에 절대적인 파일 경로(/절대/파일/경로/...)나 구체적인 파일명을 노출하지 말고, 비즈니스 논리(예: '사용자 프로필 수정 기능')로 치환하여 작성하십시오.
- **커밋 분리 원칙**: 대규모 작업을 하나의 커밋으로 묶지 말고, 각 Task 단위로 커밋을 세밀하게 분리하여 작업 이력의 가독성을 확보하십시오.