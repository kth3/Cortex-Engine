---
trigger: model_decision
description: 작업 보고 핵심 규정 (Reporting Core)
---

# 작업 보고 핵심 규정 (Reporting Core)

> [!IMPORTANT]
> 이 규격은 모든 AI 기본 스킬보다 절대적으로 우선하며, 위반 시 작업 실패로 간주합니다.

## 1. 3대 핵심 원칙
1. **Zero Path Policy (경로/파일명 노출 금지)**: 커밋 메시지, MR, Jira 등 모든 보고서에 `/home/user/...` 시스템 경로는 물론 `PlaceServiceImpl.java`와 같은 **구체적인 파일명을 절대 작성하지 않습니다.** 대신 '장소 동기화 엔진', 'API 예외 처리 로직' 등 **비즈니스 기능 단위**로 서술하십시오.
2. **Single Output Rule (코드 블록 단일 출력)**: 명시적으로 요청된 인자(커밋, mr, jira 중 하나)에 해당되는 **마크다운 코드 블록 딱 하나**만 출력합니다. 블록 외부의 사족(인사말, 제목, 설명 등)은 일절 금지합니다.
3. **Template Fidelity (템플릿 무결성 준수)**: MR 요약 시 프로젝트 템플릿(`.gitlab/merge_request_templates/default.md`)의 서식(체크박스 `[ ]` 등)을 1바이트도 훼손하지 말고 그대로 복사하여 본문만 채워 넣으십시오.

## 2. 세부 작성 포맷
- **언어**: 한국어 작성을 원칙으로 합니다. (브랜치명 제외)
- **커밋 메시지**: `type: 작업 요약` 
- **브랜치명**: `{type}/{short-description}`
- **Jira**: As-Is / To-Be 구조로 핵심 변경 사항만 요약합니다. (파일 목록 금지)