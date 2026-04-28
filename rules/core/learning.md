---
trigger: model_decision
description: Success Case Library (SCL) 기록 규칙
---

# Success Case Library (SCL) 기록 규칙

에이전트가 다른 에이전트의 능력 부족 및 한계를 보완하기 위한, '행동 유도형(Actionable)' 공통 지식 자구책입니다.
에이전트는 작업 중 난해한 버그를 해결하거나 범용적인 최적화 방법을 도출한 경우, 이 규칙에 따라 반드시 '지식 자산'으로 기록(Memory Write)해야 합니다.

## 1. 기록 판단 기준 (When to write)
단순한 코드 타이포/문법 오류 해결은 기록하지 않습니다.
- 코드 밖의 레이어(환경, 의존성, 캐시 등)가 원인인 오류를 파악했을 때
- 다른 에이전트 모델(예: Flash, pro, Sonnet, Opus 등)이 반복적으로 빠진 함정(Trap)을 분석하고 돌파구를 마련했을 때
- 여러 기술 스택에서 공통적으로 사용할 수 있는 보안 향상, 성능 최적화 방법론을 완성했을 때

## 2. '행동 유도형(Actionable)' 템플릿
memories.db에 `category: success_pattern`으로 기록 시, 다음 4가지 항목을 반드시 포함하여 마크다운 형태로 저장해야 합니다.

```markdown
1. **Symptom (증상)**: 사용자가 겪은 표면적 오류나 시스템이 뿜어내는 예외 메시지 (후행 에이전트의 검색 키워드 보장)
2. **Model's Trap (빠지기 쉬운 함정)**: 에이전트들이 흔히 착각하여 시간 낭비를 하는 지점 (예: "코드 오류인 줄 알고 계속 수정") 
3. **Breakthrough (핵심 통찰)**: 문제를 관통하는 진짜 원인과 돌파 논리
4. **Actionable Protocol (즉각 실행 지침)**: 후행 에이전트가 이 내용을 읽는 즉시 던져야 할 구체적 명령어, 스크립트, 수정 템플릿 등
```

## 3. 활용 규칙 (When to read)
- 에이전트는 작업 초기 구상이나 디버깅 시작 시, `pc_memory_search_knowledge`로 관련 핵심어와 함께 이 SCL 정보를 검색하여 이전 모델의 노하우를 먼저 확인해야 합니다.

---

# Anti-Pattern Library (APL) 기록 규칙

성공 패턴과 반대로, **커밋 히스토리에서 2회 이상 재발**이 확인된 금지 패턴을 기록합니다.

## 기록 판단 기준 (When to write)
- 동일한 버그/설계 실수가 커밋 기록에서 **2회 이상** 등장한 경우
- 수정했음에도 다음 세션에서 동일 패턴으로 재작성한 경우

## 'Anti-Pattern' 템플릿
memories.db에 `category: anti_pattern`으로 기록 시, 다음 항목을 반드시 포함하십시오.

```markdown
1. **금지 패턴 (Forbidden Pattern)**: 작성 금지 코드 스니펫
2. **재발 횟수**: 커밋 기록 기준 몇 회 확인됐는지
3. **올바른 패턴 (Correct Pattern)**: 대체 구현 방법
4. **영향 범위**: 이 패턴이 나타날 수 있는 파일/함수 목록
```

물리 백업 경로: `.agents/docs/anti_patterns/[key].md`