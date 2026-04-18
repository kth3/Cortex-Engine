---
description: "ADR(Architecture Decision Record) 작성을 트리거하고 관리하는 프로토콜입니다. 주요 기반/설계 변경 시 적용됩니다."
trigger: model_decision
---

# ADR 강제 생성 프로토콜 (ADR Generation Workflow)

에이전트는 프로젝트 내의 주요 기술적 결정 사항(예: 라이브러리 도입, 코드 리팩토링 로직 변경, 아키텍처 결정 등)이 발생했을 때, 이를 암묵지로 남기지 않고 즉시 ADR(Architecture Decision Record)로 문서화해야 합니다.

## 1. 생성 트리거
- **아키텍처 변경**: 새로운 패턴 적용, 폴더 구조 개편
- **의존성 추가**: 새로운 라이브러리, 프레임워크 훅 추가
- **주요 로직 변경**: 예외 처리 표준 등 공통/핵심 비즈니스 로직 변경 시

## 2. 절차
1. **상태 분석**: 현재 세션 또는 최근 작업에서 발생한 기술적 결정 사항 식별.
2. **ADR 생성**: 결정 사항을 `.agents/docs/adr/` 디렉토리에 생성 (또는 프로젝트 구조에 맞는 adr 디렉토리). 파일명은 `NNNN-short-description.md` 형식을 따름.
3. **연결 및 보고**: 생성 후 작업 내역(Walkthrough)에 명시.

> [!IMPORTANT]
> ADR 작성은 단순히 파일 생성의 목적이 아닌, "왜 이 기술을 선택했고 어떤 대안을 기각했는가"에 대한 컨텍스트 증류(Context Distillation) 과정입니다.