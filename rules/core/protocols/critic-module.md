---
description: "에이전트가 지식 탐색에 실패하였거나 환각 발생 위험이 있을 경우 스스로를 교정(Self-Correction)하는 비평 루프 제어 지침입니다."
trigger: model_decision
---

# 자기 교정 및 비평 루프 장치 (Self-Correction & Critic Module)

단순 검색형(Naive) RAG 파이프라인의 한계인 단일 실패(Single Point of Failure)와 단편적 지식의 조합에서 오는 환각(Hallucination) 현상을 차단하기 위해, 에이전트 내부적으로 지속적인 **비평(Critic)** 절차를 수행해야 합니다.

## 1. 연쇄 호출 및 비평 알고리즘 (Iterative Search)
- **1차 검색 실패 대응**: MCP 도구(`pc_capsule`, `pc_memory_search_knowledge`)의 검색 결과가 부실하거나 현재 문제 해결에 불충분하다고 판단될 경우, 임의의 일반 상식(General LLM Knowledge)으로 답변하려 시도하지 마십시오.
- **추가 단서 수집**: 기존 검색어를 재가공하거나 `pc_impact_graph`, `pc_logic_flow`와 같은 심화 추론 도구를 사용해 관련 정보를 2차, 3차로 추적하여 연쇄 호출해야 합니다.

## 2. 그래프 탐색 우선 (Graph-RAG 추론)
- 단순히 텍스트만 찾는 방식에서 벗어나, Kùzu DB(관계형) 등의 구조를 적극 활용하십시오.
- A 모듈을 수정할 경우 영향 받는 B 컴포넌트를 확인할 때까지 스스로 비판적 점검(Self-Check) 루프를 돌고, 근거가 명확히 확보된 후에야 분석 리포트나 코드 변경으로 나아가야 합니다.

> [!WARNING]
> 이 비평 모듈은 "검색 결과가 없으니 모릅니다." 또는 "일반 지식을 바탕으로 유추하겠습니다" 따위의 답변을 철저히 금지하고, 끈질기게 도메인 내의 해답을 추적하게 하는 안전 장치입니다.
