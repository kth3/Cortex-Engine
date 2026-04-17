---
trigger: model_decision
description: Cortex Architectural Integrity Rule (v1.0)
---

# Cortex Architectural Integrity Rule (v1.0)

> [!IMPORTANT]
> 이 규칙은 AI 에이전트의 "최소 저항 경로 본능"에 의한 시스템 비대화를 방지하기 위해 설계되었습니다.

## 1. 모노글롯(Monoglot) 유지 원칙
- **100% Python**: 모든 오케스트레이션 로직, 파서, 훅은 Python으로 작성합니다.
- **이유**: AI의 코드 수정 성공률(AI Ergonomics) 극대화, 의존성 지옥 및 IPC 병목 회피.

## 2. 모놀리식 하드코딩 금지 (Anti-Monolithic)
- **Core Separation**: `cortex_mcp.py` (엔진 진입점)와 `indexer.py` (인덱싱 흐름 제어)에는 직접적인 비즈니스 로직이나 언어별 상세 파싱 로직을 추가하지 않습니다.
- **Side-Effect Isolation**: 파일 수정 후의 동기화, 알림, 추가 검증 등 모든 사후 처리는 반드시 `hooks/` 폴더의 독립된 스크립트로 분리합니다.

## 3. 전략 패턴(Strategy Pattern) 기반 확장
- **Parser Registry**: 신규 언어 지원 시 `.agents/scripts/cortex/parsers/`에 `*_parser.py` 모듈을 추가하고 `SUPPORTED_EXTENSIONS`를 정의하십시오.
- **Dynamic Dispatch**: `HookManager`를 통해 런타임에 필요한 훅만 호출되도록 설계하여, 엔진의 복잡도를 낮게 유지하십시오.

## 4. 예방적 방어 (Preventive Defense)
- **Guard Hooks**: 위험한 도구(`pc_strict_replace` 등) 호출 전에는 `before_tool_call` 훅을 통해 파라미터의 정당성을 자율 검증해야 합니다.