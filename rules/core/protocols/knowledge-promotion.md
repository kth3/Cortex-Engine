---
trigger: model_decision
description: "추출된 메모리 지식을 물리적 파일(마크다운 형식) 기반의 영구적 규칙으로 승격 및 아카이빙하는 프로토콜입니다."
---

# 지식 승격 및 마크다운 자동화 지침 (Knowledge Promotion Auto-Sync)

에이전트가 단발성 작업 내용이나 국소적 패턴을 파악하여 이를 프로젝트 통용 규칙으로 인식했을 경우, 단순 DB 저장에 그치지 않고 별도의 아카이브 문서로 승격(Promotion)해야 합니다.

## 1. 승격 카테고리 매핑 로직
에이전트가 `pc_memory_write` 도구를 사용할 때 다음 기준을 따릅니다:
1. **Decision & Architecture**: Category가 `decision` 또는 `architecture`인 지식은 `decisions.md` (예: `/history/decisions.md` 등 프로젝트별 아카이브 경로) 파일 내부에 갱신 기록을 추가합니다.
2. **Rule & Pattern**: Category가 `pattern`, `convention`, `rule`, `protocol` 인 경우, `patterns.md` 아카이브 파일 내부에 갱신 기록을 남깁니다.

## 2. 반영 원칙
- **DB와 File의 이중 관리**: DB의 검색 용이성과 File의 영속성을 모두 확보하기 위한 행위입니다.
- **수동 개입/직접 수정**: 에이전트가 `pc_memory_write`로 DB에 등록함과 동시에, 연관된 `patterns.md`나 `decisions.md` 마크다운 파일에 직접 내용을 Appendix 형식으로 Append(추가)해야 합니다.

> [!TIP]
> 이 규칙은 에이전트의 깨달음(Insight)이 그래프 DB 영역 안에만 갇혀버려, 휴먼 개발자(User)가 읽을 수 없는 "에이전트만의 고립된 두뇌"가 되는 것을 방지하기 위한 핵심 연동 장치입니다.