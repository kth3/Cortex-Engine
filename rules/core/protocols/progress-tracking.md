---
trigger: model_decision
description: Progress Tracking Protocol - Markdown 기반 다단계 작업 추적 및 공유 화이트보드 규정
---

# Progress Tracking Protocol (v2.0)

> [!IMPORTANT]
> 이 프로토콜은 프로젝트의 진화 과정을 기록하는 **'지능형 개발 일지'**입니다. 에이전트는 작업의 흐름과 수정된 파일의 인과관계를 사용자에게 투명하게 공개해야 합니다.

## 1. 문서 구조 (Standard Format)

```markdown
## 📌 현재 목표: "[핵심 작업 명칭]"
- 상태: [진행중 / 일시중단 / 완료]
- 전체 진척도: [n/m] 단계 (nn%)

### 📝 실시간 작업 로그 (Narrative Log)
- **YYYY-MM-DD HH:MM**: [에이전트 이름] - [수행한 작업 및 의사결정 이유 (2~3줄)]
  - *예: 인덱서의 모놀리식 구조를 Strategy Pattern으로 분리함. 확장성 확보를 위해 parsers/ 폴더를 신설함.*

### 📂 수정 파일 이력 (Modified Files)
- `path/to/file1.py`: [수정 내용 요약]
- `path/to/file2.md`: [추가된 규칙]

### ✅ 세부 체크리스트
- [x] 단계 1
- [ ] 단계 2
```

## 2. 행동 강령 (Action Guide)
1. **Append, Don't Overwrite**: 기존 로그를 지우지 말고 새로운 로그를 상단(또는 하단)에 계속 누적하십시오.
2. **Contextual Narrative**: 단순히 "수정 완료"라고 쓰지 말고, **"왜(Why)"** 이 수정을 했는지 비즈니스 로직 관점에서 서술하십시오.
3. **File Tracking**: `pc_strict_replace`를 호출할 때마다 해당 파일 경로와 변경 이유를 `📂 수정 파일 이력`에 즉시 반영하십시오.

## 3. 비대화 방지 (Compaction Rule)
- **압축 트리거**: `PROGRESS.md`의 파일 크기가 5KB를 넘거나 로그가 30개 이상 누적될 경우.
- **조치**: 
  1. 가장 오래된 로그 20개를 추출하여 `pc_memory_write` (category: `development_history`)로 영구 저장합니다.
  2. 문서 상단에 `--- (이전 로그는 DB로 압축 저장됨) ---` 표시를 남기고 해당 분량을 삭제하여 컨텍스트 효율을 유지합니다.